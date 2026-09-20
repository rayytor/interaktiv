"""
The reader page: chrome around the canvas.

Layout is an `Adw.ToolbarView` with two top bars -- the header with the book's
name, and the board-sized control row under it -- a scrolled canvas, and a
status bar along the bottom. That is the same arrangement `index.html` has, for
the same reason: on a smartboard the controls a teacher touches belong at the
edges of the screen, where they can be found without looking away from the page.

The navigation rules are ported rather than reinvented. `next_page` and
`prev_page` are `viewer.js:626` and `:646`, including book mode's asymmetry --
forward from the cover lands on page 2, backward from anywhere in the first
spread lands on page 1 -- and the zoom steps are the same 0.2 increments
clamped to 0.3-3.0.
"""

from gi.repository import Adw, GLib, GObject, Gtk

from .. import icons
from ..render.service import LANE_THUMBNAIL
from ..theme import THEMES, apply_theme as apply_app_theme
from . import paging
from .focus import FocusOverlay
from .holdrepeat import bind_hold_repeat
from .overlay import activity_label
from .page_view import PageView
from .scroll_mode import ScrollModeView
from .search import SearchController
from .session import DocumentSession
from .sidebar import ActivitiesSidebar, ReaderSidebar
from .spread import SpreadView

ZOOM_CHOICES = [
    ("fit-width", "Genişliğe Sığdır", "Genişliğe Sığdır"),
    ("fit-page", "Sayfaya Sığdır", "Sayfaya Sığdır"),
    ("0.5", "%50", None),
    ("0.75", "%75", None),
    ("1.0", "%100", None),
    ("1.25", "%125", None),
    ("1.5", "%150", None),
    ("2.0", "%200", None),
]

MODE_LABELS = {
    "book": "Çift Sayfa",
    "single": "Tek Sayfa",
    "scroll": "Kaydırma",
}

ZOOM_MIN = 0.3
ZOOM_MAX = 3.0
ZOOM_STEP = 0.2

# What the reader responds to, in the shape `Gtk.ShortcutTrigger` parses. These
# are attached to the page and not to the window, so they are inert while the
# library is on screen and they lose to whatever has the keyboard focus -- which
# is what lets digits reach the page-number entry instead of turning pages.
SHORTCUTS = [
    ("Right|Page_Down|space|j|J|n|N", "next"),
    ("Left|Page_Up|k|K|p|P", "prev"),
    ("Up", "sub-prev"),
    ("Down", "sub-next"),
    ("Home", "first"),
    ("End", "last"),
    ("plus|equal|KP_Add", "zoom-in"),
    ("minus|underscore|KP_Subtract", "zoom-out"),
    ("0|KP_0", "zoom-reset"),
    ("b|B", "mode-book"),
    ("s|S", "mode-single"),
    ("c|C", "mode-scroll"),
    ("r|R", "rotate"),
    ("f|F", "fullscreen"),
    ("a|A", "toggle-activities"),
    ("F9|t|T", "toggle-sidebar"),
    ("m|M", "cycle-theme"),
    ("question", "help"),
    ("Escape", "close"),
]


