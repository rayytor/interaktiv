"""
The reader's controls: one bar floating at the bottom of the page.

A board is two metres tall and is driven standing up, so everything a lesson
touches is in the strip a hand reaches without stretching. The dock holds no
state of its own: it emits what was asked for and is told what to show.
"""

from typing import Optional

from gi.repository import GLib, GObject, Gtk

from .. import icons
from ..theme import THEMES
from ..touch import bind_touch_tooltip
from ..widgets import NumberPad, ThemePicker
from .holdrepeat import bind_hold_repeat

MODES = [
    ("book", "Çift Sayfa", icons.MODE_BOOK),
    ("single", "Tek Sayfa", icons.MODE_SINGLE),
    ("scroll", "Kaydırma", icons.MODE_SCROLL),
]
MODE_ICONS = {name: icon for name, _label, icon in MODES}

# How long the page scrubber's thumb must rest before the page turns.
SCRUB_SETTLE_MS = 300


def dock_button(icon_name: str, tooltip: str, toggle: bool = False) -> Gtk.Button:
    button = (Gtk.ToggleButton if toggle else Gtk.Button)(
        icon_name=icon_name, tooltip_text=tooltip,
    )
    button.add_css_class("dock-btn")
    # GTK never shows a tooltip to a finger; a long press does instead.
    bind_touch_tooltip(button)
    return button


def separator() -> Gtk.Widget:
    return Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)


