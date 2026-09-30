"""
Zooming the way a phone does.

Two fingers on the sheet hold on to the print under them: pinching zooms around
the point between the fingers, and moving both fingers carries the page with
them, in one gesture. A double tap zooms around the tapped point.

The anchor is a point on the printed page in PDF user space, not a scroll
offset. After the zoom has been laid out the reader looks up where that point
has gone and scrolls it back under the fingers. That is the same whether the
sheet is a centred spread smaller than the window, a spread wider than it, or
one row of the continuous scroll, so neither layout's arithmetic has to be
repeated here.

While a pinch is in flight the pages are not re-rendered: the texture already
on screen is scaled by GSK, which is instant, and the sharp render is asked for
when the fingers lift -- or when they have held still for `SETTLE_MS`.
"""

from typing import Callable, List, Optional, Tuple

from gi.repository import GLib, Gtk

from . import paging

# A pinch held still this long gets sharp pages without waiting for the lift.
SETTLE_MS = 250
# Zoom changes smaller than this are not worth a layout pass.
MIN_STEP = 0.002

Anchor = Tuple[int, Tuple[float, float]]  # (page, PDF point)


def nearest_view(views, scroller: Gtk.Widget, x: float, y: float):
    """
    The page view under (x, y) in `scroller` coordinates, or the closest one:
    fingers between two pages or in the margin still hold on to a page.
    """
    best, best_d = None, None
    for view in views:
        ok, b = view.compute_bounds(scroller)
        if not ok or b.get_width() <= 0:
            continue
        left, top = b.get_x(), b.get_y()
        right, bottom = left + b.get_width(), top + b.get_height()
        dx = max(left - x, 0.0, x - right)
        dy = max(top - y, 0.0, y - bottom)
        d = dx * dx + dy * dy
        if best_d is None or d < best_d:
            best, best_d = view, d
    return best


def anchor_at(views, scroller: Gtk.Widget, x: float, y: float) -> Optional[Anchor]:
    """The printed point under (x, y), as (page, PDF point)."""
    view = nearest_view(views, scroller, x, y)
    if view is None:
        return None
    transform = view.transform()
    ok, b = view.compute_bounds(scroller)
    if transform is None or not ok:
        return None
    return (view.page, transform.point_to_pdf(x - b.get_x(), y - b.get_y()))


def anchor_offset(views, scroller: Gtk.Widget, anchor: Anchor,
                  x: float, y: float) -> Optional[Tuple[float, float]]:
    """How far the anchor now is from (x, y), in `scroller` coordinates."""
    page, (px, py) = anchor
    for view in views:
        if view.page != page:
            continue
        transform = view.transform()
        ok, b = view.compute_bounds(scroller)
        if transform is None or not ok:
            return None
        wx, wy = transform.point_to_widget(px, py)
        return (b.get_x() + wx - x, b.get_y() + wy - y)
    return None


def scroll_by(scroller: Gtk.ScrolledWindow, dx: float, dy: float) -> None:
    for adjustment, delta in (
        (scroller.get_hadjustment(), dx),
        (scroller.get_vadjustment(), dy),
    ):
        if adjustment is None or abs(delta) < 0.01:
            continue
        top = max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(min(top, max(adjustment.get_lower(), adjustment.get_value() + delta)))


class PinchZoom:
    """
    One reader's two-finger zoom-and-pan, and its anchored single-step zooms.

    The reader provides five things: the scrolled window on screen, the page
    views in it, the zoom as it is now, a way to set a zoom without rendering
    (`live=True`) and a way to render what is on screen.
    """

    def __init__(
        self,
        scroller: Callable[[], Gtk.ScrolledWindow],
        views: Callable[[], List],
        zoom: Callable[[], float],
        set_zoom: Callable[[float, bool], None],
        settle: Callable[[], None],
    ) -> None:
        self._scroller = scroller
        self._views = views
        self._zoom = zoom
        self._set_zoom = set_zoom
        self._settle = settle
        self.active = False
        self._base = 1.0
        self._anchor: Optional[Anchor] = None
        self._target: Optional[Tuple[float, float]] = None
        self._tick = 0
        self._settle_source = 0

    # -- the gesture ------------------------------------------------------

    def begin(self, x: float, y: float) -> None:
        """Two fingers landed, centred on (x, y) in the scroller."""
        self.active = True
        self._base = self._zoom()
        self._anchor = anchor_at(self._views(), self._scroller(), x, y)
        self._target = (x, y)

    def update(self, x: float, y: float, scale: float) -> None:
        """The fingers are centred on (x, y), `scale` times as far apart."""
        if not self.active:
            self.begin(x, y)
        target = paging.pinch_zoom(self._base, scale)
        self._target = (x, y)
        if abs(target - self._zoom()) >= MIN_STEP * max(1.0, target):
            self._set_zoom(target, True)
        self._follow()
        self._restart_settle()

    def end(self) -> None:
        if not self.active:
            return
        self.active = False
        self._cancel_settle()
        self._settle()

    # -- one-shot zooms ---------------------------------------------------

    def zoom_at(self, x: float, y: float, apply_zoom: Callable[[], None]) -> None:
        """
        Run `apply_zoom` (which changes the zoom any way it likes) and keep the
        printed point under (x, y) where it is: the double tap and ctrl+wheel.
        """
        self._anchor = anchor_at(self._views(), self._scroller(), x, y)
        self._target = (x, y)
        apply_zoom()
        self._follow()

    # -- keeping the anchor under the fingers -----------------------------

    def _follow(self) -> None:
        """Scroll the anchor under the target once the new zoom is laid out."""
        if self._tick or self._anchor is None:
            return
        scroller = self._scroller()
        frames = [0]

        def tick(_widget, _clock) -> bool:
            # The first tick is ahead of this frame's layout; the second
            # follows it, when the adjustments know the new size.
            frames[0] += 1
            if frames[0] < 2:
                return GLib.SOURCE_CONTINUE
            self._tick = 0
            self.follow_now()
            return GLib.SOURCE_REMOVE

        self._tick = scroller.add_tick_callback(tick)

    def follow_now(self) -> None:
        if self._anchor is None or self._target is None:
            return
        scroller = self._scroller()
        offset = anchor_offset(self._views(), scroller, self._anchor, *self._target)
        if offset is not None:
            scroll_by(scroller, *offset)

    def _restart_settle(self) -> None:
        self._cancel_settle()
        self._settle_source = GLib.timeout_add(SETTLE_MS, self._on_settle)

    def _cancel_settle(self) -> None:
        if self._settle_source:
            GLib.source_remove(self._settle_source)
            self._settle_source = 0

    def _on_settle(self) -> bool:
        self._settle_source = 0
        self._settle()
        return GLib.SOURCE_REMOVE
