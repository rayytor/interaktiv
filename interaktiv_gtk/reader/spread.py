"""
The page canvas: one or two pages, laid out, scaled and kept rendered.

The scale problem this widget exists to solve is circular in GTK terms. Fit-page
means "as large as the space allows", but the space is only known during
allocation, and a widget may not ask for a resize while it is being allocated.
The way out is to stop asking: `SpreadView` lays its pages out by hand in
`do_size_allocate`, where the available size is finally a fact, and reports a
size request that does not depend on the fit at all. Custom zoom is the other
way round -- there the request is the page's full size and the scrolled window
does the rest.

The rules about which pages face each other and how big they are drawn are not
here at all -- they are in `paging.py`, ported from `viewer.js` and readable
without a display. This widget is what turns those numbers into an allocation
and a render request.
"""

from typing import List, Optional, Sequence, Tuple

from gi.repository import GLib, GObject, Graphene, Gsk, Gtk

from ..render import TextureCache
from ..render.service import LANE_PREFETCH, LANE_VISIBLE
from . import paging
from .page_view import PageView

MARGIN = paging.MARGIN

# What the widget asks for when the fit decides the size. Small enough that the
# window can still be made small, large enough that a viewport that briefly has
# no size does not produce a one-pixel page.
FALLBACK_NATURAL = 320


