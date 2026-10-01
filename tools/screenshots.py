#!/usr/bin/env python3
"""
Take screenshots of every screen of the app, without a desktop.

    python3 tools/screenshots.py --out /tmp/shots
    python3 tools/screenshots.py --out /tmp/shots --scale 2 --theme light

The app runs under Xvfb with its own throwaway settings and cache, so neither
the pictures nor the run depend on the state of the machine they are taken on.
`--scale 2` is a board: 3840 x 2160 at 200 %. The pictures are read from the
X server's own framebuffer, so they include popovers and are what a screen
would show.

Used to review UI changes and to produce the pictures in the README.
"""

import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The logical size of a board's screen at 200 %, less the desktop's panel.
DEFAULT_SIZE = (1920, 1020)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--out", required=True, help="directory the PNGs are written to")
    parser.add_argument("--theme", default="dark",
                        choices=["dark", "light", "sepia", "inverted"])
    parser.add_argument("--scale", type=int, default=1, choices=[1, 2])
    parser.add_argument("--size", default="%dx%d" % DEFAULT_SIZE,
                        help="window size in logical pixels, WIDTHxHEIGHT")
    parser.add_argument("--book", default=None, help="book id; default: first installed")
    parser.add_argument("--page", type=int, default=24)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def save_framebuffer(framebuffer: str, path: str, width: int, height: int) -> None:
    """Write the top-left `width` x `height` of Xvfb's framebuffer file as a PNG."""
    with open(framebuffer, "rb") as handle:
        data = handle.read()
    header = struct.unpack(">25I", data[:100])
    header_size, screen_w, screen_h = header[0], header[4], header[5]
    bits_per_pixel, bytes_per_line, colours = header[11], header[12], header[19]
    if bits_per_pixel != 32:
        raise RuntimeError(f"unexpected framebuffer depth: {bits_per_pixel} bits per pixel")
    start = header_size + colours * 12
    width, height = min(width, screen_w), min(height, screen_h)

    rows = bytearray()
    for y in range(height):
        row = data[start + y * bytes_per_line:start + y * bytes_per_line + width * 4]
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]  # BGRX -> RGB
        rows += b"\x00" + rgb
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        handle.write(_png_chunk(b"IDAT", zlib.compress(bytes(rows), 6)))
        handle.write(_png_chunk(b"IEND", b""))


def relaunch_under_xvfb(args) -> int:
    """Run this script again inside Xvfb with throwaway settings."""
    if not shutil.which("xvfb-run"):
        print("xvfb-run is not installed (Debian: apt install xvfb).", file=sys.stderr)
        return 2
    width, height = (int(n) for n in args.size.split("x"))
    os.makedirs(args.out, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="interaktiv-shots-") as home:
        env = dict(os.environ)
        env.update({
            "XDG_CONFIG_HOME": os.path.join(home, "config"),
            "XDG_CACHE_HOME": os.path.join(home, "cache"),
            "GDK_BACKEND": "x11",
            "GSK_RENDERER": "cairo",
            "GDK_SCALE": str(args.scale),
            "GTK_A11Y": "none",
        })
        framebuffer_dir = os.path.join(home, "fb")
        os.makedirs(framebuffer_dir)
        env["INTERAKTIV_FRAMEBUFFER"] = os.path.join(framebuffer_dir, "Xvfb_screen0")
        screen = "%dx%dx24" % (width * args.scale, height * args.scale)
        command = [
            "xvfb-run", "-a", "-s", f"-screen 0 {screen} -fbdir {framebuffer_dir}",
            sys.executable, os.path.abspath(__file__), "--inside",
            "--out", os.path.abspath(args.out), "--theme", args.theme,
            "--scale", str(args.scale), "--size", args.size, "--page", str(args.page),
        ]
        if args.book:
            command += ["--book", args.book]
        return subprocess.call(command, env=env, cwd=ROOT)


