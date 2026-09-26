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

# Finger-sized rather than mouse-sized. See `_tune_for_touch`.
TOUCH_DRAG_THRESHOLD = 16

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
        self._tune_for_touch()
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

    def _tune_for_touch(self) -> None:
        """
        Settle the one GTK setting that is sized for a mouse.

        `gtk-dnd-drag-threshold` is how far a press has to travel before GTK
        calls it a drag, and 8 px is a mouse's answer. A fingertip on a board
        wobbles further than that just being held still, which starts a scroll
        under a teacher who meant to tap -- and it is also all the travel the
        page swipe gets to read a direction from, because `GtkScrolledWindow`
        claims the sequence on the first motion past this number. Widening it
        fixes both: fewer taps become scrolls, and the swipe gets a sample
        long enough to tell sideways from downwards.
        """
        display = Gdk.Display.get_default()
        if display is None:
            return
        settings = Gtk.Settings.get_for_display(display)
        if settings is not None:
            settings.set_property("gtk-dnd-drag-threshold", TOUCH_DRAG_THRESHOLD)

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
