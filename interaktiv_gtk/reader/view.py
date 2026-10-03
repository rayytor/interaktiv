"""
The reader page: a book, and the controls for it.

From the top: a slim header with the way back and the book's name; the page
itself on a desk; and a dock along the bottom with everything a lesson touches.
A board is driven standing up, so the controls are where a hand is, and the
on-screen keyboard -- which floats at the top right -- covers none of them.

Turning the page has three routes: the dock's arrows, a swipe across the sheet,
and the two invisible strips down the left and right of the reading area, which
make "next page" a tap anywhere along the edge of the board.

Book mode is asymmetric on purpose: forward from the cover lands on page 2,
backward from anywhere in the first spread lands on page 1.
"""

from typing import List, Optional

from gi.repository import Adw, Gdk, GLib, Gtk

from .. import icons
from ..render.service import LANE_THUMBNAIL
from ..theme import THEMES, apply_theme as apply_app_theme
from ..touch import EdgePan, SwipeNavigator, bind_touch_tooltip
from ..widgets import SidePanel
from . import paging
from .dock import ReaderDock
from interaktiv_core.ink import BookInk

from .focus import FocusOverlay
from .help import HelpWindow
from .holdrepeat import bind_hold_repeat
from .ink_controller import InkController
from .page_view import PageView
from .pinch import PinchZoom
from .scroll_mode import ScrollModeView
from .search import SearchController
from .session import DocumentSession
from .sidebar import ReaderSidebar
from .spread import SpreadView

MODES = ("book", "single", "scroll")

# The clamp lives with the rest of the pure paging arithmetic, so that the
# dock, the keyboard, a pinch and ctrl+wheel cannot drift apart.
ZOOM_MIN = paging.ZOOM_MIN
ZOOM_MAX = paging.ZOOM_MAX
ZOOM_STEP = paging.ZOOM_STEP

# What the search count reads before anything has been searched for.
NO_MATCHES = "0 / 0"

# Wide enough for the three tab names and two columns of thumbnails.
SIDEBAR_WIDTH = 360

# Where a double tap on the page lands. Twice the sheet is the step that makes
# a diagram readable from the back of a classroom without leaving the page.
TAP_ZOOM = 2.0

# What the reader responds to, in the shape `Gtk.ShortcutTrigger` parses. These
# are attached to the page and not to the window, so they are inert while the
# library is on screen and they lose to whatever has the keyboard focus.
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


def chapter_at(toc, page: int) -> str:
    """The title of the outline entry that `page` falls under, or ''."""
    title = ""
    for entry in toc or ():
        try:
            _level, name, start = entry[0], entry[1], int(entry[2])
        except (IndexError, TypeError, ValueError):
            continue
        if start > page:
            break
        title = str(name).strip()
    return title


def unit_pages(toc) -> List[int]:
    """Where the book's top-level units begin."""
    pages = []
    for entry in toc or ():
        try:
            if int(entry[0]) == 1 and int(entry[2]) > 0:
                pages.append(int(entry[2]))
        except (IndexError, TypeError, ValueError):
            continue
    return pages


