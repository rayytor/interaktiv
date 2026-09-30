"""
Interaktiv, the GTK4 / libadwaita textbook reader for classroom smart boards.

Books are baked ahead of time: every activity region is precomputed into
`activities/books/<id>/regions.json`, so nothing here detects anything. The
package is the reader itself: a PDF canvas, a hotspot overlay, a focus zoom,
the book catalogue and a window for the publisher's HTML activities.
"""

__version__ = "0.9.0"

# The GTK and libadwaita versions are settled here, before any module of the
# package touches `gi.repository`. A missing toolkit is kept rather than
# raised so that `__main__` can say so in a window instead of a traceback.
TOOLKIT_ERROR = None
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    # Gdk is normally settled by loading Gtk, but `touch` names it first.
    gi.require_version("Gdk", "4.0")
except (ImportError, ValueError) as error:
    TOOLKIT_ERROR = error