class SpreadView(Gtk.Widget):
    __gtype_name__ = "InteraktivSpreadView"

    __gsignals__ = {
        # The effective zoom changed -- fit modes compute it from the allocation,
        # so this is how the status bar learns what "Fit Page" currently means.
        "scale-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        # A visible page finished rendering, with how long it took.
        "page-rendered": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        # A hotspot or a publisher pin was pressed on one of the pages.
        "activity-activated": (GObject.SignalFlags.RUN_FIRST, None, (object, int)),
    }

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.service = None
        self.info = None
        self.cache = TextureCache()

        self.mode = "book"
        self.rotation = 0
        self.zoom_mode = "fit-page"
        self.custom_zoom = 1.0

        self._pages: List[int] = []
        self._views: List[PageView] = []
        self._zoom = 1.0
        self._submitted: set = set()
        self._pending_submit = 0

        # Where the activities come from. The spread does not know how an
        # overlay is built -- it asks the session for the one belonging to a
        # page and hands it to the view that is showing that page.
        self.overlay_provider = None
        self._reveal = True
        self._selected = None          # (page, act_index)
        self._search_matches: Dict[int, List[Tuple[float, float, float, float]]] = {}
        self._active_match: Optional[Tuple[int, int]] = None

    # -- wiring -----------------------------------------------------------

    def attach(self, service, info, overlay_provider=None) -> None:
        self.service = service
        self.info = info
        if overlay_provider is not None:
            self.overlay_provider = overlay_provider
        self._sync_views()
        self.refresh_overlays()
        self.invalidate()

    def shutdown(self) -> None:
        self._cancel_submit()
        self.cache.clear()
        for view in self._views:
            view.unparent()
        self._views = []
        self.service = None

    # -- state ------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        # A mode change makes every texture but the visible one unreachable,
        # and on a board the memory is worth more than the cache.
        self.cache.clear()
        self.invalidate()

    def set_rotation(self, rotation: int) -> None:
        rotation %= 360
        if rotation == self.rotation:
            return
        self.rotation = rotation
        self.cache.clear()
        self.invalidate()
        self.queue_resize()

    def set_zoom(self, mode: str, custom: Optional[float] = None) -> None:
        if mode != self.zoom_mode:
            # The request mode itself depends on this, so the change has to
            # reach the parent, not just this widget's own measure.
            self.zoom_mode = mode
            parent = self.get_parent()
            if parent is not None:
                parent.queue_resize()
        if custom is not None:
            self.custom_zoom = custom
        self.invalidate()
        self.queue_resize()

    def show_pages(self, pages: Sequence[int]) -> None:
        pages = [p for p in pages if 1 <= p <= (self.info.page_count if self.info else 0)]
        if pages == self._pages:
            return
        self._pages = list(pages)
        self._sync_views()
        self.invalidate()
        self.queue_resize()

    @property
    def pages(self) -> List[int]:
        return list(self._pages)

    @property
    def zoom(self) -> float:
        return self._zoom

    def invalidate(self) -> None:
        """Everything queued is now stale; re-ask for what is on screen."""
        self._submitted.clear()
        if self.service is not None:
            self.service.bump_generation()
        self._schedule_submit()

    def drop_textures(self) -> None:
        """Called under memory pressure: give the pixels back and re-render."""
        self.cache.clear()
        for view in self._views:
            view.clear_texture()
        self._submitted.clear()
        self._schedule_submit()

    # -- pairing ----------------------------------------------------------

    def pages_for(self, page: int) -> List[int]:
        return paging.pages_for(
            page, self.info.page_count if self.info else 0, self.mode
        )

    # -- geometry ---------------------------------------------------------

    def _page_size(self, page: int) -> Tuple[float, float]:
        w, h = self.info.size(page) if self.info else (595.0, 842.0)
        if self.rotation % 180 == 90:
            return (h, w)
        return (w, h)

    def _spread_size(self, pages: Optional[Sequence[int]] = None) -> Tuple[float, float]:
        pages = self._pages if pages is None else pages
        # The rotation is already in `_page_size`, so it is not applied twice.
        return paging.spread_size([self._page_size(p) for p in pages])

    def _fit_scale(self, avail_w: float, avail_h: float) -> float:
        spread_w, spread_h = self._spread_size()
        return paging.fit_scale(
            spread_w, spread_h, avail_w, avail_h, self.zoom_mode, self.custom_zoom
        )

    # -- GTK layout -------------------------------------------------------

    def do_get_request_mode(self):
        """
        Only fit-width is genuinely height-for-width.

        Saying so matters, and not only for tidiness: in custom zoom the widget
        reports a minimum width larger than the window, and a height-for-width
        widget is then measured by the scrolled window at a width below that
        minimum, which GTK warns about on every single layout pass. Declaring a
        constant size in the modes that have one means GTK never asks the
        question.
        """
        if self.zoom_mode == "fit-width":
            return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation, for_size):
        spread_w, spread_h = self._spread_size()
        horizontal = orientation == Gtk.Orientation.HORIZONTAL

        if self.zoom_mode == "custom":
            size = int(round(
                (spread_w if horizontal else spread_h) * self.custom_zoom
            )) + 2 * MARGIN
            return (size, size, -1, -1)

        if horizontal:
            # Both fit modes take the width they are given.
            return (1, FALLBACK_NATURAL, -1, -1)

        if self.zoom_mode == "fit-width" and for_size > 0:
            # Fit-width is genuinely height-for-width: the page may then be
            # taller than the viewport, which is what the scrollbar is for, so
            # the minimum has to be the real height and not a token.
            scale = paging.fit_scale(
                spread_w, spread_h, for_size - 2 * MARGIN, 0, "fit-width"
            )
            size = int(round(spread_h * scale)) + 2 * MARGIN
            return (size, size, -1, -1)

        return (1, FALLBACK_NATURAL, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        if not self._views:
            return
        zoom = self._fit_scale(width - 2 * MARGIN, height - 2 * MARGIN)
        sizes = [self._page_size(p) for p in self._pages]
        total_w = sum(w for w, _ in sizes) * zoom
        max_h = max(h for _, h in sizes) * zoom

        x = max(MARGIN, (width - total_w) / 2.0)
        for view, (pw, ph) in zip(self._views, sizes):
            page_w = pw * zoom
            page_h = ph * zoom
            y = max(MARGIN, (height - max_h) / 2.0) + (max_h - page_h) / 2.0
            transform = Gsk.Transform().translate(Graphene.Point().init(x, y))
            view.allocate(max(1, int(round(page_w))), max(1, int(round(page_h))), -1, transform)
            x += page_w

        if abs(zoom - self._zoom) > 1e-6:
            self._zoom = zoom
            self._submitted.clear()
            if self.service is not None:
                self.service.bump_generation()
            self.emit("scale-changed", zoom)
        self._schedule_submit()

    def do_snapshot(self, snapshot):
        for view in self._views:
            self.snapshot_child(view, snapshot)

    # -- children ---------------------------------------------------------

    def _sync_views(self) -> None:
        while len(self._views) < len(self._pages):
            view = PageView()
            view.set_parent(self)
            view.connect("activity-activated", self._on_activity_activated)
            view.set_reveal(self._reveal)
            self._views.append(view)
        while len(self._views) > len(self._pages):
            self._views.pop().unparent()
        for view, page in zip(self._views, self._pages):
            view.set_page(page, self.rotation)
            if self.info is not None:
                view.set_pdf_size(*self.info.size(page))
        self.refresh_overlays()
        self._apply_search_matches()

    # -- search -----------------------------------------------------------

    def set_search_matches(
        self,
        page_matches: Dict[int, List[Tuple[float, float, float, float]]],
        active_match: Optional[Tuple[int, int]] = None,
    ) -> None:
        self._search_matches = dict(page_matches)
        self._active_match = active_match
        self._apply_search_matches()

    def _apply_search_matches(self) -> None:
        for view in self._views:
            matches = self._search_matches.get(view.page, [])
            active_idx = None
            if self._active_match and self._active_match[0] == view.page:
                active_idx = self._active_match[1]
            view.set_search_matches(matches, active_idx)

    # -- activities -------------------------------------------------------

    def refresh_overlays(self) -> None:
        """Re-ask for the activities of whatever is on screen."""
        for view, page in zip(self._views, self._pages):
            overlay = (
                self.overlay_provider(page) if self.overlay_provider else None
            )
            view.set_overlay(overlay, page)
        self._apply_selection()

    def set_reveal(self, reveal: bool) -> None:
        """
        Whether every region is outlined, or only the one under the pointer.

        The web only ever shows all of them behind `?debug=activities`, because
        a mouse has hover to reveal them with. A board does not, so this is a
        real control here and it is on by default -- otherwise the activities a
        teacher came for would be invisible until touched by accident.
        """
        self._reveal = bool(reveal)
        for view in self._views:
            view.set_reveal(self._reveal)

    def select_activity(self, page: int, act_index: int) -> None:
        self._selected = (page, act_index)
        self._apply_selection()

    def clear_selection(self) -> None:
        if self._selected is None:
            return
        self._selected = None
        self._apply_selection()

    def _apply_selection(self) -> None:
        """
        Light up every piece of the selected activity.

        A region that flows across a column break is two pieces, and picking it
        out of the sidebar should show the teacher both of them -- the hover
        rule (one piece at a time) is about the pointer, not about the
        activity.
        """
        for view, page in zip(self._views, self._pages):
            overlay = view.overlay
            if self._selected is None or overlay is None or page != self._selected[0]:
                view.set_selected(None)
                continue
            view.set_selected(self._selected[1])

    def _on_activity_activated(self, view, target) -> None:
        page = view.page
        self.emit("activity-activated", target, page)

    # -- rendering --------------------------------------------------------

    def _schedule_submit(self) -> None:
        """
        Ask for pixels, but not from inside an allocation.

        `do_size_allocate` is the only place the fit scale is known, and it is
        also the one place a widget must not start a new layout pass. Deferring
        to an idle keeps the two apart and coalesces the several allocations a
        single window resize produces into one round of requests.
        """
        if self._pending_submit or self.service is None:
            return
        self._pending_submit = GLib.idle_add(self._submit_now)

    def _cancel_submit(self) -> None:
        if self._pending_submit:
            GLib.source_remove(self._pending_submit)
            self._pending_submit = 0

    def _submit_now(self) -> bool:
        self._pending_submit = 0
        if self.service is None or not self._pages:
            return GLib.SOURCE_REMOVE

        # Textures are in device pixels; a 200 % board scale wants twice as many
        # of them for the same logical page.
        render_scale = self._zoom * self.get_scale_factor()
        generation = self.service.generation

        for view, page in zip(self._views, self._pages):
            view.set_page(page, self.rotation)
            key = (page, round(render_scale, 3), self.rotation % 360, None)
            cached = self.cache.get(key)
            if cached is not None:
                view.set_texture(cached, page, self.rotation)
                continue
            if key in self._submitted:
                continue
            self._submitted.add(key)
            self.service.submit(
                page, render_scale, self.rotation,
                lane=LANE_VISIBLE, generation=generation,
            )

        self._prefetch(render_scale, generation)
        return GLib.SOURCE_REMOVE

    def _prefetch(self, render_scale: float, generation: int) -> None:
        """
        The spread after this one, behind everything visible.

        One spread ahead, not several: the point is that the common gesture --
        turning forward, one page at a time -- finds its texture already there,
        not that the whole chapter is resident.
        """
        if not self._pages or self.info is None:
            return
        following = self.pages_for(min(self._pages[-1] + 1, self.info.page_count))
        for page in following:
            if page in self._pages:
                continue
            key = (page, round(render_scale, 3), self.rotation % 360, None)
            if key in self.cache or key in self._submitted:
                continue
            self._submitted.add(key)
            self.service.submit(
                page, render_scale, self.rotation,
                lane=LANE_PREFETCH, generation=generation,
            )

    def on_result(self, result) -> None:
        """A render came back. Cache it, and paint it if it is still wanted."""
        self.cache.put(result.key, result.texture, result.nbytes)
        page, scale, rotation, clip = result.key
        if clip is not None:
            return
        want = round(self._zoom * self.get_scale_factor(), 3)
        for view, shown in zip(self._views, self._pages):
            if shown == page and rotation == self.rotation and abs(scale - want) < 1e-6:
                view.set_texture(result.texture, page, rotation)
                self.emit("page-rendered", page, result.render_ms)
