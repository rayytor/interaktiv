"""
`python3 -m interaktiv_gtk --selftest`: can this machine run the reader?

Prints one line per thing the reader depends on and returns 0 only if all of
them are there. The board installer writes the output into its report, so a
visit to a board brings home what that board actually has.
"""

import os
import platform
import sys
from typing import Callable, List, Tuple


def _python() -> str:
    return platform.python_version()


def _gtk() -> str:
    from gi.repository import Gtk

    return f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"


def _adwaita() -> str:
    from gi.repository import Adw

    return f"{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}"


def _pymupdf() -> str:
    import pymupdf

    return f"{pymupdf.__version__} (MuPDF {pymupdf.mupdf_version})"


def _psutil() -> str:
    import psutil

    return psutil.__version__


def _webkit() -> str:
    import gi

    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit

    return f"{WebKit.get_major_version()}.{WebKit.get_minor_version()}"


def _display() -> str:
    from gi.repository import Gdk, Gtk

    if not Gtk.init_check():
        raise RuntimeError("no display")
    display = Gdk.Display.get_default()
    monitor = display.get_monitors().get_item(0)
    if monitor is None:
        return type(display).__name__
    geometry = monitor.get_geometry()
    return (f"{type(display).__name__}, {geometry.width}x{geometry.height} logical, "
            f"scale {monitor.get_scale_factor()}, {monitor.get_refresh_rate() / 1000:.0f} Hz")


def _catalogue() -> str:
    from interaktiv_core.catalogue import BooksManager

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    books = BooksManager(base_dir=root, edition="school").get_all_books()
    installed = [book for book in books if book.get("isInstalled")]
    return f"{len(books)} books, {len(installed)} installed"


def _render() -> str:
    """Open the first installed book and draw its second page, small."""
    import time

    import pymupdf

    from interaktiv_core.catalogue import BooksManager

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manager = BooksManager(base_dir=root, edition="school")
    for book in manager.get_all_books():
        path = manager.get_local_path(book["id"]) if book.get("isInstalled") else None
        if not path:
            continue
        started = time.monotonic()
        with pymupdf.open(path) as document:
            page = document[min(1, document.page_count - 1)]
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5), alpha=False)
            size = f"{pixmap.width}x{pixmap.height}"
        return f"{size} in {(time.monotonic() - started) * 1000:.0f} ms"
    return "no installed book to draw"


def _board() -> str:
    from . import board

    if not board.is_board():
        return "not an ETAP board"
    guard = board.RightClickGuard()
    return "ETAP board, right-click switch " + ("writable" if guard.available else "missing")


# (name, probe, whether the reader cannot run without it)
CHECKS: List[Tuple[str, Callable[[], str], bool]] = [
    ("python", _python, True),
    ("gtk", _gtk, True),
    ("libadwaita", _adwaita, True),
    ("pymupdf", _pymupdf, True),
    ("psutil", _psutil, False),
    ("webkitgtk", _webkit, False),
    ("display", _display, True),
    ("catalogue", _catalogue, True),
    ("render", _render, True),
    ("board", _board, False),
]


def run(out=sys.stdout) -> int:
    failed = False
    for name, probe, required in CHECKS:
        try:
            print(f"{name}: {probe()}", file=out)
        except Exception as error:
            status = "MISSING" if required else "not available"
            print(f"{name}: {status} ({type(error).__name__}: {error})", file=out)
            failed = failed or required
    print("selftest: " + ("FAILED" if failed else "ok"), file=out)
    return 1 if failed else 0
