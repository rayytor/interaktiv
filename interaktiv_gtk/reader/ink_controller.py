"""
The reader's end of drawing on the book.

Rayyanpen, the overlay pen, finishes a stroke over this window and hands it
here (see `interaktiv_gtk/ink_service.py` for the D-Bus side). Everything in
this module takes points in the window's own logical coordinates -- those of
the `Gtk.Window` at the root of the reader -- and turns them into ink on a
page, in PDF user space, through the same `PageTransform` the activity marks
use. A stroke that starts anywhere but on a page (the dock, the header, the
sidebar, a margin) is refused and stays on Rayyanpen's overlay.
"""

from collections import OrderedDict
from typing import List, Optional, Sequence, Tuple

from gi.repository import GLib, Gtk

from interaktiv_core.ink import (
    FREEHAND,
    KINDS,
    SHAPES,
    BookInk,
    EraseSession,
    InkStroke,
    simplify,
)

from .page_view import PageView

SAVE_DELAY_MS = 1000
# Freehand points closer than this to the line through their neighbours, in
# screen pixels at the zoom the stroke was drawn at, are dropped.
SIMPLIFY_PX = 0.35
# Undo history kept for strokes Rayyanpen took back and for eraser sessions.
HISTORY = 200
# Joins the ids of one stroke's pieces on different pages.
ID_SEPARATOR = "+"

Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]  # x, y, width, height

# Widgets that take a tap. Where one lies over a page, the tap is its.
CONTROL_TYPES = (
    Gtk.Button, Gtk.MenuButton, Gtk.Scale, Gtk.Editable, Gtk.Switch,
    Gtk.ListBox, Gtk.ListView, Gtk.GridView, Gtk.DropDown,
)


def _intersect(a: Rect, b: Rect) -> Optional[Rect]:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def _bounded_put(store: "OrderedDict", key, value) -> None:
    store[key] = value
    store.move_to_end(key)
    while len(store) > HISTORY:
        store.popitem(last=False)


