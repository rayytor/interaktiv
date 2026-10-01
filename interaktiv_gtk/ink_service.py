"""
What Rayyanpen, the overlay pen, talks to.

Rayyanpen draws on the whole screen. When a stroke ends over this window, it
asks here whether the stroke lands on a page of the open book; if it does, the
reader keeps the stroke in the book (see `reader/ink_controller.py`) and
Rayyanpen drops its own copy, so the drawing moves and scales with the page
from then on. Its eraser, undo and redo reach the book the same way, and so do
the two-finger pinch and pan a teacher makes over the book with the pen out.
`Surfaces` tells it where the pages are and which controls lie over them, so
that it takes input only there and every button here works with its pen out.

The object is `/org/interaktiv/School/Ink` on the application's own bus name,
`org.interaktiv.School`, interface `org.interaktiv.School.Ink`.

**Coordinates** are physical pixels relative to the X window named by `xid`,
which is what Rayyanpen gets from `xcb_translate_coordinates` onto the client
window it found under the stroke. A call about any other window -- another
program, a dialog of this one -- is refused, as is everything when the window
is not an X11 one. The window's scale factor and its client-side shadow are
taken off here, which leaves the `Gtk.Window`'s own logical coordinates.
"""

from typing import Callable, Optional, Tuple

from gi.repository import Gio, GLib

OBJECT_PATH = "/org/interaktiv/School/Ink"
INTERFACE = "org.interaktiv.School.Ink"

INTROSPECTION = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Accepts">
      <arg direction="in" type="t" name="xid"/>
      <arg direction="in" type="d" name="x"/>
      <arg direction="in" type="d" name="y"/>
      <arg direction="out" type="b" name="accepted"/>
    </method>
    <method name="AddStroke">
      <arg direction="in" type="t" name="xid"/>
      <arg direction="in" type="s" name="kind"/>
      <arg direction="in" type="s" name="rgba"/>
      <arg direction="in" type="d" name="width"/>
      <arg direction="in" type="a(dd)" name="points"/>
      <arg direction="out" type="s" name="id"/>
    </method>
    <method name="RemoveStrokes">
      <arg direction="in" type="as" name="ids"/>
      <arg direction="out" type="u" name="count"/>
    </method>
    <method name="RestoreStrokes">
      <arg direction="in" type="as" name="ids"/>
      <arg direction="out" type="u" name="count"/>
    </method>
    <method name="Erase">
      <arg direction="in" type="s" name="session"/>
      <arg direction="in" type="t" name="xid"/>
      <arg direction="in" type="d" name="radius"/>
      <arg direction="in" type="a(dd)" name="points"/>
      <arg direction="out" type="u" name="erased"/>
    </method>
    <method name="UndoErase">
      <arg direction="in" type="s" name="session"/>
      <arg direction="out" type="b" name="done"/>
    </method>
    <method name="RedoErase">
      <arg direction="in" type="s" name="session"/>
      <arg direction="out" type="b" name="done"/>
    </method>
    <method name="ClearPage">
      <arg direction="in" type="t" name="xid"/>
      <arg direction="in" type="d" name="x"/>
      <arg direction="in" type="d" name="y"/>
      <arg direction="out" type="s" name="token"/>
    </method>
    <method name="Gesture">
      <arg direction="in" type="t" name="xid"/>
      <arg direction="in" type="s" name="phase"/>
      <arg direction="in" type="d" name="x"/>
      <arg direction="in" type="d" name="y"/>
      <arg direction="in" type="d" name="scale"/>
      <arg direction="out" type="b" name="handled"/>
    </method>
    <method name="Surfaces">
      <arg direction="in" type="t" name="xid"/>
      <arg direction="out" type="ad" name="pages"/>
      <arg direction="out" type="ad" name="controls"/>
    </method>
  </interface>
</node>
"""


def window_xid(window) -> Optional[int]:
    """The X window id of a `Gtk.Window`, or None off X11."""
    surface = window.get_surface() if window is not None else None
    if surface is None:
        return None
    try:
        import gi

        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
    except (ImportError, ValueError):
        return None
    if not isinstance(surface, GdkX11.X11Surface):
        return None
    return int(surface.get_xid())


def to_window(window, x: float, y: float) -> Tuple[float, float]:
    """X window physical pixels to the `Gtk.Window`'s logical coordinates."""
    surface = window.get_surface()
    scale = max(1, surface.get_scale_factor()) if surface is not None else 1
    shadow_x, shadow_y = window.get_surface_transform()
    return (x / scale - shadow_x, y / scale - shadow_y)