class ReaderPage(Gtk.Box):
    __gtype_name__ = "InteraktivReaderPage"

    def __init__(self, app, item, path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("reader")
        self.app = app
        self.item = item
        self.settings = app.settings
        # Page sizes and render times are for whoever is tuning the renderer.
        self.debug = getattr(app, "debug", False) is True
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
        mode = self.settings.get("view_mode")
        self.view_mode = mode if mode in MODES else "book"
        self.zoom_mode = self.settings.get("zoom_mode") or "fit-page"
        self.custom_zoom = 1.0
        self._pre_tap_zoom = None
        self.rotation = 0
        self.show_activities = bool(self.settings.get("show_activities"))
        self._closed = False
        self._sidebar_loaded = False
        self._pre_focus_state = None
        self._first_page_shown = False
        self._fullscreen_handler = 0

        # The book's drawings, loaded before the pages are built so that the
        # first frame already has them.
        self.ink = BookInk.load(item.id)
        self.ink_controller = InkController(self, self.ink)

        self._build()
        self.spread.set_ink(self.ink)
        self.scroll_view.set_ink(self.ink)
        self.focus_overlay.page_view.set_ink(self.ink)
        self._install_shortcuts()
        self.apply_theme(self._theme())
        self._open()

    def _theme(self) -> str:
        theme = self.settings.get("theme")
        return theme if theme in THEMES else "dark"

    # ------------------------------------------------------------------ UI

    def _build(self) -> None:
        self.toast_overlay = Adw.ToastOverlay(vexpand=True)

        self.spread = SpreadView()
        self.spread.set_mode(self.view_mode if self.view_mode in ("book", "single") else "single")
        self.spread.set_zoom(self.zoom_mode, self.custom_zoom)
        self.spread.connect("scale-changed", self._on_scale_changed)
        self.spread.connect("page-rendered", self._on_page_rendered)
        self.spread.connect("activity-activated", self._on_activity_activated)
        self.spread.connect("zoom-toggled", lambda *_: self.toggle_tap_zoom())
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
        self.scroll_view.connect("zoom-toggled", lambda *_: self.toggle_tap_zoom())
        self.scroll_view.connect("dominant-page-changed", self._on_dominant_page_changed)
        self.scroll_view.set_reveal(self.show_activities)

        self.canvas_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        self.canvas_stack.add_named(self.scroller, "spread")
        self.canvas_stack.add_named(self.scroll_view, "scroll")
        self.canvas_stack.set_visible_child_name(
            "scroll" if self.view_mode == "scroll" else "spread"
        )

        self._install_zoom_gestures()

        # The page turns lie over the reading area, not beside it, so they
        # cost the page no width. An edge that cannot turn is insensitive, and
        # GTK then picks straight through it to the sheet underneath.
        canvas_area = Gtk.Overlay(vexpand=True)
        canvas_area.set_child(self.canvas_stack)
        canvas_area.add_overlay(self._page_edge("prev"))
        canvas_area.add_overlay(self._page_edge("next"))
        canvas_area.add_overlay(self._search_bar())
        canvas_area.add_overlay(self._opening_layer())
        if self.debug:
            canvas_area.add_overlay(self._debug_label())
        self._install_swipe(canvas_area)
        for scroller in (self.scroller, self.scroll_view.scroller):
            scroller.get_hadjustment().connect(
                "changed", lambda *_: self._update_page_edges()
            )
        # Deep zoom renders what is on screen sharp; a pan moves what that is.
        for adjustment in (self.scroller.get_hadjustment(), self.scroller.get_vadjustment()):
            adjustment.connect("value-changed", lambda *_: self.spread.schedule_detail())

        self.progress = Gtk.ProgressBar()
        self.progress.add_css_class("reading-progress")

        self.dock = ReaderDock(self._theme())
        self._connect_dock()
        dock_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        dock_row.add_css_class("dock-row")
        self.dock.set_hexpand(True)
        dock_row.append(self.dock)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.append(canvas_area)
        main.append(self.progress)
        main.append(dock_row)

        self.sidebar = ReaderSidebar()
        self.sidebar.connect("page-chosen", self._on_sidebar_page_chosen)
        self.sidebar.connect("activity-chosen", self._on_activity_chosen)

        self.split = SidePanel(
            sidebar=self.sidebar,
            content=main,
            width=SIDEBAR_WIDTH,
            show_sidebar=False,
        )
        self.split.set_vexpand(True)
        # The lists are built the first time the panel is opened, not when the
        # book is: for a long book that is real work most lessons never need.
        self.split.connect("notify::show-sidebar", self._on_sidebar_shown)

        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        shell.append(self._header())
        shell.append(self.split)

        self.focus_overlay = FocusOverlay(self)
        self.focus_overlay.set_visible(False)

        overlay = Gtk.Overlay()
        overlay.set_child(shell)
        overlay.add_overlay(self.focus_overlay)

        self.toast_overlay.set_child(overlay)
        self.append(self.toast_overlay)

        self.dock.set_mode(self.view_mode)
        self.dock.set_activities_shown(self.show_activities)
        self._update_zoom_status()

    def _header(self) -> Gtk.Widget:
        self.window_title = Adw.WindowTitle(title=self.item.title, subtitle="")
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.add_css_class("reader-header")
        header.set_title_widget(self.window_title)
        self.btn_back = Gtk.Button(tooltip_text="Kitaplığa dön (Esc)")
        self.btn_back.add_css_class("back-btn")
        back = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        back.append(Gtk.Image.new_from_icon_name(icons.BACK))
        back.append(Gtk.Label(label="Kitaplık"))
        self.btn_back.set_child(back)
        bind_touch_tooltip(self.btn_back)
        self.btn_back.connect("clicked", lambda *_: self._close_book())
        header.pack_start(self.btn_back)
        self.header = header
        return header

    def _opening_layer(self) -> Gtk.Widget:
        """
        What covers the desk until the first page is on it: the book's cover
        and a spinner, or the reason the book could not be opened.
        """
        opening = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        cover = self._cover_picture()
        if cover is not None:
            opening.append(cover)
        opening.append(Gtk.Spinner(spinning=True, width_request=36, height_request=36,
                                   halign=Gtk.Align.CENTER))
        label = Gtk.Label(label="Kitap açılıyor…")
        label.add_css_class("dim-label")
        opening.append(label)

        self.status_page = Adw.StatusPage(
            icon_name=icons.BOOK, title="Kitap açılamadı", vexpand=True,
        )

        self.opening_stack = Gtk.Stack()
        self.opening_stack.add_css_class("opening-layer")
        self.opening_stack.add_named(opening, "opening")
        self.opening_stack.add_named(self.status_page, "error")

        self.opening = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=180,
            reveal_child=True,
            child=self.opening_stack,
        )
        self.opening.connect("notify::child-revealed", self._on_opening_faded)
        return self.opening

    def _cover_picture(self) -> Optional[Gtk.Widget]:
        try:
            path = self.app.manager.get_thumbnail_path(self.item.id)
        except Exception:
            path = None
        if not isinstance(path, str):
            return None
        picture = Gtk.Picture.new_for_filename(path)
        picture.set_size_request(220, 311)
        picture.set_halign(Gtk.Align.CENTER)
        picture.add_css_class("opening-cover")
        return picture

    def _on_opening_faded(self, revealer, _param) -> None:
        if not revealer.get_child_revealed():
            revealer.set_visible(False)

    def _debug_label(self) -> Gtk.Widget:
        self.debug_label = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.END)
        self.debug_label.add_css_class("debug-label")
        self.debug_label.add_css_class("numeric")
        self.debug_label.set_can_target(False)
        return self.debug_label

    def _connect_dock(self) -> None:
        dock = self.dock
        dock.connect("previous", lambda _d: self.prev_page())
        dock.connect("next", lambda _d: self.next_page())
        dock.connect("page-chosen", lambda _d, page: self.go_to_page(page))
        dock.connect("mode-chosen", lambda _d, mode: self.set_view_mode(mode))
        dock.connect("zoom-stepped", lambda _d, step: self.step_zoom(step * ZOOM_STEP))
        dock.connect("zoom-fit", lambda _d: self.toggle_fit())
        dock.connect("sidebar-toggled", lambda _d, show: self.split.set_show_sidebar(show))
        dock.connect("activities-toggled", lambda _d, show: self.set_show_activities(show))
        dock.connect("search", lambda _d: self.toggle_search())
        dock.connect("rotate", lambda _d: self.rotate())
        dock.connect("fullscreen", lambda _d: self._toggle_fullscreen())
        dock.connect("help", lambda _d: self.show_help())
        dock.connect("clear-ink", lambda _d: self.clear_visible_ink())
        dock.connect("theme-chosen", lambda _d, theme: self.set_theme(theme))

    def _search_bar(self) -> Gtk.Widget:
        """The search row, above the dock: within reach, clear of the keyboard."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("dock")
        box.add_css_class("reader-search")

        # The property, not `set_placeholder_text()`: the setter is GTK 4.10.
        self.search_entry = Gtk.SearchEntry(placeholder_text="Kitapta ara…")
        self.search_entry.add_css_class("dock-search")
        self.search_entry.set_size_request(320, -1)
        self.search_entry.connect("activate", self._on_search_entry_activate)
        self.search_entry.connect("search-changed", self._on_search_entry_changed)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_search_key_pressed)
        self.search_entry.add_controller(key_ctrl)

        self.search_count_label = Gtk.Label(label=NO_MATCHES)
        self.search_count_label.add_css_class("numeric")
        self.search_count_label.add_css_class("dock-label")

        box.append(self.search_entry)
        box.append(self.search_count_label)
        for icon, tooltip, action in (
            (icons.UP, "Önceki eşleşme (Shift+Enter)", self.prev_search_match),
            (icons.DOWN, "Sonraki eşleşme (Enter)", self.next_search_match),
            (icons.CANCEL, "Aramayı kapat (Esc)", self.close_search),
        ):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
            button.add_css_class("dock-btn")
            bind_touch_tooltip(button)
            button.connect("clicked", lambda _b, fn=action: fn())
            box.append(button)

        self.search_bar = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=150,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.END,
            child=box,
        )
        return self.search_bar

    def _page_edge(self, which: str) -> Gtk.Widget:
        """
        One of the two page-turn strips down the sides of the reading area: a
        button like any other, drawn as nothing but a faint chevron. Its width
        is `.page-edge`'s, because a strip a finger finds without aiming is
        measured in centimetres.
        """
        forward = which == "next"
        button = Gtk.Button(
            icon_name=icons.NEXT_PAGE if forward else icons.PREV_PAGE,
            tooltip_text="Sonraki Sayfa (→)" if forward else "Önceki Sayfa (←)",
            halign=Gtk.Align.END if forward else Gtk.Align.START,
            valign=Gtk.Align.FILL,
            can_focus=False,
        )
        button.add_css_class("page-edge")
        button.add_css_class("page-edge-next" if forward else "page-edge-prev")
        bind_hold_repeat(
            button, self.next_page if forward else self.prev_page,
            tap_through=self._tap_through_edge,
        )
        if forward:
            self.btn_next = button
        else:
            self.btn_prev = button
        return button

    # --------------------------------------------------------- swipe input

    def _install_swipe(self, canvas_area: Gtk.Widget) -> None:
        """
        Flick sideways across the sheet to turn the page.

        It is installed on the overlay rather than on the scroller because the
        scroller is not the only thing listening: `GtkScrolledWindow` claims a
        touch drag as soon as it passes GTK's drag threshold, and the two
        page-turn strips lie over the same area. An ancestor's capture phase is
        the one place that sees the motion before either of them, which is
        where the choice between "this is a swipe" and "this belongs to
        whatever scrolls" has to be made.

        Two things hand the drag back. A sheet wide enough to pan is being
        panned, not swiped -- at any zoom past the frame the sideways drag is
        the only way to reach the rest of the page. And scroll mode is a
        continuous column: there is no facing page to flick to, so a sideways
        drag there means nothing and is left alone.
        """
        def can_pan() -> bool:
            adjustment = self.scroller.get_hadjustment()
            if adjustment is None:
                return False
            return adjustment.get_upper() - adjustment.get_page_size() > 1.0

        SwipeNavigator(
            canvas_area,
            self._on_swipe,
            can_pan=can_pan,
            enabled=lambda: self.view_mode in ("book", "single"),
        )

        # The strips cover the sheet, so a drag begun inside one would reach
        # nothing at all: they are the scroller's siblings, not its children.
        # This carries that drag across, and only that one -- a press anywhere
        # on the page itself belongs to the scroller and is left to it.
        edges = (self.btn_prev, self.btn_next)
        EdgePan(
            canvas_area,
            self.scroller,
            covers=lambda target: target is not None and any(
                target is edge or target.is_ancestor(edge) for edge in edges
            ),
        )

    def _tap_through_edge(self, button, x: float, y: float) -> bool:
        """
        A tap on a page-edge strip that lands on an activity opens the
        activity: the strip is invisible, and a teacher touching a question
        printed near the edge of the page meant the question.
        """
        root = self.get_root()
        point = button.translate_coordinates(root, x, y) if root is not None else None
        if point is None:
            return False
        view = self.ink_controller._geometric(*point)
        if view is None:
            return False
        local = root.translate_coordinates(view, *point)
        if local is None:
            return False
        spot, pin = view._probe(*local)
        target = pin if pin is not None else spot
        if target is None:
            return False
        view.emit("activity-activated", target)
        return True

    def _update_page_edges_idle(self) -> bool:
        self._update_page_edges()
        return GLib.SOURCE_REMOVE

    def _update_page_edges(self) -> None:
        """
        The page-turn strips are there only while the sheet fits the width.

        Zoomed in, a finger on the edge of the screen means "show me more of
        this side of the page", as on a phone, and a strip there would turn
        the page instead -- so they go, and the dock's arrows remain.
        """
        adjustment = self.canvas_scroller().get_hadjustment()
        wide = adjustment is not None and (
            adjustment.get_upper() - adjustment.get_page_size() > 1.0
        )
        for edge in (self.btn_prev, self.btn_next):
            edge.set_visible(not wide)

    def _on_swipe(self, step: int) -> None:
        if step > 0:
            self.next_page()
        else:
            self.prev_page()

    # ---------------------------------------------------------- zoom input

    def toggle_tap_zoom(self) -> None:
        """
        What a double tap on bare paper does.

        A board has no scroll wheel and no keyboard within reach, so the second
        tap is the whole zoom control for a teacher standing at the screen. It
        goes out to `TAP_ZOOM` and back to the fit the book was being read at
        -- remembered rather than assumed, so a class reading at Fit Width
        returns to Fit Width and not to Fit Page. Like a phone, it zooms in
        around the tapped point, which stays under the finger.
        """
        def apply() -> None:
            if self.zoom_mode == "custom":
                self.set_zoom(self._pre_tap_zoom or "fit-page")
                return
            self._pre_tap_zoom = self.zoom_mode
            self.set_zoom("custom", TAP_ZOOM)

        point = self._last_tap_point()
        if point is None:
            apply()
        else:
            self.pinch.zoom_at(*point, apply)

    def _last_tap_point(self):
        """Where the double tap that is being answered landed, in the scroller."""
        canvas = self.scroll_view if self.view_mode == "scroll" else self.spread
        tap = getattr(canvas, "last_zoom_tap", None)
        canvas.last_zoom_tap = None
        if not tap or tap[1] is None:
            return None
        view, (x, y) = tap
        ok, b = view.compute_bounds(self.canvas_scroller())
        if not ok:
            return None
        return (b.get_x() + x, b.get_y() + y)

    def canvas_scroller(self) -> Gtk.ScrolledWindow:
        """The scrolled window the page is in now."""
        return self.scroll_view.scroller if self.view_mode == "scroll" else self.scroller

    def visible_page_views(self) -> List[PageView]:
        if self.view_mode == "scroll":
            return self.scroll_view.page_views()
        return self.spread.page_views()

    def _install_zoom_gestures(self) -> None:
        """
        Pinch to zoom, and ctrl+wheel for the people without a touchscreen.

        Two fingers do what they do on a phone: the print between them stays
        between them while they spread, and moving them together carries the
        page along (see `pinch.py`). `Gtk.GestureZoom` reports a scale
        relative to where the fingers started, and the centre of the fingers
        as they are now, which is all that takes.
        """
        self.pinch = PinchZoom(
            scroller=self.canvas_scroller,
            views=self.visible_page_views,
            zoom=self._effective_zoom,
            set_zoom=lambda zoom, live: self.set_zoom("custom", zoom, live=live),
            settle=self._settle_zoom,
        )
        for widget in (self.scroller, self.scroll_view):
            pinch = Gtk.GestureZoom()
            pinch.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            pinch.connect("begin", self._on_pinch_begin)
            pinch.connect("scale-changed", self._on_pinch_scale)
            pinch.connect("end", self._on_pinch_end)
            pinch.connect("cancel", self._on_pinch_end)
            widget.add_controller(pinch)

            wheel = Gtk.EventControllerScroll(
                flags=Gtk.EventControllerScrollFlags.VERTICAL,
                propagation_phase=Gtk.PropagationPhase.CAPTURE,
            )
            wheel.connect("scroll", self._on_scroll_zoom)
            widget.add_controller(wheel)

    def _effective_zoom(self) -> float:
        if self.zoom_mode == "custom":
            return self.custom_zoom
        view = self.scroll_view if self.view_mode == "scroll" else self.spread
        return view.zoom or 1.0

    def _pinch_centre(self, gesture):
        """The fingers' centre, in the coordinates of the scroller on screen."""
        ok, x, y = gesture.get_bounding_box_center()
        if not ok:
            return None
        widget = gesture.get_widget()
        scroller = self.canvas_scroller()
        if widget is not scroller:
            point = widget.translate_coordinates(scroller, x, y)
            if point is None:
                return None
            x, y = point
        return (x, y)

    def _on_pinch_begin(self, gesture, _sequence) -> None:
        # Claim the moment the second finger lands. `GtkGestureZoom` never
        # claims on its own, and `GtkScrolledWindow` takes any touch drag that
        # passes GTK's threshold -- so without this the scroller pans the sheet
        # under the pinch, and the finger that started on a hotspot opens it
        # when the pinch ends. The pan it would have done, the pinch does.
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        centre = self._pinch_centre(gesture)
        if centre is not None:
            self.pinch.begin(*centre)

    def _on_pinch_scale(self, gesture, scale: float) -> None:
        centre = self._pinch_centre(gesture)
        if centre is not None:
            self.pinch.update(centre[0], centre[1], scale)

    def _on_pinch_end(self, _gesture, _sequence) -> None:
        self.pinch.end()

    def _settle_zoom(self) -> None:
        """The fingers let go or held still: render the pages sharp."""
        for view in (self.spread, self.scroll_view):
            view.live = False
            view.invalidate()
        self._update_page_edges()

    def gesture_at_window(self, phase: str, x: float, y: float, scale: float) -> bool:
        """
        A two-finger gesture Rayyanpen forwarded from over this window while
        its pen was out: the same pinch-and-pan, in window coordinates.
        """
        root = self.get_root()
        if root is None:
            return False
        point = root.translate_coordinates(self.canvas_scroller(), x, y)
        if point is None:
            return False
        if phase == "begin":
            self.pinch.begin(*point)
        elif phase == "update":
            self.pinch.update(point[0], point[1], scale)
        else:
            self.pinch.end()
        return True

    def _on_scroll_zoom(self, controller, _dx: float, dy: float) -> bool:
        if not (controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK):
            return False
        if dy == 0:
            return False
        target = paging.wheel_zoom(self._effective_zoom(), dy)
        self.set_zoom("custom", target)
        return True

    # ----------------------------------------------------------------- ink

    def redraw_ink(self) -> None:
        self.spread.redraw_ink()
        self.scroll_view.redraw_ink()
        self.focus_overlay.page_view.queue_draw()

    def clear_visible_ink(self) -> None:
        """The dock's "delete the drawings on this page", with an undo toast."""
        pages = self._visible_pages()
        token = self.ink_controller.clear_pages(pages)
        if token is None:
            self.toast_overlay.add_toast(Adw.Toast(title="Bu sayfada çizim yok"))
            return
        toast = Adw.Toast(title="Çizimler silindi", button_label="Geri al")
        toast.connect("button-clicked", lambda *_: self.ink_controller.undo_erase(token))
        self.toast_overlay.add_toast(toast)

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
        restored = min(max(self.settings.last_page(self.item.id), 1), max(1, total))
        self.current_page = restored
        self.dock.set_page_marks(unit_pages(info.toc))
        self.spread.attach(self.session.service, info, self.session.overlay)
        self.scroll_view.attach(self.session.service, info, self.session.overlay)
        self.sidebar.load_book(info, self.session.service, self.rotation, self.view_mode)
        if self.view_mode == "scroll":
            self.canvas_stack.set_visible_child_name("scroll")
            self.scroll_view.go_to_page(restored)
            self._update_position()
        else:
            self.canvas_stack.set_visible_child_name("spread")
            self._apply_page(restored)
        self._update_zoom_status()
        self.search_controller.attach(self.session.service, info)
        self.session.check_bake(self._on_bake_changed)
        if bool(self.settings.get("sidebar_open")):
            self.split.set_show_sidebar(True)

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
        renderer's own words underneath: they name the actual cause.
        """
        if self._closed:
            return
        self.status_page.set_description(
            "Dosya bozuk olabilir veya taşınmış olabilir. "
            "Kitaplıktan kaldırıp yeniden indirmeyi deneyin.\n\n"
            f"{message}"
        )
        self.opening_stack.set_visible_child_name("error")

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
        The renderer reports the process is heavier than a board can carry.
        Give the textures back; they cost one re-render each and nothing else.
        """
        if self._closed:
            return
        self.spread.drop_textures()
        self.scroll_view.drop_textures()

    def _on_page_rendered(self, _view, page: int, ms: int) -> None:
        if not self._first_page_shown:
            # The cover stays up until there is a page to show in its place.
            self._first_page_shown = True
            self.opening.set_reveal_child(False)
        if self.debug:
            width, height = self.session.info.size(page)
            self.debug_label.set_label(
                f"sayfa {page} · {round(width)} × {round(height)} pt · {ms} ms"
            )

    def page_texture(self, page: int):
        """The texture on screen for `page`, for focus mode's first frame."""
        view = self.scroll_view if self.view_mode == "scroll" else self.spread
        return view.texture_for(page)

    # --------------------------------------------------------- navigation

    def go_to_page(self, page: int) -> None:
        total = self.session.page_count
        if total <= 0:
            return
        page = min(max(int(page), 1), total)
        if self.view_mode == "scroll":
            self.current_page = page
            self.scroll_view.go_to_page(page)
            self._update_position()
            return
        if self.view_mode == "book" and page in self.spread.pages:
            return
        if self.view_mode != "book" and page == self.current_page:
            return
        self._apply_page(page)

    def _apply_page(self, page: int) -> None:
        pages = self.spread.pages_for(page)
        # Book mode snaps to the left page of the spread.
        self.current_page = pages[0]
        self.spread.show_pages(pages)
        self._update_position()

    def _on_dominant_page_changed(self, _scroll_view, page: int) -> None:
        if self.view_mode != "scroll":
            return
        self.current_page = page
        self._update_position()

    def _on_sidebar_page_chosen(self, _sidebar, page: int) -> None:
        self.go_to_page(page)

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

    def visible_pages(self) -> List[int]:
        return self._visible_pages()

    def _visible_pages(self) -> List[int]:
        if self.view_mode == "scroll":
            return [self.current_page]
        return list(self.spread.pages) or [self.current_page]

    def _update_position(self) -> None:
        """Everything that says where in the book this is."""
        total = self.session.page_count
        pages = self._visible_pages()
        self.dock.set_page(self.current_page, total)
        self.progress.set_fraction(pages[-1] / total if total > 0 else 0.0)

        can_go_back = self.current_page > 1
        can_go_on = bool(pages) and total not in pages and self.current_page < total
        self.btn_prev.set_sensitive(can_go_back)
        self.btn_next.set_sensitive(can_go_on)
        self.dock.set_can_turn(can_go_back, can_go_on)

        if self.session.is_open:
            self.window_title.set_subtitle(chapter_at(self.session.info.toc, self.current_page))
        self._update_activity_status()
        self.sidebar.update_active_page(self.current_page)
        self.settings.remember_position(self.item.id, self.current_page, total)

    # ---------------------------------------------------------- view mode

    def set_view_mode(self, mode: str) -> None:
        if mode == self.view_mode or mode not in MODES:
            return
        self.view_mode = mode
        self.settings.set("view_mode", mode)
        self.dock.set_mode(mode)
        self.sidebar.set_view_mode(mode)
        if mode == "scroll":
            self.canvas_stack.set_visible_child_name("scroll")
            self.scroll_view.go_to_page(self.current_page)
            self._update_position()
        else:
            self.canvas_stack.set_visible_child_name("spread")
            self.spread.set_mode(mode)
            self._apply_page(self.current_page)

    # --------------------------------------------------------------- zoom

    def set_zoom(self, mode: str, custom: float = None, live: bool = False) -> None:
        """
        `live` is a pinch in flight: lay out at the new zoom but scale the
        textures already on screen instead of rendering, until it settles.
        """
        self.zoom_mode = mode
        if custom is not None:
            self.custom_zoom = min(ZOOM_MAX, max(ZOOM_MIN, custom))
        if mode in ("fit-width", "fit-page"):
            self.settings.set("zoom_mode", mode)
        for view in (self.spread, self.scroll_view):
            if not live and view.live:
                view.live = False
            elif live:
                view.live = True
        self.spread.set_zoom(mode, self.custom_zoom)
        self.scroll_view.set_zoom(mode, self.custom_zoom)
        self._update_zoom_status()
        if not live:
            GLib.idle_add(self._update_page_edges_idle)

    def step_zoom(self, delta: float) -> None:
        # Stepping starts from 100 %, not from whatever the fit happens to be,
        # so that +/- always lands on the same ladder.
        current = self.custom_zoom if self.zoom_mode == "custom" else 1.0
        self.set_zoom("custom", min(ZOOM_MAX, max(ZOOM_MIN, current + delta)))

    def toggle_fit(self) -> None:
        """The dock's zoom label: back to a fit, or from one fit to the other."""
        self.set_zoom("fit-width" if self.zoom_mode == "fit-page" else "fit-page")

    def _on_scale_changed(self, _view, _zoom: float) -> None:
        self._update_zoom_status()

    def _update_zoom_status(self) -> None:
        if self.zoom_mode == "fit-page":
            self.dock.set_zoom_text("Sığdır")
        elif self.zoom_mode == "fit-width":
            self.dock.set_zoom_text("Genişlik")
        else:
            self.dock.set_zoom_text(f"%{round(self.custom_zoom * 100)}")

    # ------------------------------------------------------------- rotate

    def rotate(self) -> None:
        self.rotation = (self.rotation + 90) % 360
        self.spread.set_rotation(self.rotation)
        self.scroll_view.set_rotation(self.rotation)
        self.sidebar.set_rotation(self.rotation)

    # --------------------------------------------------------- activities

    def set_show_activities(self, show: bool) -> None:
        self.show_activities = bool(show)
        self.settings.set("show_activities", self.show_activities)
        self.spread.set_reveal(self.show_activities)
        self.scroll_view.set_reveal(self.show_activities)
        self.dock.set_activities_shown(self.show_activities)

    def toggle_activities(self) -> None:
        self.set_show_activities(not self.show_activities)

    def toggle_sidebar(self) -> None:
        self.split.set_show_sidebar(not self.split.get_show_sidebar())

    def _on_sidebar_shown(self, *_args) -> None:
        show = self.split.get_show_sidebar()
        self.settings.set("sidebar_open", show)
        self.dock.set_sidebar_open(show)
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

        A region is selected: both its pieces light up and the list follows
        it. An interactive one opens the publisher's activity; any other opens
        focus mode. A pin has no region, only an activity to open.
        """
        if self._closed:
            return
        from .overlay import Pin

        if isinstance(target, Pin):
            self.spread.clear_selection()
            self.scroll_view.clear_selection()
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
        """
        Open a publisher activity: in a window of our own where WebKitGTK is
        installed, in the board's browser where it is not.
        """
        if not guid or self._closed:
            return
        from . import activity_dialog

        manager = self.session.manager or getattr(self.app, "manager", None)
        if not activity_dialog.HAS_WEBKIT:
            if activity_dialog.open_in_browser(manager, guid):
                self.toast_overlay.add_toast(Adw.Toast(title="Etkinlik tarayıcıda açıldı."))
            else:
                self.toast_overlay.add_toast(Adw.Toast(title="Etkinlik açılamadı."))
            return

        dialog = activity_dialog.ActivityDialog(
            manager=manager,
            guid=guid,
            title=title,
            installed=installed,
        )
        dialog.show_for(self)

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

        self._update_activity_status()
        self._update_zoom_status()

    def step_activity_from(self, activity, direction: int):
        """
        Next / previous region across page boundaries, in reading order:
        activities and content alike, so that stepping walks the whole page.
        direction is +1 or -1.
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

    def _update_activity_status(self) -> None:
        """
        How many activities are on what is currently open, for the dock's
        badge. Counted from the overlays rather than from the bake, so it says
        what is actually drawn.
        """
        if not self.session.is_open or not self.session.activities_ready:
            self.dock.set_activity_count(0)
            return
        count = 0
        for page in self._visible_pages():
            overlay = self.session.overlay(page)
            if overlay is not None:
                count += overlay.activity_count + len(overlay.pins)
        self.dock.set_activity_count(count)

    # ------------------------------------------------------------- search

    @property
    def search_open(self) -> bool:
        return self.search_bar.get_reveal_child()

    def toggle_search(self) -> None:
        if self.search_open:
            self.close_search()
        else:
            self.open_search()

    def open_search(self) -> None:
        self.search_bar.set_reveal_child(True)
        self.search_entry.grab_focus()
        if self.search_entry.get_text():
            self.search_entry.select_region(0, -1)

    def close_search(self) -> None:
        self.search_bar.set_reveal_child(False)
        self.search_controller.clear()
        self.spread.set_search_matches({}, None)
        self.scroll_view.set_search_matches({}, None)
        self.search_count_label.set_label(NO_MATCHES)
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
            self.search_count_label.set_label(NO_MATCHES)

    def _on_search_results_changed(self, _ctrl, total: int) -> None:
        if total == 0:
            if self.search_controller.query:
                self.search_count_label.set_label("0 eşleşme")
            else:
                self.search_count_label.set_label(NO_MATCHES)
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
        self.search_count_label.set_label(NO_MATCHES)
        self.spread.set_search_matches({}, None)
        self.scroll_view.set_search_matches({}, None)

    # ------------------------------------------------------ help and theme

    def show_help(self) -> None:
        HelpWindow(self.get_root()).present()

    def cycle_theme(self) -> None:
        current = self._theme()
        self.set_theme(THEMES[(THEMES.index(current) + 1) % len(THEMES)])

    def set_theme(self, theme: str) -> None:
        self.settings.set("theme", theme)
        self.apply_theme(theme)

    def apply_theme(self, theme: str) -> None:
        PageView.set_global_theme(theme)
        window = self.get_root()
        if window is not None and hasattr(window, "apply_theme"):
            window.apply_theme(theme)
        else:
            apply_app_theme(theme, window)
        self.dock.set_theme(theme)
        self.spread.queue_draw()
        self.scroll_view.queue_draw()
        self.focus_overlay.queue_draw()

    # ---------------------------------------------------------- shortcuts

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
            elif self.search_open:
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
            "help": self.show_help,
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

    # ------------------------------------------------- fullscreen and close

    def _toggle_fullscreen(self) -> None:
        window = self.get_root()
        if window is None:
            return
        if not self._fullscreen_handler:
            self._fullscreen_handler = window.connect(
                "notify::fullscreened", self._on_fullscreen_changed
            )
        if window.is_fullscreen():
            window.unfullscreen()
        else:
            window.fullscreen()

    def _on_fullscreen_changed(self, window, _param) -> None:
        # In fullscreen the page has the whole board; the way out is in the
        # dock's menu and on Esc.
        fullscreen = window.is_fullscreen()
        self.header.set_visible(not fullscreen)
        self.dock.set_fullscreen(fullscreen)

    def _close_book(self) -> None:
        window = self.get_root()
        if window is None:
            return
        if window.is_fullscreen():
            window.unfullscreen()
        if hasattr(window, "navigation"):
            window.navigation.pop()

    def shutdown(self) -> None:
        """
        Called when the page is popped. The render process and the textures
        both go now rather than when Python happens to collect the page: a
        book left open is half a gigabyte on a machine that has two.
        """
        if self._closed:
            return
        self._closed = True
        window = self.get_root()
        if window is not None and self._fullscreen_handler:
            window.disconnect(self._fullscreen_handler)
            self._fullscreen_handler = 0
        self.settings.set_last_page(self.item.id, self.current_page)
        self.ink_controller.flush()
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
