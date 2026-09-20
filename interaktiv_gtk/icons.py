"""
The icon names the UI asks for, in one place.

Every name below is part of the Adwaita icon theme that ships with GTK, so a
board with no third-party icon theme installed still draws a complete UI. Names
that Adwaita does not carry get an SVG in `icons/`, which `register()` puts on
the icon theme's search path.
"""

import os

from gi.repository import Gdk, Gtk

BOOK = "x-office-document-symbolic"
OPEN = "document-open-symbolic"
PREVIEW = "view-reveal-symbolic"
INSTALL = "folder-download-symbolic"
UNINSTALL = "user-trash-symbolic"
CANCEL = "window-close-symbolic"
SEARCH = "system-search-symbolic"
BACK = "go-previous-symbolic"
REFRESH = "view-refresh-symbolic"

# Reader
PREV_PAGE = "go-previous-symbolic"
NEXT_PAGE = "go-next-symbolic"
FIRST_PAGE = "go-first-symbolic"
LAST_PAGE = "go-last-symbolic"
ZOOM_IN = "zoom-in-symbolic"
ZOOM_OUT = "zoom-out-symbolic"
ZOOM_FIT = "zoom-fit-best-symbolic"
ROTATE = "object-rotate-right-symbolic"
SIDEBAR = "sidebar-show-symbolic"
MODE_BOOK = "view-dual-symbolic"
MODE_SINGLE = "view-paged-symbolic"
MODE_SCROLL = "view-continuous-symbolic"
FULLSCREEN = "view-fullscreen-symbolic"
RESTORE = "view-restore-symbolic"
BROWSER = "web-browser-symbolic"
# The activity overlay toggle: "show me the regions", which is what
# selection-mode means everywhere else in the platform.
ACTIVITIES = "selection-mode-symbolic"


def register() -> None:
    """Put the app's own icons ahead of the theme's, if it ships any."""
    directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    if not os.path.isdir(directory):
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    Gtk.IconTheme.get_for_display(display).add_search_path(directory)
