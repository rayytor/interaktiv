"""
The one window.

A board runs one thing at a time, so the window is a single
`Adw.NavigationView`: the library is the root page and a book is pushed on top
of it. Closing the book pops back to exactly the catalogue that was left,
filter and scroll position intact, which is what the web edition's
show/hide of `#dashboard-view` was doing by hand.
"""

from gi.repository import Adw, Gio

from .library import LibraryPage
from .reader import ReaderPage
from .theme import apply_theme


class MainWindow(Adw.ApplicationWindow):
    __gtype_name__ = "InteraktivWindow"

    def __init__(self, app):
        super().__init__(application=app, title="Interaktiv")
        self.app = app
        self.settings = app.settings

        self.set_default_size(
            self.settings.get("window_width"), self.settings.get("window_height")
        )
        if self.settings.get("window_maximized"):
            self.maximize()

        self.navigation = Adw.NavigationView()
        self.library = LibraryPage(app)
        self.library.connect("open-book", self._on_open_book)
        self.navigation.add(self.library)
        # A popped reader is done with: its render thread and its textures
        # are released here rather than left to the garbage collector,
        # which on a board is the difference between going back to the
        # library and going back to a library with half a gigabyte of
        # MuPDF store still resident.
        self.navigation.connect("popped", self._on_popped)
        self.set_content(self.navigation)

        self.apply_theme(self.settings.get("theme") or "dark")
        self._install_actions()
        self.connect("close-request", self._on_close)

    def apply_theme(self, theme: str) -> None:
        apply_theme(theme, self)

    # -- actions ----------------------------------------------------------

    def _install_actions(self) -> None:
        search = Gio.SimpleAction.new("search", None)
        search.connect("activate", lambda *_: self._focus_search())
        self.add_action(search)

        back = Gio.SimpleAction.new("back", None)
        back.connect("activate", lambda *_: self.navigation.pop())
        self.add_action(back)

        app = self.get_application()
        app.set_accels_for_action("win.search", ["<Control>f"])
        # Not Escape: a window accelerator is seen before the focused widget,
        # so Escape here would close the book instead of clearing the search
        # box or dismissing a dialog. The reader binds its own Escape in M4,
        # where it means "leave focus mode".
        app.set_accels_for_action("win.back", ["<Alt>Left"])

    def _focus_search(self) -> None:
        page = self.navigation.get_visible_page()
        if page is self.library:
            self.library.focus_search()
        elif isinstance(page, ReaderPage):
            page.toggle_search()

    # -- navigation -------------------------------------------------------

    def _on_open_book(self, _library, item, path: str) -> None:
        self.navigation.push(ReaderPage(self.app, item, path))

    def _on_popped(self, _navigation, page) -> None:
        if isinstance(page, ReaderPage):
            page.shutdown()

    # -- lifecycle --------------------------------------------------------

    def _on_close(self, *_args) -> bool:
        self.settings.update(
            window_maximized=self.is_maximized(),
        )
        if not self.is_maximized():
            self.settings.update(
                window_width=self.get_width(),
                window_height=self.get_height(),
            )
        page = self.navigation.get_visible_page()
        if isinstance(page, ReaderPage):
            page.shutdown()
        self.library.shutdown()
        self.settings.flush()
        return False
