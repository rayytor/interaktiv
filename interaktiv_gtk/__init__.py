"""
The native School Edition front end.

School Edition's books are baked: every activity region is precomputed into
`activities/books/<id>/regions.json`, so nothing here detects anything. What is
left -- a PDF canvas, a hotspot overlay, a focus zoom, a catalogue and an
embedded browser for the publisher's HTML activities -- is a GTK4/Libadwaita
application, which is what a classroom smartboard should be running.

Interaktiv is a native GTK4/Libadwaita application engineered for
school and smartboard environments.
"""

__version__ = "0.1.0"

import gi

# Pinned here rather than in each module: importing any part of this package
# must settle the GTK and libadwaita versions before `gi.repository` is touched.
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
# Gdk is normally settled as a side effect of loading Gtk, but a module that
# names it first -- `touch`, reaching for `Gdk.InputSource` -- gets there
# before Gtk has been imported and is warned at for guessing.
gi.require_version("Gdk", "4.0")
