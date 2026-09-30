"""A one-line notice that slides in under a header bar."""

from gi.repository import Gtk


class Banner(Gtk.Revealer):
    __gtype_name__ = "InteraktivBanner"

    def __init__(self, title: str = "", revealed: bool = False):
        super().__init__(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            reveal_child=revealed,
        )
        self._label = Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER)
        self._label.add_css_class("banner-bar")
        self.set_child(self._label)

    def get_title(self) -> str:
        return self._label.get_label()

    def set_title(self, title: str) -> None:
        self._label.set_label(title)

    def get_revealed(self) -> bool:
        return self.get_reveal_child()

    def set_revealed(self, revealed: bool) -> None:
        self.set_reveal_child(revealed)
