#!/bin/sh
# Starts Interaktiv from this source checkout. On a board, use the bundle from
# packaging/board/ instead.
cd "$(dirname "$0")" || exit 1
# The boards are X11. On a Wayland desktop run through XWayland too, so that
# Rayyanpen (which GNOME also runs through XWayland) can find the book's window
# and hand its strokes to it; a Wayland window has no position it could ask for.
# INTERAKTIV_GDK_BACKEND=wayland ./launch.sh runs it as a Wayland window anyway.
[ -n "$DISPLAY" ] && export GDK_BACKEND="${INTERAKTIV_GDK_BACKEND:-x11}"
exec python3 -m interaktiv_gtk "$@"
