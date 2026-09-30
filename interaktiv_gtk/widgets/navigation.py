"""The window's pages: a root page and at most one page pushed on top of it."""

from typing import Optional

from gi.repository import GObject, Gtk

TRANSITION_MS = 150


class PageStack(Gtk.Stack):
    __gtype_name__ = "InteraktivPageStack"
    __gsignals__ = {
        # The page that was popped, once it is off screen and out of the stack.
        "popped": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            transition_duration=TRANSITION_MS,
        )
        self._root: Optional[Gtk.Widget] = None
        self._leaving: Optional[Gtk.Widget] = None
        self.connect("notify::transition-running", self._on_transition)

    def set_root_page(self, page: Gtk.Widget) -> None:
        self._root = page
        self.add_child(page)
        self.set_visible_child(page)

    def get_visible_page(self) -> Optional[Gtk.Widget]:
        return self.get_visible_child()

    def push(self, page: Gtk.Widget) -> None:
        self._finish_pop()
        self.add_child(page)
        self.set_visible_child(page)
        page.grab_focus()

    def pop(self) -> bool:
        page = self.get_visible_child()
        if page is None or page is self._root or self._leaving is not None:
            return False
        self._leaving = page
        self.set_visible_child(self._root)
        # With animations off there is no transition to wait for.
        if not self.get_transition_running():
            self._finish_pop()
        return True

    def _on_transition(self, *_args) -> None:
        if not self.get_transition_running():
            self._finish_pop()

    def _finish_pop(self) -> None:
        page, self._leaving = self._leaving, None
        if page is None:
            return
        self.remove(page)
        self.emit("popped", page)
