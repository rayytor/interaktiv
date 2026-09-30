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

Point = Tuple[float, float]


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
        """Keep a finished stroke on the page it was drawn over. '' if refused."""
        if kind not in KINDS or not points or width_px <= 0:
            return ""
        first = self.view_at(*points[0])
        if first is None:
            return ""
        view = self._majority(points, first)
        pdf, transform = self._to_pdf(view, points)
        if pdf is None:
            return ""
        if kind in SHAPES:
            pdf = [pdf[0], pdf[-1]]
        elif kind in FREEHAND:
            pdf = simplify(pdf, SIMPLIFY_PX / transform.scale)
        try:
            stroke = InkStroke(
                page=view.page, kind=kind, rgba=rgba,
                width=width_px / transform.scale,
                points=[c for p in pdf for c in p],
            )
        except ValueError:
            return ""
        self.ink.add(stroke)
        self.changed()
        return stroke.id

    def _majority(self, points: Sequence[Point], first: PageView) -> PageView:
        """A stroke across two facing pages belongs to the one it is mostly on."""
        best, best_n = first, -1
        for view in self._views():
            b = self._bounds(view)
            if b is None:
                continue
            n = sum(1 for x, y in points
                    if b[0] <= x <= b[0] + b[2] and b[1] <= y <= b[1] + b[3])
            if n > best_n or (n == best_n and view is first):
                best, best_n = view, n
        return best

    def remove(self, ids: Sequence[str]) -> int:
        """Rayyanpen's undo of a stroke it handed over."""
        removed = self.ink.remove(ids)
        for stroke in removed:
            _bounded_put(self._removed, stroke.id, stroke)
        if removed:
            self.changed()
        return len(removed)

    def restore(self, ids: Sequence[str]) -> int:
        """Rayyanpen's redo of that undo."""
        strokes = [self._removed.pop(i) for i in ids if i in self._removed]
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

