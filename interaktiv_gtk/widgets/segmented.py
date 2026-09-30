"""A row of mutually exclusive buttons: one choice out of a few."""

from typing import Dict, Optional

from gi.repository import GObject, Gtk

from ..touch import bind_touch_tooltip


class SegmentedControl(Gtk.Box):
    __gtype_name__ = "InteraktivSegmentedControl"
    __gsignals__ = {
        # The name of the segment that became active.
        "changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, homogeneous: bool = False):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=homogeneous)
        self.add_css_class("segmented")
        self._buttons: Dict[str, Gtk.ToggleButton] = {}
        self._labels: Dict[str, Gtk.Label] = {}
        self._active: Optional[str] = None

    def add(self, name: str, label: Optional[str] = None,
            icon_name: Optional[str] = None, tooltip: Optional[str] = None) -> Gtk.ToggleButton:
        button = Gtk.ToggleButton(hexpand=self.get_homogeneous())
        button.add_css_class("segment")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          halign=Gtk.Align.CENTER)
        if icon_name:
            content.append(Gtk.Image.new_from_icon_name(icon_name))
        if label:
            text = Gtk.Label(label=label)
            content.append(text)
            self._labels[name] = text
        button.set_child(content)

        if tooltip:
            button.set_tooltip_text(tooltip)
            bind_touch_tooltip(button)

        # Grouped toggle buttons behave as radio buttons: pressing the active
        # one leaves it active.
        first = next(iter(self._buttons.values()), None)
        if first is not None:
            button.set_group(first)

        self._buttons[name] = button
        button.connect("toggled", self._on_toggled, name)
        self.append(button)
        return button

    def get_active_name(self) -> Optional[str]:
        return self._active

    def set_active_name(self, name: str) -> None:
        button = self._buttons.get(name)
        if button is not None:
            button.set_active(True)

    def set_label(self, name: str, label: str) -> None:
        text = self._labels.get(name)
        if text is not None:
            text.set_label(label)

    def get_button(self, name: str) -> Optional[Gtk.ToggleButton]:
        return self._buttons.get(name)

    def _on_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        if not button.get_active() or name == self._active:
            return
        self._active = name
        self.emit("changed", name)
