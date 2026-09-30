"""
Scroll mode and the sidebar: page rows, thumbnails, the outline, activities.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk

from interaktiv_gtk.reader.scroll_mode import (
    FAR,
    NEARBY,
    ON_SCREEN,
    PAGE_GAP,
    PageRowWrapper,
    ScrollModeView,
)
from interaktiv_gtk.reader.sidebar import (
    BookmarksPanel,
    ReaderSidebar,
    ThumbnailsPanel,
    build_toc_tree,
)
from interaktiv_gtk.reader import scroll_mode
from interaktiv_gtk.render.cache import TextureCache
from interaktiv_gtk.render.requests import DocumentInfo


class DummyTexture:
    """Mock Gdk.Texture for testing texture caching."""
    def __init__(self, width=100, height=100):
        self.width = width
        self.height = height


class TestScrollModeVirtualization(unittest.TestCase):
    """Gtk.ListView virtualization in ScrollModeView."""

    def setUp(self):
        self.info = DocumentInfo(
            path="dummy.pdf",
            page_count=289,
            page_sizes=tuple((595.0, 842.0) for _ in range(289)),
        )
        self.budget = 10 * 1024 * 1024  # 10 MB budget
        self.cache = TextureCache(budget=self.budget)
        self.view = ScrollModeView(cache=self.cache)

    def tearDown(self):
        self.view.shutdown()

    def test_model_population_matches_page_count(self):
        self.view.attach(service=None, info=self.info)
        self.assertEqual(self.view._store.get_n_items(), 289)
        item0 = self.view._store.get_item(0)
        self.assertEqual(item0.page, 1)
        self.assertEqual(item0.width, 595.0)
        self.assertEqual(item0.height, 842.0)
        item288 = self.view._store.get_item(288)
        self.assertEqual(item288.page, 289)

    def test_unbind_drops_texture(self):
        """When an off-screen page is unbound, unbind drops its texture."""
        self.view.attach(service=None, info=self.info)

        class DummyListItem:
            def __init__(self, item):
                self._item = item
                self._child = None
                self.view = None
                self.wrapper = None

            def get_item(self):
                return self._item

            def set_child(self, child):
                self._child = child

            def get_child(self):
                return self._child

        list_item = DummyListItem(self.view._store.get_item(5))
        self.view._on_setup(None, list_item)
        self.view._on_bind(None, list_item)

        # Simulate texture arrival
        tex = DummyTexture(500, 700)
        list_item.view.set_texture(tex, 6, 0)
        self.assertTrue(list_item.view.has_texture)

        # Unbind must clear the texture
        self.view._on_unbind(None, list_item)
        self.assertFalse(list_item.view.has_texture)
        self.assertNotIn(6, self.view._bound_views)

    def test_scroll_end_to_end_texture_cache_stays_at_budget(self):
        """Simulating rendering through all 289 pages keeps TextureCache within budget."""
        self.view.attach(service=None, info=self.info)

        # Each page rendered is ~1 MB
        bytes_per_page = 1024 * 1024
        for page in range(1, 290):
            key = (page, 1.0, 0, None)
            tex = DummyTexture()
            self.cache.put(key, tex, bytes_per_page)

        # Total cached bytes must never exceed the budget (plus the single latest entry)
        self.assertLessEqual(self.cache.nbytes, self.budget + bytes_per_page)
        # Verify LRU eviction occurred: earlier pages were evicted
        self.assertIsNone(self.cache.get((1, 1.0, 0, None)))
        # Latest page is still in cache
        self.assertIsNotNone(self.cache.get((289, 1.0, 0, None)))

    def test_page_row_wrapper_measure_includes_gap(self):
        from interaktiv_gtk.reader.page_view import PageView
        pv = PageView()
        wrapper = PageRowWrapper(pv)
        wrapper.set_target_size(300, 500)

        min_w, nat_w, _, _ = wrapper.do_measure(Gtk.Orientation.HORIZONTAL, -1)
        self.assertEqual(nat_w, 300)

        min_h, nat_h, _, _ = wrapper.do_measure(Gtk.Orientation.VERTICAL, -1)
        self.assertEqual(nat_h, 500 + PAGE_GAP)


class TestScrollModeRenderBand(unittest.TestCase):
    """
    `Gtk.ListView` keeps ~200 rows bound, so which pages are rendered is decided
    from where a row is, not from whether it is bound.
    """

    VIEWPORT = 800.0

    def test_a_row_in_the_viewport_is_on_screen(self):
        self.assertEqual(scroll_mode.band(20, 780, self.VIEWPORT), ON_SCREEN)
        # Only its last pixels showing still counts.
        self.assertEqual(scroll_mode.band(-700, 10, self.VIEWPORT), ON_SCREEN)
        self.assertEqual(scroll_mode.band(790, 1500, self.VIEWPORT), ON_SCREEN)

    def test_a_row_just_outside_is_kept_warm(self):
        self.assertEqual(scroll_mode.band(820, 1600, self.VIEWPORT), NEARBY)
        self.assertEqual(scroll_mode.band(-800, -20, self.VIEWPORT), NEARBY)

    def test_a_row_far_away_gives_its_texture_back(self):
        reach = self.VIEWPORT * scroll_mode.PRELOAD
        self.assertEqual(
            scroll_mode.band(self.VIEWPORT + reach + 1, 5000, self.VIEWPORT), FAR
        )
        self.assertEqual(scroll_mode.band(-5000, -reach - 1, self.VIEWPORT), FAR)

    def test_overlap_is_the_visible_part_of_a_row(self):
        self.assertEqual(scroll_mode.overlap(20, 780, self.VIEWPORT), 760)
        self.assertEqual(scroll_mode.overlap(-100, 300, self.VIEWPORT), 300)
        self.assertEqual(scroll_mode.overlap(700, 1500, self.VIEWPORT), 100)
        self.assertEqual(scroll_mode.overlap(900, 1700, self.VIEWPORT), 0)

    def test_binding_a_row_does_not_render_it(self):
        info = DocumentInfo(
            path="dummy.pdf", page_count=20,
            page_sizes=tuple((595.0, 842.0) for _ in range(20)),
        )
        service = MagicMock()
        service.generation = 1
        view = ScrollModeView(cache=TextureCache(budget=1024))
        view.attach(service=service, info=info)

        list_item = MagicMock()
        list_item.get_item.return_value = view._store.get_item(3)
        real = {}
        list_item.set_child.side_effect = lambda child: real.setdefault("child", child)
        view._on_setup(None, list_item)
        view._on_bind(None, list_item)

        self.assertIn(4, view._bound_views)
        service.submit.assert_not_called()
        view.shutdown()

    def test_fit_zoom_uses_the_books_common_page_size(self):
        """A double-width cover must not set the zoom for the whole book."""
        sizes = [(1106.0, 780.0)] + [(553.0, 780.0)] * 9
        info = DocumentInfo(path="dummy.pdf", page_count=10, page_sizes=tuple(sizes))
        view = ScrollModeView(cache=TextureCache(budget=1024))
        view.attach(service=None, info=info)
        self.assertEqual(view._reference_size(), (553, 780))
        view.set_rotation(90)
        self.assertEqual(view._reference_size(), (780, 553))
        view.shutdown()


class TestThumbnailRequests(unittest.TestCase):
    """Thumbnails are rendered for rows on screen, not for every bound row."""

    def test_a_closed_sidebar_requests_nothing(self):
        service = MagicMock()
        service.generation = 1
        panel = ThumbnailsPanel()
        panel.load(page_count=50, mode="single", rotation=0, service=service,
                   page_sizes=[(595.0, 842.0)] * 50)
        # Never mapped: the panel is not in a window.
        panel._request_visible()
        service.submit.assert_not_called()
        panel.shutdown()

    def test_the_thumbnail_cache_is_bounded(self):
        from interaktiv_gtk.reader.sidebar import THUMB_CACHE_MAX
        from interaktiv_gtk.render.service import LANE_THUMBNAIL

        panel = ThumbnailsPanel()
        panel.load(page_count=400, mode="single", rotation=0)
        for page in range(1, THUMB_CACHE_MAX + 40):
            result = MagicMock()
            result.request.lane = LANE_THUMBNAIL
            result.request.rotation = 0
            result.request.page = page
            panel.on_thumbnail_result(result)
        self.assertEqual(len(panel._thumb_cache), THUMB_CACHE_MAX)
        self.assertNotIn(1, panel._thumb_cache)
        panel.shutdown()


class TestThumbnailGrouping(unittest.TestCase):
    """Spread grouping in book mode vs single grouping in single/scroll mode."""

    def test_book_mode_spread_grouping_odd_pages(self):
        panel = ThumbnailsPanel()
        panel.load(page_count=289, mode="book", rotation=0)

        # Total spreads: [1] is cover, then 2..289 (288 pages = 144 pairs). Total items: 145
        self.assertEqual(panel.store.get_n_items(), 145)

        first = panel.store.get_item(0)
        self.assertEqual(first.pages, (1,))
        self.assertEqual(first.label, "Sayfa 1")

        second = panel.store.get_item(1)
        self.assertEqual(second.pages, (2, 3))
        self.assertEqual(second.label, "Sayfa 2–3")

        last = panel.store.get_item(144)
        self.assertEqual(last.pages, (288, 289))
        self.assertEqual(last.label, "Sayfa 288–289")

    def test_book_mode_spread_grouping_even_pages(self):
        panel = ThumbnailsPanel()
        panel.load(page_count=10, mode="book", rotation=0)

        # Spreads: [1], [2, 3], [4, 5], [6, 7], [8, 9], [10]
        self.assertEqual(panel.store.get_n_items(), 6)
        self.assertEqual(panel.store.get_item(0).pages, (1,))
        self.assertEqual(panel.store.get_item(1).pages, (2, 3))
        self.assertEqual(panel.store.get_item(4).pages, (8, 9))
        self.assertEqual(panel.store.get_item(5).pages, (10,))
        self.assertEqual(panel.store.get_item(5).label, "Sayfa 10")

    def test_single_and_scroll_mode_grouping(self):
        panel = ThumbnailsPanel()
        for mode in ("single", "scroll"):
            with self.subTest(mode=mode):
                panel.load(page_count=50, mode=mode, rotation=0)
                self.assertEqual(panel.store.get_n_items(), 50)
                self.assertEqual(panel.store.get_item(0).pages, (1,))
                self.assertEqual(panel.store.get_item(0).label, "Sayfa 1")
                self.assertEqual(panel.store.get_item(49).pages, (50,))
                self.assertEqual(panel.store.get_item(49).label, "Sayfa 50")

    def test_active_page_selection_matching(self):
        panel = ThumbnailsPanel()
        panel.load(page_count=289, mode="book", rotation=0)

        # In book mode, page 1 selects item 0
        panel.update_active_page(1)
        self.assertEqual(panel.selection.get_selected(), 0)

        # Page 2 or 3 selects item 1
        panel.update_active_page(2)
        self.assertEqual(panel.selection.get_selected(), 1)
        panel.update_active_page(3)
        self.assertEqual(panel.selection.get_selected(), 1)

        # Page 289 selects item 144
        panel.update_active_page(289)
        self.assertEqual(panel.selection.get_selected(), 144)


class TestBookmarksTOC(unittest.TestCase):
    """Table of contents tree building and BookmarksPanel."""

    def test_build_toc_tree_hierarchy(self):
        raw_toc = [
            [1, "Bölüm 1", 1],
            [2, "Konu 1.1", 5],
            [3, "Detay 1.1.1", 8],
            [2, "Konu 1.2", 12],
            [1, "Bölüm 2", 20],
        ]
        root = build_toc_tree(raw_toc)
        self.assertEqual(root.get_n_items(), 2)

        ch1 = root.get_item(0)
        self.assertEqual(ch1.title, "Bölüm 1")
        self.assertEqual(ch1.page, 1)
        self.assertEqual(ch1.children.get_n_items(), 2)

        sec11 = ch1.children.get_item(0)
        self.assertEqual(sec11.title, "Konu 1.1")
        self.assertEqual(sec11.page, 5)
        self.assertEqual(sec11.children.get_n_items(), 1)

        sub111 = sec11.children.get_item(0)
        self.assertEqual(sub111.title, "Detay 1.1.1")
        self.assertEqual(sub111.page, 8)
        self.assertEqual(sub111.children.get_n_items(), 0)

        ch2 = root.get_item(1)
        self.assertEqual(ch2.title, "Bölüm 2")
        self.assertEqual(ch2.page, 20)
        self.assertEqual(ch2.children.get_n_items(), 0)

    def test_bookmarks_panel_empty(self):
        panel = BookmarksPanel()
        panel.load([])
        self.assertEqual(panel.stack.get_visible_child_name(), "empty")

    def test_bookmarks_panel_populated(self):
        panel = BookmarksPanel()
        raw_toc = [[1, "Giriş", 1], [1, "Sonuç", 100]]
        panel.load(raw_toc)
        self.assertEqual(panel.stack.get_visible_child_name(), "list")


class TestReaderSidebar(unittest.TestCase):
    """ReaderSidebar combining thumbnails, bookmarks, and activities."""

    def test_sidebar_contains_three_tabs(self):
        sidebar = ReaderSidebar()
        panels = (sidebar.thumbnails, sidebar.bookmarks, sidebar.activities)
        names = [sidebar.view_stack.get_page(panel).get_name() for panel in panels]
        self.assertEqual(names, ["thumbnails", "bookmarks", "activities"])

    def test_page_chosen_signal_forwarded(self):
        sidebar = ReaderSidebar()
        chosen = []
        sidebar.connect("page-chosen", lambda _w, p: chosen.append(p))

        sidebar.thumbnails.emit("page-chosen", 42)
        self.assertEqual(chosen, [42])

        sidebar.bookmarks.emit("page-chosen", 88)
        self.assertEqual(chosen, [42, 88])


class TestReaderPageScrollIntegration(unittest.TestCase):
    """Integration of scroll mode in ReaderPage."""

    def test_the_dock_offers_scroll_mode(self):
        from interaktiv_gtk.reader.dock import MODES
        labels = {name: label for name, label, _icon in MODES}
        self.assertEqual(labels["scroll"], "Kaydırma")

    def test_shortcuts_include_scroll_and_sidebar(self):
        from interaktiv_gtk.reader.view import SHORTCUTS
        shortcut_dict = dict(SHORTCUTS)
        self.assertIn("c|C", shortcut_dict)
        self.assertEqual(shortcut_dict["c|C"], "mode-scroll")
        self.assertIn("F9|t|T", shortcut_dict)
        self.assertEqual(shortcut_dict["F9|t|T"], "toggle-sidebar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
