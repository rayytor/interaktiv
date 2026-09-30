"""
Drawing a page's ink.

GTK 4.8, the oldest board's toolkit, has no path API and the boards do not
reliably have pycairo, so ink is built from the primitives GSK has had from
the start: a line segment is a rotated rectangle, a round joint or tip is a
clipped circle, a rectangle or an ellipse is a border, and an arrowhead is a
border too, drawn as the CSS border triangle.

A page's strokes become one render node, built once in page space (PDF points,
y down) and placed on the page with a single transform. Zooming and scrolling
change only the transform, so a pinch never rebuilds the ink, and GSK draws it
at the screen's resolution, so it stays sharp at any zoom.

The look follows Rayyanpen's renderer (draw-on-screen `src/core/Renderer.cpp`)
so that a stroke does not visibly change when it moves from the overlay onto
the page: round caps and joins, a marker at half opacity whose overlaps do not
darken, the outer edge of a rectangle or ellipse on the dragged box, and an
arrowhead 6 widths long with a 22.5° half angle.
"""

import math
from typing import Iterable, Optional, Sequence, Tuple

from gi.repository import Gdk, Graphene, Gsk, Gtk

from interaktiv_core.geometry import PageTransform
from interaktiv_core.ink import InkStroke

MARKER_OPACITY = 0.5
ARROW_HEAD_PER_WIDTH = 6.0
ARROW_HALF_ANGLE = math.pi / 8
# A joint that turns less than this needs no round cap: the gap on the outside
# of the bend is under a twentieth of the width.
JOINT_MIN_TURN = math.radians(6)


def parse_rgba(spec: str) -> Gdk.RGBA:
    """`#rrggbb` or `#rrggbbaa`. Parsed by hand: GDK 4.8 may not take the alpha."""
    colour = Gdk.RGBA()
    s = spec.strip().lstrip("#")
    try:
        if len(s) not in (6, 8):
            raise ValueError(spec)
        colour.red = int(s[0:2], 16) / 255
        colour.green = int(s[2:4], 16) / 255
        colour.blue = int(s[4:6], 16) / 255
        colour.alpha = int(s[6:8], 16) / 255 if len(s) == 8 else 1.0
    except ValueError:
        colour.red = colour.green = colour.blue = 0.0
        colour.alpha = 1.0
    return colour


def _rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, w, h)


def _point(x: float, y: float) -> Graphene.Point:
    return Graphene.Point().init(x, y)


def _dot(snap: Gtk.Snapshot, colour: Gdk.RGBA, x: float, y: float, d: float) -> None:
    bounds = _rect(x - d / 2, y - d / 2, d, d)
    rounded = Gsk.RoundedRect()
    rounded.init_from_rect(bounds, d / 2)
    snap.push_rounded_clip(rounded)
    snap.append_color(colour, bounds)
    snap.pop()


def _segment(snap, colour, ax, ay, bx, by, w) -> None:
    length = math.hypot(bx - ax, by - ay)
    if length <= 0:
        return
    snap.save()
    snap.translate(_point(ax, ay))
    snap.rotate(math.degrees(math.atan2(by - ay, bx - ax)))
    snap.append_color(colour, _rect(0, -w / 2, length, w))
    snap.restore()


def _polyline(snap, colour, pts: Sequence[Tuple[float, float]], w: float) -> None:
    if len(pts) == 1 or all(p == pts[0] for p in pts):
        _dot(snap, colour, pts[0][0], pts[0][1], w)
        return
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        _segment(snap, colour, ax, ay, bx, by, w)
    _dot(snap, colour, pts[0][0], pts[0][1], w)
    _dot(snap, colour, pts[-1][0], pts[-1][1], w)
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        h1 = math.atan2(b[1] - a[1], b[0] - a[0])
        h2 = math.atan2(c[1] - b[1], c[0] - b[0])
        turn = abs((h2 - h1 + math.pi) % (2 * math.pi) - math.pi)
        if turn >= JOINT_MIN_TURN:
            _dot(snap, colour, b[0], b[1], w)


def _box(snap, colour, a, b, w, ellipse: bool) -> None:
    x0, x1 = sorted((a[0], b[0]))
    y0, y1 = sorted((a[1], b[1]))
    bw, bh = x1 - x0, y1 - y0
    bounds = _rect(x0, y0, bw, bh)
    rounded = Gsk.RoundedRect()
    if ellipse:
        corner = Graphene.Size().init(bw / 2, bh / 2)
        rounded.init(bounds, corner, corner, corner, corner)
    else:
        rounded.init_from_rect(bounds, 0)
    if bw <= w or bh <= w:
        # Too small to have a hole: a filled box or blob, as Rayyanpen draws it.
        snap.push_rounded_clip(rounded)
        snap.append_color(colour, bounds)
        snap.pop()
        return
    snap.append_border(rounded, [w] * 4, [colour] * 4)


