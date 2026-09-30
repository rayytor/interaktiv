#!/usr/bin/env python3
"""
Drawing on the book, and zooming like a phone.

Nothing here opens a window. The pinch is tested through its arithmetic --
where the printed point under the fingers goes -- and the D-Bus service
through the answers it gives for windows it does and does not own.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from interaktiv_gtk import touch  # noqa: E402,F401  (pins the GTK versions)
from interaktiv_core.geometry import PageTransform  # noqa: E402
from interaktiv_core.ink import BookInk, InkStroke  # noqa: E402
from interaktiv_gtk import ink_service  # noqa: E402
from interaktiv_gtk.reader import ink_layer, pinch  # noqa: E402

from gi.repository import Graphene, Gsk, Gtk  # noqa: E402


class FakeView:
    """A page laid out at (left, top) in the scroller, at `scale` px per pt."""

    def __init__(self, page, left, top, scale, size=(500.0, 700.0)):
        self.page = page
        self.left, self.top = left, top
        self.scale = scale
        self.size = size

    def transform(self):
        return PageTransform.for_full_page(*self.size, self.scale)

    def compute_bounds(self, _scroller):
        w, h = self.size[0] * self.scale, self.size[1] * self.scale
        return True, Graphene.Rect().init(self.left, self.top, w, h)


class TestPinchAnchor(unittest.TestCase):
    def test_the_point_under_the_fingers_stays_there(self):
        view = FakeView(1, 40, 20, 1.0)
        anchor = pinch.anchor_at([view], None, 240, 320)
        # The page is laid out again at twice the scale; nothing scrolled yet.
        view.scale = 2.0
        dx, dy = pinch.anchor_offset([view], None, anchor, 240, 320)
        # Scrolling by the offset brings the same print back under (240, 320).
        self.assertAlmostEqual(dx, 200.0)
        self.assertAlmostEqual(dy, 300.0)

    def test_moving_both_fingers_pans(self):
        view = FakeView(1, 0, 0, 1.0)
        anchor = pinch.anchor_at([view], None, 100, 100)
        dx, dy = pinch.anchor_offset([view], None, anchor, 160, 130)
        self.assertAlmostEqual(dx, -60.0)
        self.assertAlmostEqual(dy, -30.0)

    def test_fingers_in_the_margin_hold_the_nearest_page(self):
        left = FakeView(1, 0, 0, 1.0)
        right = FakeView(2, 520, 0, 1.0)
        page, _point = pinch.anchor_at([left, right], None, 515, 50)
        self.assertEqual(page, 2)

    def test_scroll_by_stays_in_range(self):
        scroller = Gtk.ScrolledWindow()
        adj = scroller.get_hadjustment()
        adj.configure(50, 0, 1000, 1, 10, 200)
        pinch.scroll_by(scroller, 5000, 0)
        self.assertEqual(adj.get_value(), 800)
        pinch.scroll_by(scroller, -5000, 0)
        self.assertEqual(adj.get_value(), 0)

    def test_a_pinch_is_live_until_it_ends(self):
        calls = []
        zoom = [1.0]

        def set_zoom(z, live):
            zoom[0] = z
            calls.append(live)

        settled = []
        view = FakeView(1, 0, 0, 1.0)
        scroller = MagicMock()
        p = pinch.PinchZoom(lambda: scroller, lambda: [view], lambda: zoom[0],
                            set_zoom, lambda: settled.append(True))
        p.begin(10, 10)
        p.update(10, 10, 1.5)
        self.assertEqual(calls, [True])
        self.assertAlmostEqual(zoom[0], 1.5)
        p.end()
        self.assertEqual(settled, [True])
        self.assertFalse(p.active)


class TestInkLayer(unittest.TestCase):
    def test_colour_with_alpha(self):
        c = ink_layer.parse_rgba("#ff000080")
        self.assertAlmostEqual(c.red, 1.0)
        self.assertAlmostEqual(c.alpha, 128 / 255)
        self.assertAlmostEqual(ink_layer.parse_rgba("garbage").alpha, 1.0)

    def test_every_kind_builds_a_node(self):
        strokes = [
            InkStroke(page=1, kind=k, rgba="#123456ff", width=2,
                      points=[10, 10, 50, 60, 90, 20])
            for k in ("pen", "marker", "line", "arrow", "rect", "ellipse")
        ]
        node = ink_layer.build_node(strokes, 100)
        # PyGObject 3.42 (the oldest boards) cannot return a container node;
        # then the strokes are drawn directly, which must work everywhere.
        if ink_layer.NODES_SUPPORTED:
            self.assertIsInstance(node, Gsk.RenderNode)
        else:
            self.assertIsNone(node)
        self.assertIsNone(ink_layer.build_node([], 100))
        snap = Gtk.Snapshot()
        t = PageTransform.for_full_page(100, 100, 2.0, 90)
        ink_layer.append_page_ink(snap, None, strokes, 100, t,
                                  Graphene.Rect().init(0, 0, 200, 200))

    def test_page_transform_matches_geometry(self):
        # The GSK transform must put a point where PageTransform does.
        for rotation in (0, 90, 180, 270):
            t = PageTransform.for_full_page(200, 300, 1.5, rotation)
            gsk = ink_layer.page_to_widget(t)
            u, v = 40.0, 70.0          # page space, y down
            out = gsk.transform_point(Graphene.Point().init(u, v))
            expected = t.point_to_widget(u, 300 - v)
            self.assertAlmostEqual(out.x, expected[0], places=3, msg=rotation)
            self.assertAlmostEqual(out.y, expected[1], places=3, msg=rotation)


class TestDeepZoomDetail(unittest.TestCase):
    def test_only_past_the_render_cap(self):
        from interaktiv_gtk.reader import detail
        self.assertFalse(detail.needs_detail(595, 842, 2.0))
        self.assertTrue(detail.needs_detail(595, 842, 10.0))

    def test_clip_is_the_visible_part_padded_and_snapped(self):
        from interaktiv_gtk.reader import detail
        # A 500 x 700 pt page at 4 px/pt, scrolled so that (1000, 800) px of
        # it is at the scroller's top-left, in a 400 x 300 px viewport.
        view = FakeView(1, -1000, -800, 4.0)
        scroller = MagicMock()
        scroller.get_width.return_value = 400
        scroller.get_height.return_value = 300
        x0, y0, x1, y1 = detail.visible_clip(view, scroller)
        # Visible: x 250..350 pt, y (from the top) 200..275 pt -> PDF y 425..500.
        self.assertLessEqual(x0, 250 - 0.15 * 100)
        self.assertGreaterEqual(x1, 350 + 0.15 * 100)
        self.assertLessEqual(y0, 425)
        self.assertGreaterEqual(y1, 500)
        self.assertEqual(x0 % detail.GRID, 0)
        self.assertLess(x1 - x0, 200)

    def test_off_screen_page_has_no_clip(self):
        from interaktiv_gtk.reader import detail
        view = FakeView(1, 5000, 5000, 1.0)
        scroller = MagicMock()
        scroller.get_width.return_value = 400
        scroller.get_height.return_value = 300
        self.assertIsNone(detail.visible_clip(view, scroller))


class TestInkService(unittest.TestCase):
    def _service(self, xid=42, reader=None):
        window = MagicMock()
        window.get_surface.return_value.get_scale_factor.return_value = 2
        window.get_surface_transform.return_value = (10.0, 20.0)
        service = ink_service.InkService(lambda: window, lambda: reader)
        patcher = patch.object(ink_service, "window_xid", return_value=xid)
        patcher.start()
        self.addCleanup(patcher.stop)
        return service

    def test_points_lose_the_scale_and_the_shadow(self):
        reader = MagicMock()
        reader.ink_controller.add_stroke.return_value = "abc"
        service = self._service(reader=reader)
        result = service.handle(
            "AddStroke", (42, "pen", "#000000ff", 6.0, [(120.0, 240.0), (140.0, 260.0)])
        )
        self.assertEqual(result, ("abc",))
        kind, rgba, width, points = reader.ink_controller.add_stroke.call_args[0]
        self.assertEqual(width, 3.0)
        self.assertEqual(points, [(50.0, 100.0), (60.0, 110.0)])

    def test_another_window_is_refused(self):
        reader = MagicMock()
        service = self._service(xid=42, reader=reader)
        self.assertEqual(service.handle("AddStroke", (7, "pen", "#000", 2.0, [(1.0, 1.0)])), ("",))
        self.assertEqual(service.handle("Accepts", (7, 1.0, 1.0)), (False,))
        reader.ink_controller.add_stroke.assert_not_called()

    def test_no_book_open_refuses(self):
        service = self._service(reader=None)
        self.assertEqual(service.handle("AddStroke", (42, "pen", "#000", 2.0, [(1.0, 1.0)])), ("",))
        self.assertEqual(service.handle("RemoveStrokes", (["a"],)), (0,))

    def test_gesture_phases(self):
        reader = MagicMock()
        reader.gesture_at_window.return_value = True
        service = self._service(reader=reader)
        self.assertEqual(service.handle("Gesture", (42, "update", 100.0, 100.0, 1.2)), (True,))
        reader.gesture_at_window.assert_called_with("update", 40.0, 30.0, 1.2)
        self.assertEqual(service.handle("Gesture", (42, "spin", 1.0, 1.0, 1.0)), (False,))

    def test_introspection_parses(self):
        from gi.repository import Gio
        info = Gio.DBusNodeInfo.new_for_xml(ink_service.INTROSPECTION)
        names = {m.name for m in info.interfaces[0].methods}
        self.assertLessEqual(
            {"AddStroke", "RemoveStrokes", "RestoreStrokes", "Erase", "UndoErase",
             "RedoErase", "ClearPage", "Gesture", "Accepts"}, names
        )


class TestReaderInkWiring(unittest.TestCase):
    def _page(self, tmp):
        from interaktiv_gtk.reader.view import ReaderPage
        item = MagicMock()
        item.id = "test-book"
        item.title = "Test Book"
        app = MagicMock()
        app.settings.get.return_value = None
        with patch("interaktiv_gtk.reader.view.DocumentSession") as session_cls, \
             patch("interaktiv_gtk.reader.view.ReaderPage._install_shortcuts"), \
             patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
            session = MagicMock()
            session.is_open = True
            session.page_count = 10
            session_cls.return_value = session
            return ReaderPage(app, item, "/fake/path.pdf")

    def test_the_book_ink_reaches_every_canvas(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            page = self._page(tmp)
            self.assertIsInstance(page.ink, BookInk)
            self.assertIs(page.spread._ink, page.ink)
            self.assertIs(page.scroll_view._ink, page.ink)
            self.assertIs(page.focus_overlay.page_view._ink, page.ink)

    def test_clearing_is_undoable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            page = self._page(tmp)
            stroke = InkStroke(page=3, kind="pen", rgba="#000000ff", width=1, points=[1, 1, 2, 2])
            page.ink.add(stroke)
            token = page.ink_controller.clear_pages([3])
            self.assertEqual(len(page.ink), 0)
            page.ink_controller.undo_erase(token)
            self.assertEqual([s.id for s in page.ink.strokes_on(3)], [stroke.id])
            self.assertIsNone(page.ink_controller.clear_pages([4]))

    def test_edge_strips_hide_when_the_page_is_wider_than_the_screen(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            page = self._page(tmp)
            adj = page.scroller.get_hadjustment()
            adj.configure(0, 0, 3000, 1, 10, 1000)
            page._update_page_edges()
            self.assertFalse(page.btn_next.get_visible())
            adj.configure(0, 0, 1000, 1, 10, 1000)
            page._update_page_edges()
            self.assertTrue(page.btn_next.get_visible())


if __name__ == "__main__":
    unittest.main()