class InkController:
    def __init__(self, reader, ink: BookInk) -> None:
        self.reader = reader
        self.ink = ink
        self._removed: "OrderedDict[str, InkStroke]" = OrderedDict()
        self._sessions: "OrderedDict[str, EraseSession]" = OrderedDict()
        self._save_source = 0

    # -- where on the book -------------------------------------------------

    def _root(self) -> Optional[Gtk.Widget]:
        return self.reader.get_root()

    def _views(self) -> List[PageView]:
        focus = getattr(self.reader, "focus_overlay", None)
        if focus is not None and focus.is_active:
            return [focus.page_view]
        return self.reader.visible_page_views()

    def _bounds(self, view: PageView):
        root = self._root()
        if root is None:
            return None
        ok, b = view.compute_bounds(root)
        if not ok or b.get_width() <= 0 or b.get_height() <= 0:
            return None
        return (b.get_x(), b.get_y(), b.get_width(), b.get_height())

    def view_at(self, x: float, y: float) -> Optional[PageView]:
        """
        The page under a window point, if nothing else is on top of it there.

        Picking rather than hit-testing geometry is what keeps a stroke drawn
        over the dock, the sidebar or a popover off the page underneath. The
        page-turn strips are the one exception: they are invisible and lie
        over the sheet, so a stroke begun on them is on the page.
        """
        root = self._root()
        if root is None:
            return None
        widget = root.pick(x, y, Gtk.PickFlags.DEFAULT)
        edges = [getattr(self.reader, n, None) for n in ("btn_prev", "btn_next")]
        while widget is not None:
            if isinstance(widget, PageView):
                return widget if widget in self._views() else None
            if any(widget is e for e in edges if e is not None):
                return self._geometric(x, y)
            widget = widget.get_parent()
        return None

    def _geometric(self, x: float, y: float) -> Optional[PageView]:
        for view in self._views():
            b = self._bounds(view)
            if b and b[0] <= x <= b[0] + b[2] and b[1] <= y <= b[1] + b[3]:
                return view
        return None

    # -- what Rayyanpen may draw over ----------------------------------------

    def surfaces(self) -> Tuple[List[Rect], List[Rect]]:
        """
        The pages on screen and the controls lying over them, as rectangles in
        the window's logical coordinates.

        Rayyanpen takes input only over the pages less the controls; a tap
        anywhere else in the window comes straight here, so the dock, the
        header and every other button work while its pen is out. With a menu
        or a dialog open there are no pages to draw on at all.
        """
        root = self._root()
        if root is None or self._menu_open(root):
            return [], []
        focus = getattr(self.reader, "focus_overlay", None)
        clip = None
        if focus is None or not focus.is_active:
            clip = self._widget_rect(self.reader.canvas_scroller(), root)
            if clip is None:
                return [], []
        pages = []
        for view in self._views():
            rect = self._bounds(view)
            if rect is not None and clip is not None:
                rect = _intersect(rect, clip)
            if rect is not None:
                pages.append(rect)
        if not pages:
            return [], []
        controls: List[Rect] = []
        self._collect_controls(self.reader, root, controls)
        return pages, [c for c in controls if any(_intersect(c, p) for p in pages)]

    @staticmethod
    def _widget_rect(widget, root) -> Optional[Rect]:
        ok, b = widget.compute_bounds(root)
        if not ok or b.get_width() <= 0 or b.get_height() <= 0:
            return None
        return (b.get_x(), b.get_y(), b.get_width(), b.get_height())

    @staticmethod
    def _menu_open(root) -> bool:
        """A dialog over the window, or a popover anywhere in it."""
        for window in Gtk.Window.list_toplevels():
            if window is not root and window.get_mapped() and window.get_transient_for() is root:
                return True
        stack = [root]
        while stack:
            widget = stack.pop()
            if not widget.get_mapped():
                continue
            if isinstance(widget, Gtk.Popover):
                return True
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return False

    def _collect_controls(self, widget, root, out: List[Rect]) -> None:
        # The page-turn strips are invisible and lie over the sheet's edges:
        # they stay drawable, as in `view_at`.
        edges = [getattr(self.reader, n, None) for n in ("btn_prev", "btn_next")]
        # The canvas holds only pages (scroll mode's are rows of a list view).
        canvas = getattr(self.reader, "canvas_stack", None)
        if not widget.get_mapped() or widget is canvas or isinstance(widget, PageView) or any(
                widget is e for e in edges):
            return
        if isinstance(widget, CONTROL_TYPES) or widget is getattr(self.reader, "dock", None):
            rect = self._widget_rect(widget, root)
            if rect is not None:
                out.append(rect)
            return
        child = widget.get_first_child()
        while child is not None:
            self._collect_controls(child, root, out)
            child = child.get_next_sibling()

    def accepts(self, x: float, y: float) -> bool:
        return self.view_at(x, y) is not None

    def _to_pdf(self, view: PageView, points: Sequence[Point]):
        transform = view.transform()
        b = self._bounds(view)
        if transform is None or b is None:
            return None, None
        return [transform.point_to_pdf(x - b[0], y - b[1]) for x, y in points], transform

    # -- strokes -----------------------------------------------------------

    def add_stroke(self, kind: str, rgba: str, width_px: float,
                   points: Sequence[Point]) -> str:
        """
        Keep a finished stroke on the pages it was drawn over. '' if refused.

        A stroke across two facing pages is kept on both, each page holding the
        part that is on it, so it is whole on screen and each half goes with
        its own page afterwards. The pieces share one id (`a+b`), which
        `remove` and `restore` take apart again.
        """
        if kind not in KINDS or not points or width_px <= 0:
            return ""
        first = self.view_at(*points[0])
        if first is None:
            return ""
        ids: List[str] = []
        for view in self._views():
            b = self._bounds(view)
            if b is None:
                continue
            for run in self._runs_on(kind, points, b, width_px / 2.0, view is first):
                pdf, transform = self._to_pdf(view, run)
                if pdf is None:
                    continue
                if kind in FREEHAND:
                    pdf = simplify(pdf, SIMPLIFY_PX / transform.scale)
                try:
                    stroke = InkStroke(
                        page=view.page, kind=kind, rgba=rgba,
                        width=width_px / transform.scale,
                        points=[c for p in pdf for c in p],
                    )
                except ValueError:
                    continue
                self.ink.add(stroke)
                ids.append(stroke.id)
        if not ids:
            return ""
        self.changed()
        return ID_SEPARATOR.join(ids)

    @staticmethod
    def _runs_on(kind: str, points: Sequence[Point], bounds, pad: float,
                 is_first: bool) -> List[List[Point]]:
        """
        The parts of a stroke that a page at `bounds` has to keep.

        A shape is kept whole by every page its box reaches, and the page clips
        it. A freehand stroke is cut into the runs of points on the page, each
        with one point beyond either end so the ink runs up to the page's edge.
        """
        x0, y0 = bounds[0] - pad, bounds[1] - pad
        x1, y1 = bounds[0] + bounds[2] + pad, bounds[1] + bounds[3] + pad
        if kind in SHAPES:
            (ax, ay), (bx, by) = points[0], points[-1]
            reaches = (min(ax, bx) <= x1 and max(ax, bx) >= x0
                       and min(ay, by) <= y1 and max(ay, by) >= y0)
            return [[points[0], points[-1]]] if reaches or is_first else []
        inside = [x0 <= x <= x1 and y0 <= y <= y1 for x, y in points]
        runs: List[List[Point]] = []
        run: List[Point] = []
        for i, point in enumerate(points):
            near = inside[i] or (i > 0 and inside[i - 1]) or (
                i + 1 < len(points) and inside[i + 1])
            if near:
                run.append(point)
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
        return runs

    @staticmethod
    def _split_ids(ids: Sequence[str]) -> List[str]:
        return [part for i in ids for part in i.split(ID_SEPARATOR) if part]

    def remove(self, ids: Sequence[str]) -> int:
        """Rayyanpen's undo of a stroke it handed over."""
        removed = self.ink.remove(self._split_ids(ids))
        for stroke in removed:
            _bounded_put(self._removed, stroke.id, stroke)
        if removed:
            self.changed()
        return len(removed)

    def restore(self, ids: Sequence[str]) -> int:
        """Rayyanpen's redo of that undo."""
        strokes = [self._removed.pop(i) for i in self._split_ids(ids) if i in self._removed]
        if strokes:
            self.ink.restore(strokes)
            self.changed()
        return len(strokes)

    # -- erasing -----------------------------------------------------------

    def erase(self, session_id: str, radius_px: float, points: Sequence[Point]) -> int:
        """
        Rub out along a path. Streamed: Rayyanpen sends the eraser's path a
        few times a second under one `session_id`, which is undone as one.
        """
        if not points or radius_px <= 0:
            return 0
        session = self._sessions.get(session_id)
        if session is None:
            session = EraseSession()
            _bounded_put(self._sessions, session_id, session)
        touched = 0
        for view in self._views():
            b = self._bounds(view)
            if b is None:
                continue
            r = radius_px
            if not any(b[0] - r <= x <= b[0] + b[2] + r and b[1] - r <= y <= b[1] + b[3] + r
                       for x, y in points):
                continue
            pdf, transform = self._to_pdf(view, points)
            if pdf is None:
                continue
            removed, added = self.ink.erase(view.page, pdf, radius_px / transform.scale)
            session.record(removed, added)
            touched += len(removed)
        if touched:
            self.changed()
        return touched

    def undo_erase(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.undo(self.ink)
        self.changed()
        return True

    def redo_erase(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.redo(self.ink)
        self.changed()
        return True

    def clear_pages(self, pages: Sequence[int]) -> Optional[str]:
        """Delete every stroke on `pages`, undoable with `undo_erase(token)`."""
        session = EraseSession()
        for page in pages:
            session.record(self.ink.clear_page(page), [])
        if not session.removed:
            return None
        token = f"clear-{GLib.get_monotonic_time()}"
        _bounded_put(self._sessions, token, session)
        self.changed()
        return token

    def clear_page_at(self, x: float, y: float) -> str:
        view = self.view_at(x, y)
        if view is None:
            return ""
        return self.clear_pages([view.page]) or ""

    def pages_with_ink(self, pages: Sequence[int]) -> List[int]:
        return [p for p in pages if self.ink.strokes_on(p)]

    # -- redraw and save -----------------------------------------------------

    def changed(self) -> None:
        self.reader.redraw_ink()
        if not self._save_source:
            self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._on_save)

    def _on_save(self) -> bool:
        self._save_source = 0
        self.ink.save()
        return GLib.SOURCE_REMOVE

    def flush(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        if self.ink.dirty:
            self.ink.save()