def _arrow(snap, colour, a, b, w) -> None:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        return
    ux, uy = dx / length, dy / length
    head = ARROW_HEAD_PER_WIDTH * w
    tip = (b[0] + ux * head / 4, b[1] + uy * head / 4)
    base = (tip[0] - ux * head, tip[1] - uy * head)
    if length > head * 3 / 4:
        _segment(snap, colour, a[0], a[1], base[0], base[1], w)
        _dot(snap, colour, a[0], a[1], w)
    half = head * math.sin(ARROW_HALF_ANGLE)
    # Frame: tip at the origin, the arrow pointing along +x. The head is the
    # CSS border triangle: a box that is all left border, whose top and bottom
    # borders (transparent) meet it on the diagonals at the tip. Clips cannot
    # do this, because GSK widens a rotated clip to its bounding box.
    clear = Gdk.RGBA()
    clear.alpha = 0.0
    rounded = Gsk.RoundedRect()
    rounded.init_from_rect(_rect(-head, -half, head, 2 * half), 0)
    snap.save()
    snap.translate(_point(*tip))
    snap.rotate(math.degrees(math.atan2(uy, ux)))
    snap.append_border(rounded, [half, 0, half, head], [clear, clear, clear, colour])
    snap.restore()


def append_stroke(snap: Gtk.Snapshot, stroke: InkStroke, page_h: float) -> None:
    """One stroke, in page space: PDF points with y flipped to grow downward."""
    colour = parse_rgba(stroke.rgba)
    opacity = colour.alpha * (MARKER_OPACITY if stroke.kind == "marker" else 1.0)
    # Drawn opaque inside one opacity group, so that where a stroke crosses
    # itself it does not get darker -- the marker especially.
    if opacity < 1.0:
        colour.alpha = 1.0
        snap.push_opacity(opacity)
    pts = [(x, page_h - y) for x, y in stroke.pairs()]
    w = max(0.1, stroke.width)
    if stroke.kind in ("pen", "marker"):
        _polyline(snap, colour, pts, w)
    elif stroke.kind == "line":
        _polyline(snap, colour, [pts[0], pts[-1]], w)
    elif stroke.kind == "arrow":
        _arrow(snap, colour, pts[0], pts[-1], w)
    elif stroke.kind in ("rect", "ellipse"):
        _box(snap, colour, pts[0], pts[-1], w, stroke.kind == "ellipse")
    if opacity < 1.0:
        snap.pop()


# Whether this PyGObject can hand a render node back to Python. The one on the
# oldest boards (3.42, Debian 12) cannot translate GskContainerNode, and there
# the strokes are drawn into the page's own snapshot every time instead.
NODES_SUPPORTED: Optional[bool] = None


def build_node(strokes: Iterable[InkStroke], page_h: float) -> Optional[Gsk.RenderNode]:
    """
    All of a page's strokes as one node in page space, oldest underneath.
    None when there are none -- or when nodes cannot be kept (see above).
    """
    global NODES_SUPPORTED
    strokes = sorted(strokes, key=lambda s: s.created_ms)
    if not strokes or NODES_SUPPORTED is False:
        return None
    snap = Gtk.Snapshot()
    for stroke in strokes:
        append_stroke(snap, stroke, page_h)
    try:
        node = snap.to_node()
    except TypeError:
        NODES_SUPPORTED = False
        return None
    NODES_SUPPORTED = True
    return node


def page_to_widget(transform: PageTransform) -> Gsk.Transform:
    """
    The GSK transform that does what `PageTransform._device` does, minus the
    origin: scale, then the user's rotation, then the crop or rotation offset.
    """
    return (
        Gsk.Transform.new()
        .translate(_point(-transform.origin_x, -transform.origin_y))
        .rotate(transform.rotation % 360)
        .scale(transform.scale, transform.scale)
    )


def append_page_ink(
    snapshot: Gtk.Snapshot, node: Optional[Gsk.RenderNode], strokes: Sequence[InkStroke],
    page_h: float, transform: PageTransform, bounds,
) -> None:
    """
    Place a page's ink on the page widget, clipped to the sheet: the cached
    node where there is one, else the strokes drawn afresh.
    """
    if node is None and not strokes:
        return
    snapshot.push_clip(bounds)
    snapshot.save()
    snapshot.transform(page_to_widget(transform))
    if node is not None:
        snapshot.append_node(node)
    else:
        for stroke in strokes:
            append_stroke(snapshot, stroke, page_h)
    snapshot.restore()
    snapshot.pop()
