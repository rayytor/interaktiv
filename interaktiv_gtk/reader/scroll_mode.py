"""
Continuous vertical scroll: every page of the book in one virtualized list.

`Gtk.ListView` keeps about two hundred rows bound around its anchor whatever
the size of the viewport, so a bound row is not a visible page. A page is
rendered only while it is within `PRELOAD` viewport heights of the screen and
gives its texture back when it leaves that band. Rendering every bound row
instead holds two hundred page textures, which at a board's 200 % scale is
well over a gigabyte.
"""

from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple

from gi.repository import Gio, GLib, GObject, Graphene, Gsk, Gtk

from ..render import TextureCache
from ..render.service import LANE_PREFETCH, LANE_VISIBLE
from ..widgets import after_layout, scroll_to_item
from . import paging
from .page_view import PageView

PAGE_GAP = 20

# How far outside the viewport, in viewport heights, a page stays rendered.
PRELOAD = 0.75

ON_SCREEN = "on-screen"
NEARBY = "nearby"
FAR = "far"


def band(top: float, bottom: float, viewport_height: float,
         preload: float = PRELOAD) -> str:
    """Where a row spanning `top`..`bottom` lies, in viewport coordinates."""
    if bottom > 0 and top < viewport_height:
        return ON_SCREEN
    reach = viewport_height * preload
    if bottom > -reach and top < viewport_height + reach:
        return NEARBY
    return FAR


def overlap(top: float, bottom: float, viewport_height: float) -> float:
    """How many pixels of a row spanning `top`..`bottom` are on screen."""
    return max(0.0, min(bottom, viewport_height) - max(top, 0.0))


class ScrollPageItem(GObject.Object):
    __gtype_name__ = "InteraktivScrollPageItem"

    def __init__(self, page: int, width: float, height: float):
        super().__init__()
        self.page = page
        self.width = width
        self.height = height


class PageRowWrapper(Gtk.Widget):
    """
    Wraps a single `PageView` inside a `Gtk.ListView` row.

    Centers the page horizontally with a bottom margin of `PAGE_GAP`.
    Measures height as `target_h + PAGE_GAP` so `Gtk.ListView` allocates the row
    accurately.
    """
    __gtype_name__ = "InteraktivPageRowWrapper"

    def __init__(self, page_view: PageView):
        super().__init__()
        self.page_view = page_view
        self.page_view.set_parent(self)
        self.target_w = 400
        self.target_h = 600

    def set_target_size(self, w: int, h: int) -> None:
        if (self.target_w, self.target_h) == (w, h):
            return
        self.target_w = max(1, w)
        self.target_h = max(1, h)
        self.queue_resize()

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> Tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.VERTICAL:
            size = self.target_h + PAGE_GAP
            return (size, size, -1, -1)
        return (self.target_w, self.target_w, -1, -1)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        x = max(0.0, (width - self.target_w) / 2.0)
        y = 0.0
        transform = Gsk.Transform().translate(Graphene.Point().init(x, y))
        self.page_view.allocate(self.target_w, self.target_h, -1, transform)

    def do_snapshot(self, snapshot) -> None:
        self.snapshot_child(self.page_view, snapshot)


