"""
Continuous vertical scroll mode: all pages in a virtualized list.

Port of `viewer.js:830-906` (`renderScrollMode`, `updateDominantScrollPage`).
Instead of the web's `IntersectionObserver`, GTK4's `Gtk.ListView` manages
virtualization: only the visible pages (plus immediate buffer) have widgets
allocated and bound.

When an off-screen page is unbound, `unbind` drops its texture
(`unmountPageCanvas` equivalent). The texture remains in `TextureCache` subject
to the LRU byte budget.
"""

from typing import Callable, Dict, List, Optional, Sequence, Tuple

from gi.repository import Gio, GLib, GObject, Graphene, Gsk, Gtk

from ..render import TextureCache
from ..render.service import LANE_PREFETCH, LANE_VISIBLE
from . import paging
from .page_view import PageView

PAGE_GAP = 20


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
        self._submitted: set = set()
        self._scroll_source = 0
        self._suppress_scroll_sync = False

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
        vadjustment.connect("value-changed", self._on_scroll_changed)

    # -- wiring -----------------------------------------------------------

    def attach(self, service, info, overlay_provider=None) -> None:
        self.service = service
        self.info = info
        if overlay_provider is not None:
            self.overlay_provider = overlay_provider
        self._populate_model()
        self.refresh_overlays()
        self.invalidate()

    def shutdown(self) -> None:
        if self._scroll_source:
            GLib.source_remove(self._scroll_source)
            self._scroll_source = 0
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
        sample_page = self.current_page or 1
        pw, ph = self._page_size(sample_page)

        avail_w = max(paging.MIN_VIEWPORT, self.get_width() - 2 * paging.MARGIN)
        avail_h = max(paging.MIN_VIEWPORT, self.get_height() - 2 * paging.MARGIN)

        zoom = paging.fit_scale(
            pw, ph, avail_w, avail_h, self.zoom_mode, self.custom_zoom
        )
        if abs(zoom - self._zoom) > 1e-6:
            self._zoom = zoom
            self._submitted.clear()
            if self.service is not None:
                self.service.bump_generation()
            self.emit("scale-changed", zoom)
            self._update_row_sizes()

    def _update_row_sizes(self) -> None:
        for page, view in self._bound_views.items():
            parent = view.get_parent()
            if isinstance(parent, PageRowWrapper):
                pw, ph = self._page_size(page)
                tw = max(1, int(round(pw * self._zoom)))
                th = max(1, int(round(ph * self._zoom)))
                parent.set_target_size(tw, th)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Gtk.Box.do_size_allocate(self, width, height, baseline)
        self._recompute_scale()

    @property
    def zoom(self) -> float:
        return self._zoom

    def invalidate(self) -> None:
        self._submitted.clear()
        if self.service is not None:
            self.service.bump_generation()
        self._submit_visible()

    def drop_textures(self) -> None:
        self.cache.clear()
        for view in self._bound_views.values():
            view.clear_texture()
        self._submitted.clear()
        self._submit_visible()

    # -- factory callbacks ------------------------------------------------

    def _on_setup(self, _factory, list_item) -> None:
        view = PageView()
        view.connect("activity-activated", self._on_activity_activated)
        view.connect("zoom-toggled", lambda *_: self.emit("zoom-toggled"))
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
        self._check_and_request_render(page, view)

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

    def _submit_visible(self) -> None:
        for page, view in list(self._bound_views.items()):
            self._check_and_request_render(page, view)

    def _check_and_request_render(self, page: int, view: PageView) -> None:
        if self.service is None:
            return
        render_scale = self._zoom * self.get_scale_factor()
        key = (page, round(render_scale, 3), self.rotation % 360, None)
        cached = self.cache.get(key)
        if cached is not None:
            view.set_texture(cached, page, self.rotation)
            return
        if key in self._submitted:
            return
        self._submitted.add(key)
        self.service.submit(
            page, render_scale, self.rotation,
            lane=LANE_VISIBLE, generation=self.service.generation,
        )

    def on_result(self, result) -> None:
        self.cache.put(result.key, result.texture, result.nbytes)
        page, scale, rotation, clip = result.key
        if clip is not None:
            return
        want = round(self._zoom * self.get_scale_factor(), 3)
        if page in self._bound_views and rotation == self.rotation and abs(scale - want) < 1e-6:
            self._bound_views[page].set_texture(result.texture, page, rotation)
            self.emit("page-rendered", page, result.render_ms)

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

    def _on_activity_activated(self, view, target) -> None:
        self.emit("activity-activated", target, view.page)

    # -- navigation & scrolling -------------------------------------------

    def go_to_page(self, page: int) -> None:
        if not self.info or self.info.page_count <= 0:
            return
        page = min(max(1, int(page)), self.info.page_count)
        self.current_page = page
        self._suppress_scroll_sync = True
        self.list_view.scroll_to(page - 1, Gtk.ListScrollFlags.NONE, None)

        def _enable():
            self._suppress_scroll_sync = False
            return GLib.SOURCE_REMOVE

        GLib.idle_add(_enable)

    def _on_scroll_changed(self, vadj) -> None:
        if self._suppress_scroll_sync:
            return
        if self._scroll_source:
            GLib.source_remove(self._scroll_source)
        self._scroll_source = GLib.idle_add(self._sync_dominant_page)

    def _sync_dominant_page(self) -> bool:
        self._scroll_source = 0
        if not self.info or self.info.page_count <= 0 or self._suppress_scroll_sync:
            return GLib.SOURCE_REMOVE

        vadj = self.scroller.get_vadjustment()
        v_top = vadj.get_value()
        v_bot = v_top + vadj.get_page_size()

        best_page = self.current_page
        max_visible = -1.0

        y = 0.0
        for p in range(1, self.info.page_count + 1):
            _pw, ph = self._page_size(p)
            row_h = ph * self._zoom + PAGE_GAP
            p_top = y
            p_bot = y + row_h
            y += row_h

            vis = min(p_bot, v_bot) - max(p_top, v_top)
            if vis > max_visible:
                max_visible = vis
                best_page = p

        if best_page != self.current_page and 1 <= best_page <= self.info.page_count:
            self.current_page = best_page
            self.emit("dominant-page-changed", best_page)
        return GLib.SOURCE_REMOVE