def run_inside(args) -> int:
    sys.path.insert(0, ROOT)
    import json

    from interaktiv_core import appdirs

    width, height = (int(n) for n in args.size.split("x"))
    os.makedirs(appdirs.config_dir(), exist_ok=True)
    with open(appdirs.state_path(), "w", encoding="utf-8") as handle:
        json.dump({"window_width": width, "window_height": height,
                   "window_maximized": False, "theme": args.theme}, handle)

    import interaktiv_gtk  # noqa: F401  (pins the GTK versions)
    from gi.repository import GLib, Gtk

    from interaktiv_gtk.app import InteraktivApp

    app = InteraktivApp(edition="school")
    failures = []

    def shot(name):
        path = os.path.join(args.out, f"{name}.png")
        save_framebuffer(
            os.environ["INTERAKTIV_FRAMEBUFFER"], path,
            width * args.scale, height * args.scale,
        )
        print(path, flush=True)

    def reader():
        return app.window.navigation.get_visible_page()

    def open_book():
        library = app.window.library
        items = [i for i in library.model.items if i.is_installed]
        if args.book:
            items = [i for i in items if i.id.startswith(args.book)]
        if not items:
            raise RuntimeError("no installed book to open")
        library.open_book(items[0])

    def focus_activity():
        page = reader()
        for number in (page.current_page, page.current_page + 1):
            overlay = page.session.overlay(number)
            if overlay and overlay.activities:
                page.focus_activity(overlay.activities[0], part_index=0)
                return
        raise RuntimeError(f"no activity on page {page.current_page}")

    def sidebar(tab):
        def show():
            page = reader()
            page.split.set_show_sidebar(tab is not None)
            if tab is not None:
                page.sidebar.switcher.set_active_name(tab)
        return show

    def show_help():
        reader().show_help()

    def close_extra_windows():
        for window in Gtk.Window.get_toplevels():
            if window is not app.window:
                window.destroy()

    def popover(name):
        def show():
            getattr(reader().dock, name).popup()
        return show

    def close_popover(name):
        def close():
            getattr(reader().dock, name).popdown()
        return close

    def close_book():
        app.window.navigation.pop()

    # (milliseconds to wait first, what to do)
    steps = [
        (2500, lambda: shot("01-library")),
        (200, open_book),
        (6000, lambda: reader().go_to_page(args.page)),
        (4000, lambda: shot("02-reader")),
        (200, popover("page_popover")),
        (1200, lambda: shot("03-go-to-page")),
        (200, close_popover("page_popover")),
        (400, popover("more_popover")),
        (1200, lambda: shot("04-more")),
        (200, close_popover("more_popover")),
        (400, sidebar("thumbnails")),
        (3500, lambda: shot("05-sidebar-pages")),
        (200, sidebar("bookmarks")),
        (1500, lambda: shot("06-sidebar-contents")),
        (200, sidebar("activities")),
        (2500, lambda: shot("07-sidebar-activities")),
        (200, sidebar(None)),
        (500, focus_activity),
        (5000, lambda: shot("08-focus")),
        (200, lambda: reader().exit_focus()),
        (500, lambda: reader().set_view_mode("single")),
        (3000, lambda: shot("09-single-page")),
        (200, lambda: reader().set_view_mode("scroll")),
        (4000, lambda: shot("10-scroll")),
        (200, lambda: reader().set_view_mode("book")),
        (500, show_help),
        (1500, lambda: shot("11-help")),
        (200, close_extra_windows),
        (300, close_book),
        (2500, lambda: shot("12-library-continue")),
        (200, lambda: app.window.library.show_about()),
        (1500, lambda: shot("13-about")),
        (200, close_extra_windows),
        (200, app.quit),
    ]

    def run(index=0):
        if index >= len(steps):
            return
        delay, action = steps[index]

        def go():
            try:
                action()
            except Exception as error:  # keep going: one bad screen is not all of them
                failures.append(f"step {index}: {error!r}")
                print(failures[-1], file=sys.stderr, flush=True)
            run(index + 1)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(delay, go)

    app.connect("activate", lambda *_: run())
    app.run([])
    return 1 if failures else 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.inside:
        return run_inside(args)
    return relaunch_under_xvfb(args)


if __name__ == "__main__":
    raise SystemExit(main())
