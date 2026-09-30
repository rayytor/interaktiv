"""One swatch per reading theme."""

from gi.repository import GObject, Gtk

from ..theme import THEME_LABELS, THEMES
from ..touch import bind_touch_tooltip


class ThemePicker(Gtk.Box):
    __gtype_name__ = "InteraktivThemePicker"
    __gsignals__ = {
        "theme-chosen": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, active: str = "dark"):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                         halign=Gtk.Align.CENTER)
        self.add_css_class("theme-picker")
        self._buttons = {}
        self._syncing = False
        first = None
        for name in THEMES:
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            swatch = Gtk.ToggleButton(tooltip_text=THEME_LABELS[name])
            swatch.add_css_class("theme-swatch")
            swatch.add_css_class(f"swatch-{name}")
            if first is None:
                first = swatch
            else:
                swatch.set_group(first)
            swatch.connect("toggled", self._on_toggled, name)
            bind_touch_tooltip(swatch)
            self._buttons[name] = swatch

            label = Gtk.Label(label=THEME_LABELS[name])
            label.add_css_class("caption")
            column.append(swatch)
            column.append(label)
            self.append(column)
        self.set_active(active)

    def set_active(self, name: str) -> None:
        button = self._buttons.get(name)
        if button is None or button.get_active():
            return
        self._syncing = True
        button.set_active(True)
        self._syncing = False

    def _on_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        if button.get_active() and not self._syncing:
            self.emit("theme-chosen", name)
