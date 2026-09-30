"""A panel that slides in over the left edge of its content."""

from gi.repository import GObject, Gtk

TRANSITION_MS = 150


class SidePanel(Gtk.Overlay):
    """
    The panel lies over the content instead of beside it, so opening it does
    not change the content's width and the open pages are not re-rendered.
    While it is open the rest of the content is dimmed, and a tap there closes
    it again.
    """

    __gtype_name__ = "InteraktivSidePanel"

    show_sidebar = GObject.Property(type=bool, default=False)

    def __init__(self, sidebar: Gtk.Widget, content: Gtk.Widget,
                 width: int = 360, show_sidebar: bool = False):
        super().__init__()
        self.set_child(content)

        self.scrim = Gtk.Box(hexpand=True, vexpand=True, visible=False)
        self.scrim.add_css_class("side-scrim")
        tap = Gtk.GestureClick()
        tap.connect("released", lambda *_: self.set_show_sidebar(False))
        self.scrim.add_controller(tap)
        self.add_overlay(self.scrim)

        sidebar.set_size_request(width, -1)
        self.revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT,
            transition_duration=TRANSITION_MS,
            halign=Gtk.Align.START,
            child=sidebar,
        )
        self.add_overlay(self.revealer)

        self.connect("notify::show-sidebar", self._on_show_changed)
        self.show_sidebar = show_sidebar

    def get_show_sidebar(self) -> bool:
        return self.show_sidebar

    def set_show_sidebar(self, show: bool) -> None:
        self.show_sidebar = bool(show)

    def _on_show_changed(self, *_args) -> None:
        self.revealer.set_reveal_child(self.show_sidebar)
        self.scrim.set_visible(self.show_sidebar)
