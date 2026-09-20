"""
The application object.

It owns the three things that outlive any one window: the catalogue
(`BooksManager`, shared unchanged with the web edition), the persisted
`Settings`, and the stylesheet.
"""

import os

from gi.repository import Adw, Gdk, Gio, Gtk

from books_manager import BooksManager

from . import icons
from .state import Settings
from .window import MainWindow

APP_ID = "org.interaktiv.School"
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)


class InteraktivApp(Adw.Application):
    __gtype_name__ = "InteraktivApp"

    def __init__(self, *, edition=None, library_dir=None, activities_cache_dir=None,
                 base_dir=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.manager = BooksManager(
            base_dir=base_dir or PROJECT_ROOT,
            library_dir=library_dir,
            activities_cache_dir=activities_cache_dir,
            edition=edition or "school",
        )
        self.settings = Settings()
        self.window = None

    # -- lifecycle --------------------------------------------------------

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        icons.register()
        self._load_css()
        self._install_actions()

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self)
        self.window.present()

    def do_shutdown(self) -> None:
        self.settings.flush()
        Adw.Application.do_shutdown(self)

    # -- chrome -----------------------------------------------------------

    def _load_css(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.css_provider = provider

    def _install_actions(self) -> None:
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
