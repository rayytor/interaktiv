"""
The native School Edition front end.

School Edition's books are baked: every activity region is precomputed into
`activities/books/<id>/regions.json`, so nothing here detects anything. What is
left -- a PDF canvas, a hotspot overlay, a focus zoom, a catalogue and an
embedded browser for the publisher's HTML activities -- is a GTK4/Libadwaita
application, which is what a classroom smartboard should be running.

The web UI (`index.html`, `js/`, `server.py`) is untouched and remains the
authoring edition. The two editions share `interaktiv_core` and
`books_manager.py`, not a front end.
"""

__version__ = "0.1.0"

import gi

# Pinned here rather than in each module: importing any part of this package
# must settle the GTK and libadwaita versions before `gi.repository` is touched.
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