class ReaderPage(Adw.NavigationPage):
    __gtype_name__ = "InteraktivReaderPage"

    def __init__(self, app, item, path: str):
        super().__init__(title=item.title, tag=f"reader:{item.id}")
        self.app = app
        self.item = item
        self.settings = app.settings
        self.session = DocumentSession(
            item.id, item.title, path,
            manager=app.manager,
            confidence_gate=bool(self.settings.get("link_confidence_gate")),
        )

        self.search_controller = SearchController()
        self.search_controller.connect("results-changed", self._on_search_results_changed)
        self.search_controller.connect("active-match-changed", self._on_search_active_match_changed)
        self.search_controller.connect("search-cleared", self._on_search_cleared)

        self.current_page = 1
        self.view_mode = self.settings.get("view_mode") or "book"
        self.zoom_mode = self.settings.get("zoom_mode") or "fit-page"
        self.custom_zoom = 1.0
        self.rotation = 0
        self.show_activities = bool(self.settings.get("show_activities"))
        self._syncing = False
        self._closed = False
        self._sidebar_loaded = False
        self._pre_focus_state = None

        self._build()
        self._install_shortcuts()
        self.apply_theme(self.settings.get("theme") or "dark")
        self._open()

    # ------------------------------------------------------------------ UI

    def _build(self) -> None:
        self.toast_overlay = Adw.ToastOverlay()
        shell = Adw.ToolbarView()

        self.window_title = Adw.WindowTitle(title=self.item.title, subtitle="")
        header = Adw.HeaderBar()
        self.btn_help = self._icon_button(
            "help-about-symbolic", "Klavye kısayolları (?)", self.show_shortcuts_dialog
        )
        header.pack_end(self.btn_help)

        self.btn_search = self._icon_button(
            icons.SEARCH, "Belgede ara (Ctrl+F)", self.toggle_search
        )
        header.pack_end(self.btn_search)

        self.btn_fullscreen = self._icon_button(
            icons.FULLSCREEN, "Tam Ekran (F)", self._toggle_fullscreen
        )
        header.pack_end(self.btn_fullscreen)

        self.btn_activities = Gtk.ToggleButton(
            icon_name=icons.ACTIVITIES,
            tooltip_text="Etkinlikleri göster (A)",
            active=self.show_activities,
        )
        self.btn_activities.add_css_class("tool-btn")
        self.btn_activities.connect("toggled", self._on_activities_toggled)
        header.pack_end(self.btn_activities)

        self.btn_sidebar = Gtk.ToggleButton(
            icon_name=icons.SIDEBAR,
            tooltip_text="Kenar çubuğu (F9 / T)",
            active=False,
        )
        self.btn_sidebar.add_css_class("tool-btn")
        header.pack_start(self.btn_sidebar)
        shell.add_top_bar(header)
        shell.add_top_bar(self._toolbar())
        shell.add_top_bar(self._search_bar())

        self.spread = SpreadView()
        self.spread.set_mode(self.view_mode if self.view_mode in ("book", "single") else "single")
        self.spread.set_zoom(self.zoom_mode, self.custom_zoom)
        self.spread.connect("scale-changed", self._on_scale_changed)
        self.spread.connect("page-rendered", self._on_page_rendered)
        self.spread.connect("activity-activated", self._on_activity_activated)
        self.spread.set_reveal(self.show_activities)

        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True,
            vexpand=True,
        )
        self.scroller.add_css_class("reader-canvas")
        self.scroller.set_child(self.spread)

        self.scroll_view = ScrollModeView(cache=self.spread.cache)
        self.scroll_view.set_zoom(self.zoom_mode, self.custom_zoom)
        self.scroll_view.connect("scale-changed", self._on_scale_changed)
        self.scroll_view.connect("page-rendered", self._on_page_rendered)
        self.scroll_view.connect("activity-activated", self._on_activity_activated)
        self.scroll_view.connect("dominant-page-changed", self._on_dominant_page_changed)
        self.scroll_view.set_reveal(self.show_activities)

        self.canvas_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        self.canvas_stack.add_named(self.scroller, "spread")
        self.canvas_stack.add_named(self.scroll_view, "scroll")
        self.canvas_stack.set_visible_child_name(
            "scroll" if self.view_mode == "scroll" else "spread"
        )

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._loading_page(), "loading")
        self.stack.add_named(self.canvas_stack, "canvas")
        self.stack.set_visible_child_name("loading")

        self.sidebar = ReaderSidebar()
        self.sidebar.connect("page-chosen", lambda _w, p: self.go_to_page(p))
        self.sidebar.connect("activity-chosen", self._on_activity_chosen)

        # The list overlays the page rather than squeezing it: on a board the
        # page is the point, and a sidebar that reflowed the spread every time
        # it opened would re-render both sheets for the sake of a list.
        self.split = Adw.OverlaySplitView(
            sidebar=self.sidebar,
            content=self.stack,
            show_sidebar=bool(self.settings.get("sidebar_open")),
            sidebar_width_fraction=0.16,
            min_sidebar_width=200,
            max_sidebar_width=232,
        )
        self.split.bind_property(
            "show-sidebar", self.btn_sidebar, "active",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        # The list is built the first time it is asked for, not at open: for a
        # book with ninety baked sheets it is real work, and most lessons never
        # open it at all.
        self.split.connect("notify::show-sidebar", self._on_sidebar_shown)
        shell.set_content(self.split)
        shell.add_bottom_bar(self._status_bar())

        self.focus_overlay = FocusOverlay(self)
        self.focus_overlay.set_visible(False)

        overlay = Gtk.Overlay()
        overlay.set_child(shell)
        overlay.add_overlay(self.focus_overlay)

        self.toast_overlay.set_child(overlay)
        self.set_child(self.toast_overlay)

    def _loading_page(self) -> Gtk.Widget:
        page = Adw.StatusPage(
            title="Kitap açılıyor…",
            description="Büyük kitaplarda bu birkaç saniye sürebilir.",
            vexpand=True,
        )
        page.set_child(Adw.Spinner(width_request=42, height_request=42))
        self.status_page = page
        return page

    def _toolbar(self) -> Gtk.Widget:
        bar = Gtk.CenterBox()
        bar.add_css_class("reader-toolbar")

        # -- centre: paging and view mode
        centre = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.btn_prev = self._icon_button(icons.PREV_PAGE, "Önceki Sayfa (←)", None)
        self.btn_prev.add_css_class("page-turn")
        bind_hold_repeat(self.btn_prev, self.prev_page)

        self.btn_next = self._icon_button(icons.NEXT_PAGE, "Sonraki Sayfa (→)", None)
        self.btn_next.add_css_class("page-turn")
        bind_hold_repeat(self.btn_next, self.next_page)

        self.page_entry = Gtk.SpinButton.new_with_range(1, 1, 1)
        self.page_entry.set_numeric(True)
        self.page_entry.set_width_chars(4)
        # Centred, so the number sits in the same place whether the book is on
        # page 7 or page 289 -- it is read at a glance from across the room.
        self.page_entry.set_alignment(0.5)
        self.page_entry.add_css_class("page-entry")
        self.page_entry.set_tooltip_text("Sayfa numarası")
        self.page_entry.connect("value-changed", self._on_page_entry)

        self.page_total = Gtk.Label(label="/ …")
        self.page_total.add_css_class("page-total")

        centre.append(self.btn_prev)
        centre.append(self.page_entry)
        centre.append(self.page_total)
        centre.append(self.btn_next)
        centre.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.mode_group = Adw.ToggleGroup()
        self.mode_group.add(Adw.Toggle(
            name="book", label=MODE_LABELS["book"], icon_name=icons.MODE_BOOK,
            tooltip="Çift sayfa görünümü (B)",
        ))
        self.mode_group.add(Adw.Toggle(
            name="single", label=MODE_LABELS["single"], icon_name=icons.MODE_SINGLE,
            tooltip="Tek sayfa görünümü (S)",
        ))
        scroll_toggle = Adw.Toggle(
            name="scroll", label=MODE_LABELS["scroll"], icon_name=icons.MODE_SCROLL,
            tooltip="Sürekli kaydırma görünümü (C)",
        )
        self.mode_group.add(scroll_toggle)
        self.mode_group.set_active_name(self.view_mode)
        self.mode_group.connect("notify::active-name", self._on_mode_toggled)
        centre.append(self.mode_group)
        bar.set_center_widget(centre)

        # -- right: zoom and rotation
        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        right.append(self._icon_button(icons.ZOOM_OUT, "Uzaklaştır (-)", lambda: self.step_zoom(-ZOOM_STEP)))

        self.zoom_list = Gtk.StringList.new([c[1] for c in ZOOM_CHOICES])
        self.zoom_drop = Gtk.DropDown(model=self.zoom_list)
        self.zoom_drop.set_tooltip_text("Yakınlaştırma")
        self.zoom_drop.set_selected(self._zoom_index())
        self.zoom_drop.connect("notify::selected", self._on_zoom_selected)
        right.append(self.zoom_drop)

        right.append(self._icon_button(icons.ZOOM_IN, "Yakınlaştır (+)", lambda: self.step_zoom(ZOOM_STEP)))
        right.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        right.append(self._icon_button(icons.ROTATE, "Döndür (R)", self.rotate))
        bar.set_end_widget(right)

        return bar

    def _search_bar(self) -> Gtk.Widget:
        bar = Gtk.SearchBar()
        bar.set_key_capture_widget(self)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("reader-search-box")

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Belgede ara…")
        self.search_entry.set_hexpand(True)
        bar.connect_entry(self.search_entry)
        self.search_entry.connect("activate", self._on_search_entry_activate)
        self.search_entry.connect("search-changed", self._on_search_entry_changed)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_search_key_pressed)
        self.search_entry.add_controller(key_ctrl)

        self.search_count_label = Gtk.Label(label="0 of 0")
        self.search_count_label.add_css_class("search-match-count")

        self.btn_search_prev = self._icon_button(
            "go-up-symbolic", "Önceki eşleşme (Shift+Enter)", self.prev_search_match
        )
        self.btn_search_next = self._icon_button(
            "go-down-symbolic", "Sonraki eşleşme (Enter)", self.next_search_match
        )
        self.btn_search_close = self._icon_button(
            icons.CANCEL, "Aramayı kapat (Esc)", self.close_search
        )

        box.append(self.search_entry)
        box.append(self.search_count_label)
        box.append(self.btn_search_prev)
        box.append(self.btn_search_next)
        box.append(self.btn_search_close)

        bar.set_child(box)
        self.search_bar = bar
        return bar

    def _status_bar(self) -> Gtk.Widget:
        bar = Gtk.ActionBar()
        bar.add_css_class("reader-status")

        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_size = Gtk.Label(label="")
        self.status_render = Gtk.Label(label="Hazır")
        self.status_activity = Gtk.Label(label="")
        for label in (self.status_size, self.status_render, self.status_activity):
            label.add_css_class("status-item")
        self.status_activity.add_css_class("status-activity")
        left.append(self.status_size)
        left.append(Gtk.Label(label="•", css_classes=["status-sep"]))
        left.append(self.status_render)
        left.append(Gtk.Label(label="•", css_classes=["status-sep"]))
        left.append(self.status_activity)
        bar.pack_start(left)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_zoom = Gtk.Label(label="")
        self.status_mode = Gtk.Label(label=MODE_LABELS[self.view_mode])
        for label in (self.status_zoom, self.status_mode):
            label.add_css_class("status-item")
        right.append(self.status_zoom)
        right.append(Gtk.Label(label="•", css_classes=["status-sep"]))
        right.append(self.status_mode)
        bar.pack_end(right)
        return bar

    def _icon_button(self, icon_name, tooltip, action) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip)
        button.add_css_class("tool-btn")
        if action is not None:
            button.connect("clicked", lambda *_: action())
        return button

    # ------------------------------------------------------------ opening

    def _open(self) -> None:
        self.session.open(
            on_ready=self._on_ready,
            on_result=self._on_result,
            on_error=self._on_error,
            on_pressure=self._on_pressure,
        )

    def _on_ready(self, info) -> None:
        if self._closed:
            return
        total = info.page_count
        self.page_entry.set_range(1, max(1, total))
        self.page_total.set_label(f"/ {total}")

        restored = min(max(self.settings.last_page(self.item.id), 1), max(1, total))
        self.current_page = restored
        self.spread.attach(self.session.service, info, self.session.overlay)
        self.scroll_view.attach(self.session.service, info, self.session.overlay)
        self.sidebar.load_book(info, self.session.service, self.rotation, self.view_mode)
        self.stack.set_visible_child_name("canvas")
        if self.view_mode == "scroll":
            self.canvas_stack.set_visible_child_name("scroll")
            self.scroll_view.go_to_page(restored)
            self._sync_page_entry()
            self._update_nav_buttons()
            self._update_status_size()
            self._update_zoom_status()
            self._update_activity_status()
            self.sidebar.update_active_page(self.current_page)
        else:
            self.canvas_stack.set_visible_child_name("spread")
            self._apply_page(restored, force=True)
            self._update_status_size()
            self._update_zoom_status()
            self._update_activity_status()
            self.sidebar.update_active_page(self.current_page)
        self.search_controller.attach(self.session.service, info)
        self.session.check_bake(self._on_bake_changed)

    def _on_bake_changed(self) -> None:
        """
        The bake was replaced under us. Everything derived from it is stale:
        the overlays on screen and the list in the sidebar both come again.
        """
        if self._closed:
            return
        self.spread.refresh_overlays()
        self.scroll_view.refresh_overlays()
        self._update_activity_status()
        if self.session.bake_state == "baking":
            self.toast_overlay.add_toast(Adw.Toast(title="Etkinlikler hazırlanıyor…"))
            return
        self._sidebar_loaded = False
        if self.split.get_show_sidebar():
            self._load_sidebar()

    def _on_error(self, message: str) -> None:
        """
        The book did not open. Say so in the teacher's language and keep the
        renderer's own words underneath -- they name the actual cause, and a
        lesson that has already started is not the moment to hide it.
        """
        if self._closed:
            return
        self.status_page.set_child(None)
        self.status_page.set_icon_name("dialog-warning-symbolic")
        self.status_page.set_title("Kitap açılamadı")
        self.status_page.set_description(
            "Dosya bozuk olabilir veya taşınmış olabilir. "
            "Kitaplıktan kaldırıp yeniden indirmeyi deneyin.\n\n"
            f"{message}"
        )

    def _on_result(self, result) -> None:
        if self._closed:
            return
        if result.request.lane == LANE_THUMBNAIL:
            self.sidebar.on_thumbnail_result(result)
            return
        if self.focus_overlay.is_active and result.request.clip is not None:
            self.focus_overlay.on_result(result)
            return
        if self.view_mode == "scroll":
            self.scroll_view.on_result(result)
        else:
            self.spread.on_result(result)

    def _on_pressure(self, rss_mb: float) -> None:
        """
        The render thread reports the process is heavier than a board can carry.
        Give the textures back; they cost one re-render each and nothing else.
        """
        if self._closed:
            return
        self.spread.drop_textures()
        self.scroll_view.drop_textures()

    # --------------------------------------------------------- navigation

    def go_to_page(self, page: int) -> None:
        total = self.session.page_count
        if total <= 0:
            return
        page = min(max(int(page), 1), total)
        if self.view_mode == "scroll":
            self.current_page = page
            self.scroll_view.go_to_page(page)
            self._sync_page_entry()
            self._update_nav_buttons()
            self._update_status_size()
            self._update_activity_status()
            self.sidebar.update_active_page(self.current_page)
            self.settings.set_last_page(self.item.id, self.current_page)
            return
        if self.view_mode == "book" and page in self.spread.pages:
            return
        if self.view_mode != "book" and page == self.current_page:
            return
        self._apply_page(page)

    def _apply_page(self, page: int, force: bool = False) -> None:
        pages = self.spread.pages_for(page)
        # Book mode snaps to the left page of the spread, exactly as
        # `renderBookMode` rewrites `currentPage` before it draws.
        self.current_page = pages[0]
        self.spread.show_pages(pages)
        self._sync_page_entry()
        self._update_nav_buttons()
        self._update_status_size()
        self._update_activity_status()
        self.sidebar.update_active_page(self.current_page)
        self.settings.set_last_page(self.item.id, self.current_page)

    def _on_dominant_page_changed(self, _scroll_view, page: int) -> None:
        if self.view_mode != "scroll":
            return
        self.current_page = page
        self._sync_page_entry()
        self._update_nav_buttons()
        self._update_status_size()
        self._update_activity_status()
        self.sidebar.update_active_page(self.current_page)
        self.settings.set_last_page(self.item.id, self.current_page)

    def next_page(self) -> None:
        if self.view_mode == "scroll":
            self.go_to_page(self.current_page + 1)
        else:
            self.go_to_page(paging.next_page(
                self.current_page, self.session.page_count, self.view_mode,
                self.spread.pages,
            ))

    def prev_page(self) -> None:
        if self.view_mode == "scroll":
            self.go_to_page(self.current_page - 1)
        else:
            self.go_to_page(paging.prev_page(
                self.current_page, self.session.page_count, self.view_mode
            ))

    def _update_nav_buttons(self) -> None:
        total = self.session.page_count
        if self.view_mode == "scroll":
            self.btn_prev.set_sensitive(self.current_page > 1)
            self.btn_next.set_sensitive(self.current_page < total)
        else:
            pages = self.spread.pages
            self.btn_prev.set_sensitive(self.current_page > 1)
            self.btn_next.set_sensitive(bool(pages) and total not in pages)

    def _sync_page_entry(self) -> None:
        self._syncing = True
        self.page_entry.set_value(self.current_page)
        self._syncing = False

    def _on_page_entry(self, spin) -> None:
        if self._syncing:
            return
        self.go_to_page(int(spin.get_value()))

    # ---------------------------------------------------------- view mode

    def set_view_mode(self, mode: str) -> None:
        if mode == self.view_mode or mode not in ("book", "single", "scroll"):
            return
        self.view_mode = mode
        self.settings.set("view_mode", mode)
        self.status_mode.set_label(MODE_LABELS[mode])
        if self.mode_group.get_active_name() != mode:
            self.mode_group.set_active_name(mode)
        self.sidebar.set_view_mode(mode)
        if mode == "scroll":
            self.canvas_stack.set_visible_child_name("scroll")
            self.scroll_view.go_to_page(self.current_page)
            self._sync_page_entry()
            self._update_nav_buttons()
            self._update_status_size()
            self._update_activity_status()
            self.sidebar.update_active_page(self.current_page)
        else:
            self.canvas_stack.set_visible_child_name("spread")
            self.spread.set_mode(mode)
            self._apply_page(self.current_page, force=True)

    def _on_mode_toggled(self, group, _param) -> None:
        name = group.get_active_name()
        if name in ("book", "single", "scroll"):
            self.set_view_mode(name)

    # --------------------------------------------------------------- zoom

    def _zoom_index(self):
        """
        Which row of the dropdown the current zoom is, or None if it is a value
        between the rungs of the ladder -- which `+` and `-` produce constantly.
        """
        for index, (value, _label, _status) in enumerate(ZOOM_CHOICES):
            if self.zoom_mode == "custom":
                if value not in ("fit-width", "fit-page") and abs(
                    float(value) - self.custom_zoom
                ) < 1e-6:
                    return index
            elif value == self.zoom_mode:
                return index
        return None

    def set_zoom(self, mode: str, custom: float = None) -> None:
        self.zoom_mode = mode
        if custom is not None:
            self.custom_zoom = custom
        if mode in ("fit-width", "fit-page"):
            self.settings.set("zoom_mode", mode)
        self.spread.set_zoom(mode, self.custom_zoom)
        self.scroll_view.set_zoom(mode, self.custom_zoom)
        self._sync_zoom_drop()
        self._update_zoom_status()

    def step_zoom(self, delta: float) -> None:
        # The web starts stepping from 100 %, not from whatever the fit happens
        # to be, so that +/- always lands on the same ladder.
        current = self.custom_zoom if self.zoom_mode == "custom" else 1.0
        self.set_zoom("custom", min(ZOOM_MAX, max(ZOOM_MIN, current + delta)))

    def _sync_zoom_drop(self) -> None:
        """
        Keep the dropdown honest about a zoom it has no row for.

        Stepping with `+` lands on 110 %, 130 %, 170 % -- none of which is on
        the list. The web `<select>` simply goes blank in that case; here the
        odd value is appended as a row of its own and taken away again as soon
        as the zoom returns to the ladder, so the control always reads back
        what the page is actually at.
        """
        self._syncing = True
        index = self._zoom_index()
        extra = self.zoom_list.get_n_items() > len(ZOOM_CHOICES)
        if index is None:
            label = f"%{round(self.custom_zoom * 100)}"
            if extra:
                self.zoom_list.splice(len(ZOOM_CHOICES), 1, [label])
            else:
                self.zoom_list.append(label)
            self.zoom_drop.set_selected(len(ZOOM_CHOICES))
        else:
            if extra:
                self.zoom_list.splice(len(ZOOM_CHOICES), 1, None)
            self.zoom_drop.set_selected(index)
        self._syncing = False

    def _on_zoom_selected(self, drop, _param) -> None:
        if self._syncing:
            return
        selected = drop.get_selected()
        if selected >= len(ZOOM_CHOICES):
            return
        value = ZOOM_CHOICES[selected][0]
        if value in ("fit-width", "fit-page"):
            self.set_zoom(value)
        else:
            self.set_zoom("custom", float(value))

    def _on_scale_changed(self, _spread, zoom: float) -> None:
        self._update_zoom_status(zoom)

    def _update_zoom_status(self, zoom: float = None) -> None:
        """
        In a fit mode the label is the mode's own name and the number does not
        matter. In custom mode the number is `custom_zoom` and not
        `spread.zoom`: the spread only learns the new scale during its next
        allocation, so reading it here would show the teacher the zoom they
        just left.
        """
        if self.zoom_mode == "custom":
            self.status_zoom.set_label(f"%{round(self.custom_zoom * 100)}")
            return
        for value, _label, status in ZOOM_CHOICES:
            if value == self.zoom_mode and status:
                self.status_zoom.set_label(status)
                return
        self.status_zoom.set_label(f"%{round((zoom or self.spread.zoom) * 100)}")

    # ------------------------------------------------------------- rotate

    def rotate(self) -> None:
        self.rotation = (self.rotation + 90) % 360
        self.spread.set_rotation(self.rotation)
        self.scroll_view.set_rotation(self.rotation)
        self.sidebar.set_rotation(self.rotation)
        self._update_status_size()

    # ------------------------------------------------------------- status

    def _update_status_size(self) -> None:
        if not self.session.is_open:
            return
        if self.view_mode == "scroll":
            first = self.session.info.size(self.current_page)
            text = f"{round(first[0])} × {round(first[1])} pt"
        else:
            pages = self.spread.pages or [self.current_page]
            first = self.session.info.size(pages[0])
            if len(pages) > 1:
                spread_w = sum(self.session.info.size(p)[0] for p in pages)
                spread_h = max(self.session.info.size(p)[1] for p in pages)
                text = (f"{round(first[0])} × {round(first[1])} pt"
                        f"  (açık: {round(spread_w)} × {round(spread_h)} pt)")
            else:
                text = f"{round(first[0])} × {round(first[1])} pt"
        self.status_size.set_label(text)
        self.window_title.set_subtitle(
            f"Sayfa {self.current_page} / {self.session.page_count}"
        )

    def _on_page_rendered(self, _spread, page: int, ms: int) -> None:
        self.status_render.set_label(f"{ms} ms'de çizildi")

    # --------------------------------------------------------- activities

    def _on_activities_toggled(self, button) -> None:
        self.show_activities = button.get_active()
        self.settings.set("show_activities", self.show_activities)
        self.spread.set_reveal(self.show_activities)
        self.scroll_view.set_reveal(self.show_activities)

    def _on_sidebar_shown(self, *_args) -> None:
        show = self.split.get_show_sidebar()
        self.settings.set("sidebar_open", show)
        if show:
            self._load_sidebar()

    def _load_sidebar(self) -> None:
        if self._sidebar_loaded or not self.session.is_open:
            return
        self._sidebar_loaded = True
        self.sidebar.load_activities(self.session.activity_summaries())

    def _on_activity_activated(self, _spread, target, page: int) -> None:
        """
        A hotspot or a pin was pressed on the page itself.

        A region is selected -- both its pieces light up and the list follows
        it. A pin has no region and so no row to follow, so it names itself in
        the status bar instead. Non-interactive regions open focus mode.
        """
        if self._closed:
            return
        from .overlay import Pin

        if isinstance(target, Pin):
            self.spread.clear_selection()
            self.scroll_view.clear_selection()
            self.status_activity.set_label(f"⚡ {target.label}")
            if target.oge and target.oge.guid:
                where = target.oge.printed_page or page
                self.open_activity_dialog(
                    guid=target.oge.guid,
                    title=f"Sayfa {where} · {target.label}",
                    installed=bool(target.oge.is_installed),
                )
            return
        if self.view_mode == "scroll":
            self.scroll_view.select_activity(page, target.act_index)
        else:
            self.spread.select_activity(page, target.act_index)
        self.sidebar.select_activity(page, target.act_index)
        self._show_activity(target.label, target.item_count, target.interactive)
        if target.interactive and target.guid:
            self.open_activity_dialog(
                guid=target.guid,
                title=f"Sayfa {page} · {target.label}",
                installed=target.installed,
            )
        elif not target.interactive and target.activity is not None:
            self.focus_activity(target.activity, part_index=target.part_index)

    def open_activity_dialog(
        self, guid: str, title: str = "", installed: bool = False
    ) -> None:
        """Open the interactive activity modal dialog for a given GUID."""
        if not guid or self._closed:
            return
        from .activity_dialog import ActivityDialog

        dialog = ActivityDialog(
            manager=self.session.manager or getattr(self.app, "manager", None),
            guid=guid,
            title=title,
            installed=installed,
        )
        dialog.present(self)

    # ------------------------------------------------------------ focus mode

    def focus_activity(
        self, activity, part_index: int = 0, sub_index: int = -1
    ) -> None:
        """Zoom into an activity part or question in focus mode."""
        if not activity or self._closed:
            return
        if not self.focus_overlay.is_active:
            scroller = self.scroll_view.scroller if self.view_mode == "scroll" else self.scroller
            self._pre_focus_state = {
                "view_mode": self.view_mode,
                "current_page": self.current_page,
                "zoom_mode": self.zoom_mode,
                "custom_zoom": self.custom_zoom,
                "hadj": scroller.get_hadjustment().get_value(),
                "vadj": scroller.get_vadjustment().get_value(),
            }
        self.focus_overlay.start(activity, part_index=part_index, sub_index=sub_index)
        name = activity_label(activity) or activity.name()
        self.status_mode.set_label(f"Odak: Sayfa {activity.page_num} · {name}")

    def exit_focus(self, silent: bool = False) -> None:
        """Exit focus mode and restore pre-focus state."""
        if not self.focus_overlay.is_active:
            return
        self.focus_overlay.clear()
        prev = self._pre_focus_state
        self._pre_focus_state = None

        if not silent and prev is not None:
            if prev["view_mode"] != self.view_mode:
                self.set_view_mode(prev["view_mode"])
            if prev["current_page"] != self.current_page:
                self.go_to_page(prev["current_page"])
            if prev["zoom_mode"] != self.zoom_mode or prev["custom_zoom"] != self.custom_zoom:
                self.set_zoom(prev["zoom_mode"], prev["custom_zoom"])

            hadj = prev["hadj"]
            vadj = prev["vadj"]

            def _restore_scroll():
                scroller = self.scroll_view.scroller if self.view_mode == "scroll" else self.scroller
                scroller.get_hadjustment().set_value(hadj)
                scroller.get_vadjustment().set_value(vadj)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_restore_scroll)

        self.status_mode.set_label(MODE_LABELS.get(self.view_mode, ""))
        self._update_activity_status()
        self._update_zoom_status()

    def step_activity_from(self, activity, direction: int):
        """
        Next / previous activity across page boundaries, in reading order.
        direction is +1 or -1. Port of `viewer.js:1896-1909`.
        """
        overlay = self.session.overlay(activity.page_num)
        acts = list(overlay.activities) if overlay else []
        idx = next((i for i, a in enumerate(acts) if a.id == activity.id), -1)
        target = idx + direction
        if idx != -1 and 0 <= target < len(acts):
            return acts[target]

        start_p = activity.page_num + direction
        end_p = (self.session.page_count + 1) if direction > 0 else 0
        for p in range(start_p, end_p, direction):
            ov = self.session.overlay(p)
            if ov and ov.activities:
                return ov.activities[0] if direction > 0 else ov.activities[-1]
        return None

    def _on_activity_chosen(self, _sidebar, page: int, act_index: int) -> None:
        """A row was picked: go to its sheet and light the activity up there."""
        if self._closed:
            return
        self.go_to_page(page)
        if self.view_mode == "scroll":
            self.scroll_view.select_activity(page, act_index)
        else:
            self.spread.select_activity(page, act_index)
        overlay = self.session.overlay(page)
        spot = overlay.first_spot_of(act_index) if overlay else None
        if spot is not None:
            self._show_activity(spot.label, spot.item_count, spot.interactive)

    def _show_activity(self, label: str, items: int, interactive: bool) -> None:
        name = label or "Etkinlik"
        parts = [f"⚡ {name}" if interactive else name]
        if items:
            parts.append(f"{items} soru")
        self.status_activity.set_label(" · ".join(parts))

    def _update_activity_status(self) -> None:
        """
        How many activities are on what is currently open.

        Counted from the overlays rather than from the bake, so it says what is
        actually drawn -- a sheet whose dimensions disagree with its bake has
        its overlay dropped, and the count has to drop with it.
        """
        if not self.session.is_open:
            return
        if not self.session.activities_ready:
            self.status_activity.set_label(
                "Etkinlik bilgisi yok" if self.session.bake_state != "baking" else ""
            )
            return
        total = 0
        pins = 0
        pages = [self.current_page] if self.view_mode == "scroll" else (self.spread.pages or [self.current_page])
        for page in pages:
            overlay = self.session.overlay(page)
            if overlay is None:
                continue
            total += len(overlay.activities)
            pins += len(overlay.pins)
        if not total and not pins:
            self.status_activity.set_label("Bu sayfada etkinlik yok")
        elif pins:
            self.status_activity.set_label(f"{total} etkinlik · {pins} ⚡")
        else:
            self.status_activity.set_label(f"{total} etkinlik")

    def toggle_activities(self) -> None:
        self.btn_activities.set_active(not self.btn_activities.get_active())

    def toggle_sidebar(self) -> None:
        self.split.set_show_sidebar(not self.split.get_show_sidebar())

    # ------------------------------------------------------------- search

    def toggle_search(self) -> None:
        if self.search_bar.get_search_mode():
            self.close_search()
        else:
            self.open_search()

    def open_search(self) -> None:
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()
        text = self.search_entry.get_text()
        if text:
            self.search_entry.select_region(0, -1)

    def close_search(self) -> None:
        self.search_bar.set_search_mode(False)
        self.search_controller.clear()
        self.spread.set_search_matches({}, None)
        self.scroll_view.set_search_matches({}, None)
        self.search_count_label.set_label("0 of 0")
        self.grab_focus()

    def execute_search(self, query: str) -> None:
        visible = self.spread.pages if self.view_mode == "book" else [self.current_page]
        self.search_count_label.set_label("Aranıyor…")
        self.search_controller.execute_search(query, visible_pages=visible)

    def next_search_match(self) -> None:
        self.search_controller.next_match()

    def prev_search_match(self) -> None:
        self.search_controller.prev_match()

    def _on_search_key_pressed(self, controller, keyval, keycode, state) -> bool:
        from gi.repository import Gdk
        if keyval == Gdk.KEY_Escape:
            self.close_search()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if state & Gdk.ModifierType.SHIFT_MASK:
                self.prev_search_match()
                return True
        return False

    def _on_search_entry_activate(self, entry) -> None:
        text = entry.get_text().strip()
        if not text:
            return
        if self.search_controller.query.lower() == text.lower() and self.search_controller.total_matches > 0:
            self.next_search_match()
        else:
            self.execute_search(text)

    def _on_search_entry_changed(self, entry) -> None:
        text = entry.get_text().strip()
        if not text:
            self.search_controller.clear()
            self.spread.set_search_matches({}, None)
            self.scroll_view.set_search_matches({}, None)
            self.search_count_label.set_label("0 of 0")

    def _on_search_results_changed(self, _ctrl, total: int) -> None:
        if total == 0:
            if self.search_controller.query:
                self.search_count_label.set_label("0 eşleşme")
            else:
                self.search_count_label.set_label("0 of 0")
            self.spread.set_search_matches({}, None)
            self.scroll_view.set_search_matches({}, None)
            return
        curr = self.search_controller.current_match_index
        self.search_count_label.set_label(f"{curr + 1} / {total}")
        page_matches = self.search_controller.all_page_matches()
        active = None
        if self.search_controller.current_match:
            cm = self.search_controller.current_match
            active = (cm.page, cm.match_index)
        self.spread.set_search_matches(page_matches, active)
        self.scroll_view.set_search_matches(page_matches, active)

    def _on_search_active_match_changed(self, _ctrl, index: int, match) -> None:
        total = self.search_controller.total_matches
        self.search_count_label.set_label(f"{index + 1} / {total}")
        if match is not None:
            if self.view_mode == "book":
                if match.page not in self.spread.pages:
                    self.go_to_page(match.page)
            else:
                if match.page != self.current_page:
                    self.go_to_page(match.page)
        page_matches = self.search_controller.all_page_matches()
        active = (match.page, match.match_index) if match else None
        self.spread.set_search_matches(page_matches, active)
        self.scroll_view.set_search_matches(page_matches, active)

    def _on_search_cleared(self, _ctrl) -> None:
        self.search_count_label.set_label("0 of 0")
        self.spread.set_search_matches({}, None)
        self.scroll_view.set_search_matches({}, None)

    # ---------------------------------------------------------- shortcuts

    def show_shortcuts_dialog(self) -> None:
        dialog = Adw.ShortcutsDialog()
        dialog.set_title("Klavye Kısayolları")

        nav = Adw.ShortcutsSection(title="Gezinme")
        nav.add(Adw.ShortcutsItem(title="Sonraki sayfa / açık", accelerator="Right"))
        nav.add(Adw.ShortcutsItem(title="Önceki sayfa / açık", accelerator="Left"))
        nav.add(Adw.ShortcutsItem(title="İlk sayfa", accelerator="Home"))
        nav.add(Adw.ShortcutsItem(title="Son sayfa", accelerator="End"))
        dialog.add(nav)

        view = Adw.ShortcutsSection(title="Görünüm ve Yakınlaştırma")
        view.add(Adw.ShortcutsItem(title="Yakınlaştır", accelerator="plus"))
        view.add(Adw.ShortcutsItem(title="Uzaklaştır", accelerator="minus"))
        view.add(Adw.ShortcutsItem(title="Sayfaya sığdır", accelerator="0"))
        view.add(Adw.ShortcutsItem(title="Çift sayfa görünümü", accelerator="b"))
        view.add(Adw.ShortcutsItem(title="Tek sayfa görünümü", accelerator="s"))
        view.add(Adw.ShortcutsItem(title="Kaydırma görünümü", accelerator="c"))
        view.add(Adw.ShortcutsItem(title="Saat yönünde döndür", accelerator="r"))
        view.add(Adw.ShortcutsItem(title="Tam ekran", accelerator="f"))
        dialog.add(view)

        tools = Adw.ShortcutsSection(title="Araçlar")
        tools.add(Adw.ShortcutsItem(title="Belgede ara", accelerator="<Control>f"))
        tools.add(Adw.ShortcutsItem(title="Kenar çubuğunu aç/kapat", accelerator="t"))
        tools.add(Adw.ShortcutsItem(title="Okuma temasını değiştir", accelerator="m"))
        tools.add(Adw.ShortcutsItem(title="Etkinlikleri göster/gizle", accelerator="a"))
        tools.add(Adw.ShortcutsItem(title="Klavye kısayolları", accelerator="question"))
        dialog.add(tools)

        focus = Adw.ShortcutsSection(title="Odak Modu")
        focus.add(Adw.ShortcutsItem(title="Sonraki etkinlik", accelerator="Right"))
        focus.add(Adw.ShortcutsItem(title="Önceki etkinlik", accelerator="Left"))
        focus.add(Adw.ShortcutsItem(title="Sonraki soru", accelerator="Down"))
        focus.add(Adw.ShortcutsItem(title="Önceki soru", accelerator="Up"))
        focus.add(Adw.ShortcutsItem(title="Odaktan çık", accelerator="Escape"))
        dialog.add(focus)

        dialog.present(self)

    def cycle_theme(self) -> None:
        themes = THEMES
        current = self.settings.get("theme") or "dark"
        next_idx = (themes.index(current) + 1) % len(themes) if current in themes else 0
        new_theme = themes[next_idx]
        self.settings.set("theme", new_theme)
        self.apply_theme(new_theme)

    def apply_theme(self, theme: str) -> None:
        PageView.set_global_theme(theme)
        window = self.get_root()
        if window is not None and hasattr(window, "apply_theme"):
            window.apply_theme(theme)
        else:
            apply_app_theme(theme, window)
        self.spread.queue_draw()
        self.scroll_view.queue_draw()
        self.focus_overlay.queue_draw()

    def _install_shortcuts(self) -> None:
        def _on_next():
            if self.focus_overlay.is_active:
                self.focus_overlay.step_activity(1)
            else:
                self.next_page()

        def _on_prev():
            if self.focus_overlay.is_active:
                self.focus_overlay.step_activity(-1)
            else:
                self.prev_page()

        def _on_sub_prev():
            if self.focus_overlay.is_active:
                self.focus_overlay.step_sub_item(-1)

        def _on_sub_next():
            if self.focus_overlay.is_active:
                self.focus_overlay.step_sub_item(1)

        def _on_zoom_in():
            if self.focus_overlay.is_active:
                self.focus_overlay.nudge_zoom(0.2)
            else:
                self.step_zoom(ZOOM_STEP)

        def _on_zoom_out():
            if self.focus_overlay.is_active:
                self.focus_overlay.nudge_zoom(-0.2)
            else:
                self.step_zoom(-ZOOM_STEP)

        def _on_zoom_reset():
            if self.focus_overlay.is_active:
                self.focus_overlay.reset_zoom()
            else:
                self.set_zoom("fit-page")

        def _on_close():
            if self.focus_overlay.is_active:
                self.exit_focus()
            elif self.search_bar.get_search_mode():
                self.close_search()
            else:
                self._close_book()

        def _ignore_during_focus(action):
            return lambda: action() if not self.focus_overlay.is_active else None

        actions = {
            "next": _on_next,
            "prev": _on_prev,
            "sub-prev": _on_sub_prev,
            "sub-next": _on_sub_next,
            "first": _ignore_during_focus(lambda: self.go_to_page(1)),
            "last": _ignore_during_focus(lambda: self.go_to_page(self.session.page_count)),
            "zoom-in": _on_zoom_in,
            "zoom-out": _on_zoom_out,
            "zoom-reset": _on_zoom_reset,
            "mode-book": _ignore_during_focus(lambda: self.set_view_mode("book")),
            "mode-single": _ignore_during_focus(lambda: self.set_view_mode("single")),
            "mode-scroll": _ignore_during_focus(lambda: self.set_view_mode("scroll")),
            "rotate": _ignore_during_focus(self.rotate),
            "fullscreen": self._toggle_fullscreen,
            "toggle-activities": _ignore_during_focus(self.toggle_activities),
            "toggle-sidebar": _ignore_during_focus(self.toggle_sidebar),
            "cycle-theme": _ignore_during_focus(self.cycle_theme),
            "help": self.show_shortcuts_dialog,
            "close": _on_close,
        }
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.LOCAL)
        for trigger, name in SHORTCUTS:
            action = actions[name]
            controller.add_shortcut(Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string(trigger),
                action=Gtk.CallbackAction.new(
                    lambda _w, _a, fn=action: (fn(), True)[1]
                ),
            ))
        self.add_controller(controller)
        self.set_focusable(True)

    def _toggle_fullscreen(self) -> None:
        window = self.get_root()
        if window is None:
            return
        if window.is_fullscreen():
            window.unfullscreen()
        else:
            window.fullscreen()

    def _close_book(self) -> None:
        window = self.get_root()
        if window is not None and hasattr(window, "navigation"):
            window.navigation.pop()

    # ----------------------------------------------------------- teardown

    def shutdown(self) -> None:
        """
        Called when the page is popped. The render thread and the textures both
        go now rather than when Python happens to collect the page: a book left
        open is half a gigabyte of MuPDF store on a machine that has two.
        """
        if self._closed:
            return
        self._closed = True
        self.settings.set_last_page(self.item.id, self.current_page)
        self.focus_overlay.clear()
        self.search_controller.shutdown()
        self.sidebar.shutdown()
        self.scroll_view.shutdown()
        self.spread.shutdown()
        self.session.close()
        # The renderer's memory leaves with its process, but the textures were
        # this process's own. Hand the arenas back once, off the pop animation.
        GLib.idle_add(_reclaim, priority=GLib.PRIORITY_LOW)


def _reclaim() -> bool:
    from interaktiv_core import jobs

    jobs.trim_memory()
    return GLib.SOURCE_REMOVE