class ReaderDock(Gtk.Box):
    __gtype_name__ = "InteraktivReaderDock"
    __gsignals__ = {
        "previous": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "next": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "page-chosen": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "mode-chosen": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # -1 or +1: one step out or in.
        "zoom-stepped": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "zoom-fit": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "sidebar-toggled": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "activities-toggled": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "search": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "rotate": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "fullscreen": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "help": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Delete the pen drawings on the pages on screen.
        "clear-ink": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "theme-chosen": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, theme: str = "dark"):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=2,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
        )
        self.add_css_class("dock")
        self.add_css_class("reader-dock")
        self._page = 1
        self._total = 1
        self._syncing = False
        self._scrub_source = 0

        self.btn_sidebar = dock_button(icons.CONTENTS, "Sayfalar ve içindekiler (T)", toggle=True)
        self.btn_sidebar.connect("toggled", self._on_sidebar_toggled)
        self.append(self.btn_sidebar)
        self.append(separator())

        self.btn_prev = dock_button(icons.PREV_PAGE, "Önceki sayfa (←)")
        bind_hold_repeat(self.btn_prev, lambda: self.emit("previous"))
        self.append(self.btn_prev)
        self.append(self._build_page_chip())
        self.btn_next = dock_button(icons.NEXT_PAGE, "Sonraki sayfa (→)")
        bind_hold_repeat(self.btn_next, lambda: self.emit("next"))
        self.append(self.btn_next)
        self.append(separator())

        self.append(self._build_mode_button())
        self.btn_zoom_out = dock_button(icons.ZOOM_OUT, "Uzaklaştır (−)")
        self.btn_zoom_out.connect("clicked", lambda _b: self.emit("zoom-stepped", -1))
        self.append(self.btn_zoom_out)
        self.btn_zoom = Gtk.Button(label="Sığdır", tooltip_text="Sayfaya sığdır (0)")
        self.btn_zoom.add_css_class("dock-btn")
        self.btn_zoom.add_css_class("dock-zoom")
        self.btn_zoom.add_css_class("numeric")
        bind_touch_tooltip(self.btn_zoom)
        self.btn_zoom.connect("clicked", lambda _b: self.emit("zoom-fit"))
        self.append(self.btn_zoom)
        self.btn_zoom_in = dock_button(icons.ZOOM_IN, "Yakınlaştır (+)")
        self.btn_zoom_in.connect("clicked", lambda _b: self.emit("zoom-stepped", 1))
        self.append(self.btn_zoom_in)
        self.append(separator())

        self.append(self._build_activities_button())
        self.btn_search = dock_button(icons.SEARCH, "Kitapta ara (Ctrl+F)")
        self.btn_search.connect("clicked", lambda _b: self.emit("search"))
        self.append(self.btn_search)
        self.append(self._build_more_button(theme))

    # -- page chip --------------------------------------------------------

    def _build_page_chip(self) -> Gtk.Widget:
        self.page_label = Gtk.Label(label="1 / 1")
        self.page_label.add_css_class("numeric")

        self.numpad = NumberPad()
        self.numpad.connect("submitted", self._on_page_submitted)

        self.scrubber = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 2, 1)
        self.scrubber.set_draw_value(False)
        self.scrubber.set_size_request(280, -1)
        self.scrubber.add_css_class("page-scrubber")
        self.scrubber.connect("value-changed", self._on_scrubbed)

        sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        heading = Gtk.Label(label="Sayfaya git")
        heading.add_css_class("sheet-title")
        sheet.append(heading)
        sheet.append(self.scrubber)
        sheet.append(self.numpad)

        self.page_popover = Gtk.Popover(position=Gtk.PositionType.TOP)
        self.page_popover.add_css_class("sheet-popover")
        self.page_popover.set_child(sheet)
        self.page_popover.connect("show", self._on_page_popover_shown)

        chip = Gtk.MenuButton(popover=self.page_popover, tooltip_text="Sayfaya git")
        chip.set_child(self.page_label)
        chip.add_css_class("dock-btn")
        chip.add_css_class("page-chip")
        chip.add_css_class("flat")
        self.page_chip = chip
        return chip

    def _on_page_popover_shown(self, *_args) -> None:
        self.numpad.clear()
        self.numpad.set_hint(str(self._page))
        self._syncing = True
        self.scrubber.set_value(self._page)
        self._syncing = False

    def _on_scrubbed(self, scale: Gtk.Scale) -> None:
        if self._syncing:
            return
        # The number follows the thumb; the page turns once the thumb rests,
        # so dragging across a book does not render every page on the way.
        self.numpad.clear()
        self.numpad.set_hint(str(int(round(scale.get_value()))))
        if self._scrub_source:
            GLib.source_remove(self._scrub_source)
        self._scrub_source = GLib.timeout_add(SCRUB_SETTLE_MS, self._commit_scrub)

    def _commit_scrub(self) -> bool:
        self._scrub_source = 0
        page = int(round(self.scrubber.get_value()))
        if page != self._page:
            self.emit("page-chosen", page)
        return GLib.SOURCE_REMOVE

    def _on_page_submitted(self, _pad, page: int) -> None:
        self.page_popover.popdown()
        self.emit("page-chosen", page)

    # -- view mode --------------------------------------------------------

    def _build_mode_button(self) -> Gtk.Widget:
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._mode_rows = {}
        first: Optional[Gtk.ToggleButton] = None
        for name, label, icon in MODES:
            row = Gtk.ToggleButton()
            row.add_css_class("sheet-row")
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            content.append(Gtk.Image.new_from_icon_name(icon))
            content.append(Gtk.Label(label=label, xalign=0.0, hexpand=True))
            row.set_child(content)
            if first is None:
                first = row
            else:
                row.set_group(first)
            row.connect("toggled", self._on_mode_toggled, name)
            self._mode_rows[name] = row
            menu.append(row)

        self.mode_popover = Gtk.Popover(position=Gtk.PositionType.TOP)
        self.mode_popover.add_css_class("sheet-popover")
        self.mode_popover.set_child(menu)

        self.btn_mode = Gtk.MenuButton(
            icon_name=icons.MODE_BOOK, popover=self.mode_popover, tooltip_text="Görünüm",
        )
        self.btn_mode.add_css_class("dock-btn")
        self.btn_mode.add_css_class("flat")
        return self.btn_mode

    def _on_mode_toggled(self, row: Gtk.ToggleButton, name: str) -> None:
        if not row.get_active() or self._syncing:
            return
        self.mode_popover.popdown()
        self.emit("mode-chosen", name)

    # -- activities -------------------------------------------------------

    def _build_activities_button(self) -> Gtk.Widget:
        self.btn_activities = dock_button(icons.ACTIVITIES, "Etkinlikleri göster (A)", toggle=True)
        self.btn_activities.connect("toggled", self._on_activities_toggled)

        self.activity_badge = Gtk.Label(halign=Gtk.Align.END, valign=Gtk.Align.START)
        self.activity_badge.add_css_class("dock-badge")
        self.activity_badge.add_css_class("numeric")
        self.activity_badge.set_can_target(False)
        self.activity_badge.set_visible(False)

        overlay = Gtk.Overlay()
        overlay.set_child(self.btn_activities)
        overlay.add_overlay(self.activity_badge)
        return overlay

    # -- more -------------------------------------------------------------

    def _build_more_button(self, theme: str) -> Gtk.Widget:
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self.theme_picker = ThemePicker(theme if theme in THEMES else "dark")
        self.theme_picker.connect(
            "theme-chosen", lambda _p, name: self.emit("theme-chosen", name)
        )
        menu.append(self.theme_picker)
        menu.append(Gtk.Separator())

        for icon, label, signal in (
            (icons.ROTATE, "Sayfayı döndür", "rotate"),
            (icons.FULLSCREEN, "Tam ekran", "fullscreen"),
            (icons.UNINSTALL, "Bu sayfadaki çizimleri sil", "clear-ink"),
            (icons.HELP, "Yardım", "help"),
        ):
            row = Gtk.Button()
            row.add_css_class("sheet-row")
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            image = Gtk.Image.new_from_icon_name(icon)
            content.append(image)
            text = Gtk.Label(label=label, xalign=0.0, hexpand=True)
            content.append(text)
            row.set_child(content)
            row.connect("clicked", self._on_more_row, signal)
            menu.append(row)
            if signal == "fullscreen":
                self._fullscreen_icon, self._fullscreen_label = image, text

        self.more_popover = Gtk.Popover(position=Gtk.PositionType.TOP)
        self.more_popover.add_css_class("sheet-popover")
        self.more_popover.set_child(menu)

        button = Gtk.MenuButton(
            icon_name=icons.MORE, popover=self.more_popover, tooltip_text="Diğer",
        )
        button.add_css_class("dock-btn")
        button.add_css_class("flat")
        self.btn_more = button
        return button

    def _on_more_row(self, _row, signal: str) -> None:
        self.more_popover.popdown()
        self.emit(signal)

    # -- toggles ----------------------------------------------------------

    def _on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        if not self._syncing:
            self.emit("sidebar-toggled", button.get_active())

    def _on_activities_toggled(self, button: Gtk.ToggleButton) -> None:
        if not self._syncing:
            self.emit("activities-toggled", button.get_active())

    # -- what to show -----------------------------------------------------

    def set_page(self, page: int, total: int) -> None:
        self._page, self._total = int(page), max(1, int(total))
        self.page_label.set_label(f"{self._page} / {self._total}")
        self._syncing = True
        self.scrubber.set_range(1, max(2, self._total))
        self.scrubber.set_value(self._page)
        self._syncing = False

    def set_page_marks(self, pages) -> None:
        """Tick the scrubber where the book's units begin."""
        self.scrubber.clear_marks()
        for page in pages:
            self.scrubber.add_mark(page, Gtk.PositionType.BOTTOM, None)

    def set_can_turn(self, previous: bool, following: bool) -> None:
        self.btn_prev.set_sensitive(previous)
        self.btn_next.set_sensitive(following)

    def set_mode(self, mode: str) -> None:
        self._syncing = True
        row = self._mode_rows.get(mode)
        if row is not None:
            row.set_active(True)
        self._syncing = False
        self.btn_mode.set_icon_name(MODE_ICONS.get(mode, icons.MODE_BOOK))

    def set_zoom_text(self, text: str) -> None:
        self.btn_zoom.set_label(text)

    def set_sidebar_open(self, is_open: bool) -> None:
        self._syncing = True
        self.btn_sidebar.set_active(is_open)
        self._syncing = False

    def set_activities_shown(self, shown: bool) -> None:
        self._syncing = True
        self.btn_activities.set_active(shown)
        self._syncing = False

    def set_activity_count(self, count: int) -> None:
        """How many activities are on the open pages; nothing when there are none."""
        self.activity_badge.set_visible(count > 0)
        self.activity_badge.set_label(str(count))

    def set_fullscreen(self, fullscreen: bool) -> None:
        self._fullscreen_icon.set_from_icon_name(
            icons.RESTORE if fullscreen else icons.FULLSCREEN
        )
        self._fullscreen_label.set_label("Tam ekrandan çık" if fullscreen else "Tam ekran")

    def set_theme(self, theme: str) -> None:
        self.theme_picker.set_active(theme)
