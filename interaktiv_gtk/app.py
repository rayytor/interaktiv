"""
The application object.

It owns the things that outlive any one window: the catalogue
(`BooksManager`), the persisted `Settings`, and the stylesheet.
"""

import os

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from interaktiv_core.catalogue import BooksManager

from . import fonts, icons
from .board import RightClickGuard
from .ink_service import InkService
from .state import Settings
from .window import MainWindow

APP_ID = "org.interaktiv.School"

# Finger-sized rather than mouse-sized. See `_tune_for_touch`.
TOUCH_DRAG_THRESHOLD = 16
# A double tap by a finger: further apart and slower than a mouse's double
# click, which is 5 px and 400 ms in GTK's defaults.
TOUCH_DOUBLE_TAP_DISTANCE = 40
TOUCH_DOUBLE_TAP_TIME = 400

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)


class InteraktivApp(Adw.Application):
    __gtype_name__ = "InteraktivApp"

    def __init__(self, *, edition=None, library_dir=None, activities_cache_dir=None,
                 base_dir=None, debug=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.manager = BooksManager(
            base_dir=base_dir or PROJECT_ROOT,
            library_dir=library_dir,
            activities_cache_dir=activities_cache_dir,
            edition=edition or "school",
        )
        self.settings = Settings()
        self.debug = bool(debug)
        self.right_click_guard = RightClickGuard()
        self.window = None
        # Rayyanpen hands its strokes over this (see ink_service.py).
        self.ink_service = InkService(lambda: self.window, self.current_reader)

    def current_reader(self):
        """The reader page on screen, if a book is open."""
        from .reader import ReaderPage

        window = self.window
        page = window.navigation.get_visible_page() if window is not None else None
        return page if isinstance(page, ReaderPage) else None

    def do_dbus_register(self, connection, object_path) -> bool:
        if not Adw.Application.do_dbus_register(self, connection, object_path):
            return False
        try:
            self.ink_service.register(connection)
        except Exception:
            # Drawing on the book is an extra; the reader works without it.
            pass
        return True

    def do_dbus_unregister(self, connection, object_path) -> None:
        self.ink_service.unregister()
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    # -- lifecycle --------------------------------------------------------

    def do_startup(self) -> None:
        # The window's X class is how the desktop matches it to its menu entry
        # (StartupWMClass) and how Rayyanpen knows a stroke is over the book.
        # Run as `python3 -m`, GTK would otherwise call it "python3".
        GLib.set_prgname(APP_ID)
        Adw.Application.do_startup(self)
        self._set_x11_class()
        self.right_click_guard.clean_stale()
        fonts.register()
        icons.register()
        self._tune_for_touch()
        self._load_css()
        self._install_actions()

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self)
            # ETAP's long-press right click is a desktop-wide switch, so it is
            # only held off while this window is the one being touched.
            self.window.connect("notify::is-active", self._on_window_active)
        self.window.present()

    @staticmethod
    def _set_x11_class() -> None:
        display = Gdk.Display.get_default()
        try:
            import gi

            gi.require_version("GdkX11", "4.0")
            from gi.repository import GdkX11
        except (ImportError, ValueError):
            return
        if isinstance(display, GdkX11.X11Display):
            GdkX11.X11Display.set_program_class(display, APP_ID)

    def _on_window_active(self, window, _param) -> None:
        if window.is_active():
            self.right_click_guard.acquire()
        else:
            self.right_click_guard.release()

    def do_shutdown(self) -> None:
        self.right_click_guard.release()
        reader = self.current_reader()
        if reader is not None:
            reader.ink_controller.flush()
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
            settings.set_property("gtk-double-click-distance", TOUCH_DOUBLE_TAP_DISTANCE)
            settings.set_property("gtk-double-click-time", TOUCH_DOUBLE_TAP_TIME)

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