class InkService:
    """
    The D-Bus object. `reader` returns the reader page on screen, if a book is
    open, and `window` the application window.
    """

    def __init__(self, window: Callable, reader: Callable) -> None:
        self._window = window
        self._reader = reader
        self._registration = 0
        self._connection = None
        self._info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION).interfaces[0]

    def register(self, connection: Gio.DBusConnection) -> None:
        self._connection = connection
        self._registration = connection.register_object(
            OBJECT_PATH, self._info, self._on_call, None, None
        )

    def unregister(self) -> None:
        if self._connection is not None and self._registration:
            self._connection.unregister_object(self._registration)
        self._registration = 0
        self._connection = None

    # -- the calls ---------------------------------------------------------

    def _mapper(self, xid: int):
        """A point converter for `xid` if it is this app's main window."""
        window = self._window()
        if window is None or window_xid(window) != xid:
            return None
        return lambda x, y: to_window(window, x, y)

    def _ink(self):
        reader = self._reader()
        return getattr(reader, "ink_controller", None), reader

    def handle(self, method: str, args: tuple):
        """Answer one call, as a Python tuple matching the out arguments."""
        ink, reader = self._ink()

        if method in ("RemoveStrokes", "RestoreStrokes"):
            if ink is None:
                return (0,)
            fn = ink.remove if method == "RemoveStrokes" else ink.restore
            return (fn(list(args[0])),)
        if method in ("UndoErase", "RedoErase"):
            if ink is None:
                return (False,)
            fn = ink.undo_erase if method == "UndoErase" else ink.redo_erase
            return (bool(fn(args[0])),)

        xid = args[1] if method == "Erase" else args[0]
        to_win = self._mapper(xid)
        if method == "Surfaces":
            # Flat x, y, width, height runs, in the X window's physical pixels.
            if to_win is None or ink is None:
                return ([], [])
            pages, controls = ink.surfaces()
            return (self._from_window(pages), self._from_window(controls))
        refused = {
            "Accepts": (False,), "AddStroke": ("",), "Erase": (0,),
            "ClearPage": ("",), "Gesture": (False,),
        }[method]
        if to_win is None or ink is None:
            return refused

        if method == "Accepts":
            return (ink.accepts(*to_win(args[1], args[2])),)
        if method == "AddStroke":
            _xid, kind, rgba, width, points = args
            scale = self._scale()
            pts = [to_win(x, y) for x, y in points]
            return (ink.add_stroke(kind, rgba, width / scale, pts),)
        if method == "Erase":
            session, _xid, radius, points = args
            pts = [to_win(x, y) for x, y in points]
            return (ink.erase(session, radius / self._scale(), pts),)
        if method == "ClearPage":
            # Rayyanpen's Clear: the drawings on every page on screen.
            return (ink.clear_pages(reader.visible_pages()) or "",)
        if method == "Gesture":
            _xid, phase, x, y, scale = args
            if phase not in ("begin", "update", "end"):
                return (False,)
            return (bool(reader.gesture_at_window(phase, *to_win(x, y), scale)),)
        return refused

    def _from_window(self, rects) -> list:
        window = self._window()
        scale = self._scale()
        shadow_x, shadow_y = window.get_surface_transform()
        out = []
        for x, y, w, h in rects:
            out += [(x + shadow_x) * scale, (y + shadow_y) * scale, w * scale, h * scale]
        return out

    def _scale(self) -> int:
        window = self._window()
        surface = window.get_surface() if window is not None else None
        return max(1, surface.get_scale_factor()) if surface is not None else 1

    def _on_call(self, _connection, _sender, _path, _interface, method,
                 parameters, invocation) -> None:
        method_info = self._info.lookup_method(method)
        if method_info is None:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod", method
            )
            return
        try:
            result = self.handle(method, parameters.unpack())
        except Exception as error:  # a bad call must not take the lesson down
            invocation.return_dbus_error(
                "org.interaktiv.School.Error.Failed", str(error)
            )
            return
        signature = "(" + "".join(a.signature for a in method_info.out_args) + ")"
        invocation.return_value(GLib.Variant(signature, result))