class ScrollModeView(Gtk.Box):
    __gtype_name__ = "InteraktivScrollModeView"

    __gsignals__ = {
        "scale-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "page-rendered": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        "activity-activated": (GObject.SignalFlags.RUN_FIRST, None, (object, int)),
        "zoom-toggled": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "dominant-page-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, cache: Optional[TextureCache] = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.add_css_class("scroll-mode-view")

        self.service = None
        self.info = None
        self.cache = cache if cache is not None else TextureCache()
        self.overlay_provider: Optional[Callable] = None

        self.rotation = 0
        self.zoom_mode = "fit-width"
        self.custom_zoom = 1.0
        self.current_page = 1

        self._zoom = 1.0
        self._reveal = True
        self._selected: Optional[Tuple[int, int]] = None
        self._bound_views: Dict[int, PageView] = {}
        self._search_matches: Dict[int, List[Tuple[float, float, float, float]]] = {}
        self._active_match: Optional[Tuple[int, int]] = None
        # Render key -> the generation it was asked for in. A key from an
        # older generation was dropped by the service and may be asked again.
        self._submitted: Dict[Tuple, int] = {}
        self._update_source = 0
        self._refit_source = 0
        self._suppress_scroll_sync = False
        # A scroll asked for before the list has a size, to apply once it has.
        self._pending_scroll = False
        self._common_size: Optional[Tuple[int, int]] = None
        self._ink = None
        self.last_zoom_tap = None
        # See SpreadView.live: scale what is on screen, render when it settles.
        self.live = False

        self._store = Gio.ListStore(item_type=ScrollPageItem)
        self._selection = Gtk.NoSelection(model=self._store)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        factory.connect("unbind", self._on_unbind)
        factory.connect("teardown", self._on_teardown)
        self._factory = factory

        self.list_view = Gtk.ListView(model=self._selection, factory=factory)
        self.list_view.add_css_class("scroll-pages-list")

        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True,
        )
        self.scroller.add_css_class("reader-canvas")
        self.scroller.set_child(self.list_view)
        self.append(self.scroller)

        vadjustment = self.scroller.get_vadjustment()
        vadjustment.connect("value-changed", lambda *_: self._queue_update())
        # "changed" is the list settling after a layout: rows have moved.
        vadjustment.connect("changed", lambda *_: self._queue_update())
        # The viewport's size is the adjustments' page size. `Gtk.Box` lays
        # out through a layout manager, so its `size_allocate` is never called
        # and cannot be used to notice a resize.
        vadjustment.connect("notify::page-size", lambda *_: self._queue_refit())
        self.scroller.get_hadjustment().connect(
            "notify::page-size", lambda *_: self._queue_refit()
        )
        self.connect("map", lambda *_: self._queue_refit())
        self.connect("unmap", lambda *_: self._release_textures())

    # -- wiring -----------------------------------------------------------

    def attach(self, service, info, overlay_provider=None) -> None:
        self.service = service
        self.info = info
        self._common_size = None
        if overlay_provider is not None:
            self.overlay_provider = overlay_provider
        self._populate_model()
        self.refresh_overlays()
        self.invalidate()

    def shutdown(self) -> None:
        for name in ("_update_source", "_refit_source"):
            source = getattr(self, name)
            if source:
                GLib.source_remove(source)
                setattr(self, name, 0)
        self._store.remove_all()
        self._bound_views.clear()
        self._submitted.clear()
        self.service = None

    def _populate_model(self) -> None:
        self._store.remove_all()
        if not self.info or self.info.page_count <= 0:
            return
        for p in range(1, self.info.page_count + 1):
            w, h = self._page_size(p)
            self._store.append(ScrollPageItem(p, w, h))

    # -- state & zoom -----------------------------------------------------

    def _page_size(self, page: int) -> Tuple[float, float]:
        w, h = self.info.size(page) if self.info else (595.0, 842.0)
        if self.rotation % 180 == 90:
            return (h, w)
        return (w, h)

    def _reference_size(self) -> Tuple[float, float]:
        """
        The page size the fit modes fit: the book's most common one. A cover
        that is a double-width spread would otherwise set the zoom for every
        page after it.
        """
        if not self.info or self.info.page_count <= 0:
            return (595.0, 842.0)
        if self._common_size is None:
            sizes = Counter(
                (round(w), round(h)) for w, h in
                (self.info.size(p) for p in range(1, self.info.page_count + 1))
            )
            self._common_size = sizes.most_common(1)[0][0]
        w, h = self._common_size
        return (h, w) if self.rotation % 180 == 90 else (w, h)

    def set_rotation(self, rotation: int) -> None:
        rotation %= 360
        if rotation == self.rotation:
            return
        self.rotation = rotation
        self.cache.clear()
        self._populate_model()
        self.invalidate()

    def set_zoom(self, mode: str, custom: Optional[float] = None) -> None:
        self.zoom_mode = mode
        if custom is not None:
            self.custom_zoom = custom
        self._recompute_scale()
        self.invalidate()

    def _recompute_scale(self) -> None:
        if not self.info or self.info.page_count <= 0:
            return
        width, height = self.scroller.get_width(), self.scroller.get_height()
        if self.zoom_mode != "custom" and (width <= 0 or height <= 0):
            return  # not on screen yet; `map` comes back here
        pw, ph = self._reference_size()
        zoom = paging.fit_scale(
            pw, ph, width - 2 * paging.MARGIN, height - 2 * paging.MARGIN,
            self.zoom_mode, self.custom_zoom,
        )
        if abs(zoom - self._zoom) > 1e-6:
            self._zoom = zoom
            if not self.live:
                self._submitted.clear()
                if self.service is not None:
                    self.service.bump_generation()
            self.emit("scale-changed", zoom)
            self._update_row_sizes()
            self._queue_update()

    def _update_row_sizes(self) -> None:
        for page, view in self._bound_views.items():
            parent = view.get_parent()
            if isinstance(parent, PageRowWrapper):
                pw, ph = self._page_size(page)
                tw = max(1, int(round(pw * self._zoom)))
                th = max(1, int(round(ph * self._zoom)))
                parent.set_target_size(tw, th)

    def _queue_refit(self) -> None:
        # Deferred: this is called from inside a size allocation, and a row
        # may not be resized there.
        if not self._refit_source:
            self._refit_source = GLib.idle_add(self._refit)

    def _refit(self) -> bool:
        self._refit_source = 0
        self._recompute_scale()
        if self._pending_scroll:
            self._scroll_to_current()
        self._queue_update()
        return GLib.SOURCE_REMOVE

    @property
    def zoom(self) -> float:
        return self._zoom

    def texture_for(self, page: int):
        """The texture on screen for `page`, if that page is rendered."""
        view = self._bound_views.get(page)
        return view.texture if view is not None else None

    def invalidate(self) -> None:
        if self.live:
            self._queue_update()
            return
        self._submitted.clear()
        if self.service is not None:
            self.service.bump_generation()
        self._queue_update()

    def drop_textures(self) -> None:
        self.cache.clear()
        self._release_textures()
        self._queue_update()

    def _release_textures(self) -> None:
        for view in self._bound_views.values():
            view.clear_texture()
        self._submitted.clear()

    # -- factory callbacks ------------------------------------------------

    def _on_setup(self, _factory, list_item) -> None:
        view = PageView()
        view.connect("activity-activated", self._on_activity_activated)
        view.connect("zoom-toggled", self._on_zoom_tap)
        wrapper = PageRowWrapper(view)
        list_item.set_child(wrapper)
        list_item.wrapper = wrapper
        list_item.view = view

    def _on_bind(self, _factory, list_item) -> None:
        item = list_item.get_item()
        if item is None:
            return
        page = item.page
        view = list_item.view
        wrapper = list_item.wrapper

        pw, ph = self._page_size(page)
        tw = max(1, int(round(pw * self._zoom)))
        th = max(1, int(round(ph * self._zoom)))
        wrapper.set_target_size(tw, th)

        view.set_page(page, self.rotation)
        if self.info is not None:
            view.set_pdf_size(*self.info.size(page))
        view.set_reveal(self._reveal)
        view.set_ink(self._ink)
        overlay = self.overlay_provider(page) if self.overlay_provider else None
        view.set_overlay(overlay, page)

        if self._selected and self._selected[0] == page:
            view.set_selected(self._selected[1])
        else:
            view.set_selected(None)

        matches = self._search_matches.get(page, [])
        active_idx = None
        if self._active_match and self._active_match[0] == page:
            active_idx = self._active_match[1]
        view.set_search_matches(matches, active_idx)

        self._bound_views[page] = view
        # Not rendered here: being bound says nothing about being on screen.
        self._queue_update()

    def _on_unbind(self, _factory, list_item) -> None:
        item = list_item.get_item()
        if item is not None:
            self._bound_views.pop(item.page, None)
        # Drop the texture to free memory for offscreen pages (unmountPageCanvas)
        list_item.view.clear_texture()
        list_item.view.clear_search_matches()

    def set_search_matches(
        self,
        page_matches: Dict[int, List[Tuple[float, float, float, float]]],
        active_match: Optional[Tuple[int, int]] = None,
    ) -> None:
        self._search_matches = dict(page_matches)
        self._active_match = active_match
        self._apply_search_matches()

    def _apply_search_matches(self) -> None:
        for page, view in self._bound_views.items():
            matches = self._search_matches.get(page, [])
            active_idx = None
            if self._active_match and self._active_match[0] == page:
                active_idx = self._active_match[1]
            view.set_search_matches(matches, active_idx)

    def _on_teardown(self, _factory, list_item) -> None:
        wrapper = getattr(list_item, "wrapper", None)
        if wrapper is not None and wrapper.page_view is not None:
            wrapper.page_view.unparent()
        list_item.set_child(None)

    # -- rendering --------------------------------------------------------

    def _queue_update(self) -> None:
        if not self._update_source:
            self._update_source = GLib.idle_add(self._update_visible)

    def _row_span(self, view: PageView) -> Optional[Tuple[float, float]]:
        """The top and bottom of a bound page in the viewport, if it is laid out."""
        # The list keeps rows bound that it has stopped laying out; those are
        # unmapped and their last position means nothing.
        if not view.get_mapped():
            return None
        ok, bounds = view.compute_bounds(self.scroller)
        if not ok or bounds.get_height() <= 0:
            return None
        return bounds.get_y(), bounds.get_y() + bounds.get_height()

    def _update_visible(self) -> bool:
        """
        Render what is near the screen, release what is not, and report which
        page the viewport is mostly showing.
        """
        self._update_source = 0
        height = self.scroller.get_height()
        if not self.get_mapped() or height <= 0:
            return GLib.SOURCE_REMOVE

        best_page, best_overlap = None, 0.0
        for page, view in list(self._bound_views.items()):
            span = self._row_span(view)
            where = band(span[0], span[1], height) if span is not None else FAR
            if where == FAR:
                if view.has_texture:
                    view.clear_texture()
                continue
            self._request(page, view, LANE_VISIBLE if where == ON_SCREEN else LANE_PREFETCH)
            visible = overlap(span[0], span[1], height)
            if visible > best_overlap:
                best_page, best_overlap = page, visible

        if (best_page is not None and best_page != self.current_page
                and not self._suppress_scroll_sync):
            self.current_page = best_page
            self.emit("dominant-page-changed", best_page)
        return GLib.SOURCE_REMOVE

    def _request(self, page: int, view: PageView, lane: int) -> None:
        if self.service is None:
            return
        if self.live and view.has_texture:
            return  # mid-pinch: the texture on screen is scaled for now
        render_scale = self._zoom * self.get_scale_factor()
        key = (page, round(render_scale, 3), self.rotation % 360, None)
        cached = self.cache.get(key)
        if cached is not None:
            view.set_texture(cached, page, self.rotation)
            return
        generation = self.service.generation
        if self._submitted.get(key) == generation:
            return
        self._submitted[key] = generation
        self.service.submit(
            page, render_scale, self.rotation, lane=lane, generation=generation,
        )

    def on_result(self, result) -> None:
        self.cache.put(result.key, result.texture, result.nbytes)
        self._submitted.pop(result.key, None)
        page, scale, rotation, clip = result.key
        if clip is not None:
            return
        view = self._bound_views.get(page)
        want = round(self._zoom * self.get_scale_factor(), 3)
        if view is None or rotation != self.rotation or abs(scale - want) > 1e-6:
            return
        span = self._row_span(view)
        if span is None or band(span[0], span[1], self.scroller.get_height()) == FAR:
            return  # scrolled away while it was being rendered
        view.set_texture(result.texture, page, rotation)
        self.emit("page-rendered", page, result.render_ms)

    # -- ink --------------------------------------------------------------

    def set_ink(self, ink) -> None:
        self._ink = ink
        for view in self._bound_views.values():
            view.set_ink(ink)

    def redraw_ink(self) -> None:
        for view in self._bound_views.values():
            view.queue_draw()

    def page_views(self) -> List[PageView]:
        """The page views laid out now, which may include some off screen."""
        return [v for v in self._bound_views.values() if v.get_mapped()]

    # -- activities & overlays --------------------------------------------

    def refresh_overlays(self) -> None:
        for page, view in self._bound_views.items():
            overlay = self.overlay_provider(page) if self.overlay_provider else None
            view.set_overlay(overlay, page)
        self._apply_selection()

    def set_reveal(self, reveal: bool) -> None:
        self._reveal = bool(reveal)
        for view in self._bound_views.values():
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
        for page, view in self._bound_views.items():
            if self._selected is None or page != self._selected[0]:
                view.set_selected(None)
            else:
                view.set_selected(self._selected[1])

    def _on_zoom_tap(self, view) -> None:
        # Kept for the reader, which zooms around the tapped point.
        self.last_zoom_tap = (view, view.last_tap)
        self.emit("zoom-toggled")

    def _on_activity_activated(self, view, target) -> None:
        self.emit("activity-activated", target, view.page)

    # -- navigation & scrolling -------------------------------------------

    def go_to_page(self, page: int) -> None:
        if not self.info or self.info.page_count <= 0:
            return
        page = min(max(1, int(page)), self.info.page_count)
        self.current_page = page
        self._scroll_to_current()

    def _scroll_to_current(self) -> None:
        # The list cannot scroll before it has a size: it is hidden behind the
        # spread until scroll mode is chosen.
        if not self.get_mapped() or self.scroller.get_height() <= 0:
            self._pending_scroll = True
            return
        self._pending_scroll = False
        self._suppress_scroll_sync = True
        scroll_to_item(self.list_view, self.current_page - 1)
        after_layout(self, self._align_current)

    def _align_current(self) -> None:
        """
        Bring the top of the current page to the top of the viewport. The list
        only scrolls far enough to make a row visible, which for a page below
        the screen leaves it at the bottom edge.
        """
        view = self._bound_views.get(self.current_page)
        span = self._row_span(view) if view is not None else None
        if span is not None:
            vadj = self.scroller.get_vadjustment()
            limit = max(vadj.get_lower(), vadj.get_upper() - vadj.get_page_size())
            target = min(limit, max(vadj.get_lower(), vadj.get_value() + span[0] - PAGE_GAP))
            if abs(target - vadj.get_value()) > 1.0:
                vadj.set_value(target)
        self._suppress_scroll_sync = False
        self._queue_update()
