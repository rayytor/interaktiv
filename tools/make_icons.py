#!/usr/bin/env python3
"""
Generate the app's symbolic icons.

    python3 tools/make_icons.py    # writes interaktiv_gtk/icons/hicolor/scalable/actions/

Each icon is described below as strokes on a 24 x 24 grid and written out as
filled outlines. They have to be fills: GTK recolours a symbolic icon by
forcing `fill` on its paths, which turns a stroked drawing into a blob. Every
solid contour runs the same way round and holes run the other way, so the
overlapping pieces of one icon union under the non-zero fill rule.

The SVGs are committed; run this again only to change or add an icon.
"""

import math
import os
from typing import Callable, Dict, List, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Inside a `hicolor` theme folder: GTK 4.8 only recolours a symbolic icon it
# found in a theme, not one lying loose on the search path.
OUT_DIR = os.path.join(ROOT, "interaktiv_gtk", "icons", "hicolor", "scalable", "actions")
PREFIX = "interaktiv-"

GRID = 24
STROKE = 2.0

Point = Tuple[float, float]


def _n(value: float) -> str:
    """A coordinate, without the noise of full float precision."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def line(points: Sequence[Point], width: float = STROKE) -> str:
    """A polyline with round caps and joins: one capsule per segment."""
    half = width / 2.0
    parts = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        length = math.hypot(x2 - x1, y2 - y1)
        if length == 0:
            continue
        nx, ny = -(y2 - y1) / length * half, (x2 - x1) / length * half
        parts.append(
            f"M{_n(x1 + nx)} {_n(y1 + ny)}L{_n(x2 + nx)} {_n(y2 + ny)}"
            f"A{_n(half)} {_n(half)} 0 0 0 {_n(x2 - nx)} {_n(y2 - ny)}"
            f"L{_n(x1 - nx)} {_n(y1 - ny)}"
            f"A{_n(half)} {_n(half)} 0 0 0 {_n(x1 + nx)} {_n(y1 + ny)}Z"
        )
    return "".join(parts)


def _circle(cx: float, cy: float, r: float, hole: bool = False) -> str:
    sweep = 1 if hole else 0
    return (f"M{_n(cx - r)} {_n(cy)}"
            f"A{_n(r)} {_n(r)} 0 0 {sweep} {_n(cx + r)} {_n(cy)}"
            f"A{_n(r)} {_n(r)} 0 0 {sweep} {_n(cx - r)} {_n(cy)}Z")


def disc(cx: float, cy: float, r: float) -> str:
    return _circle(cx, cy, r)


def ring(cx: float, cy: float, r: float, width: float = STROKE) -> str:
    half = width / 2.0
    return _circle(cx, cy, r + half) + _circle(cx, cy, r - half, hole=True)


def arc(cx: float, cy: float, r: float, start: float, end: float,
        width: float = STROKE) -> str:
    """
    Part of a ring, clockwise on screen from `start` to `end` degrees, where 0
    is three o'clock and 90 is six o'clock. Round caps.
    """
    half = width / 2.0
    large = 1 if (end - start) > 180 else 0

    def at(radius: float, degrees: float) -> Point:
        angle = math.radians(degrees)
        return cx + radius * math.cos(angle), cy + radius * math.sin(angle)

    outer_start, outer_end = at(r + half, start), at(r + half, end)
    inner_start, inner_end = at(r - half, start), at(r - half, end)
    return (
        f"M{_n(outer_end[0])} {_n(outer_end[1])}"
        f"A{_n(r + half)} {_n(r + half)} 0 {large} 0 {_n(outer_start[0])} {_n(outer_start[1])}"
        f"A{_n(half)} {_n(half)} 0 0 0 {_n(inner_start[0])} {_n(inner_start[1])}"
        f"A{_n(r - half)} {_n(r - half)} 0 {large} 1 {_n(inner_end[0])} {_n(inner_end[1])}"
        f"A{_n(half)} {_n(half)} 0 0 0 {_n(outer_end[0])} {_n(outer_end[1])}Z"
    )


def _rounded(x: float, y: float, w: float, h: float, r: float, hole: bool) -> str:
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    if hole:  # clockwise on screen
        corners = [(x + w - r, y), (x + w, y + r), (x + w, y + h - r), (x + w - r, y + h),
                   (x + r, y + h), (x, y + h - r), (x, y + r), (x + r, y)]
        path = f"M{_n(x + r)} {_n(y)}"
        sweep = 1
    else:     # counter-clockwise on screen
        corners = [(x, y + h - r), (x + r, y + h), (x + w - r, y + h), (x + w, y + h - r),
                   (x + w, y + r), (x + w - r, y), (x + r, y), (x, y + r)]
        path = f"M{_n(x)} {_n(y + r)}"
        sweep = 0
    for index in range(0, 8, 2):
        straight, corner = corners[index], corners[index + 1]
        path += f"L{_n(straight[0])} {_n(straight[1])}"
        if r > 0:
            path += f"A{_n(r)} {_n(r)} 0 0 {sweep} {_n(corner[0])} {_n(corner[1])}"
    return path + "Z"


def block(x: float, y: float, w: float, h: float, r: float = 0.0) -> str:
    """A filled rounded rectangle."""
    return _rounded(x, y, w, h, r, hole=False)


def frame(x: float, y: float, w: float, h: float, r: float = 2.0,
          width: float = STROKE) -> str:
    """The outline of a rounded rectangle whose centre line is the given box."""
    half = width / 2.0
    return (_rounded(x - half, y - half, w + width, h + width, r + half, hole=False)
            + _rounded(x + half, y + half, w - width, h - width, max(0.0, r - half), hole=True))


def polygon(points: Sequence[Point]) -> str:
    """A filled polygon, wound the same way as every other solid."""
    area = sum(x1 * y2 - x2 * y1
               for (x1, y1), (x2, y2) in zip(points, list(points[1:]) + [points[0]]))
    ordered = list(points) if area < 0 else list(reversed(points))
    return "M" + "L".join(f"{_n(x)} {_n(y)}" for x, y in ordered) + "Z"


def closed(points: Sequence[Point], width: float = STROKE) -> str:
    return line(list(points) + [points[0]], width)


def _half_disc(cx: float, cy: float, r: float) -> str:
    """The right half of a disc."""
    return (f"M{_n(cx)} {_n(cy + r)}A{_n(r)} {_n(r)} 0 0 0 {_n(cx)} {_n(cy - r)}Z")


# --- the icons ----------------------------------------------------------------

def _corners() -> str:
    return (line([(4, 9), (4, 4), (9, 4)]) + line([(15, 4), (20, 4), (20, 9)])
            + line([(20, 15), (20, 20), (15, 20)]) + line([(9, 20), (4, 20), (4, 15)]))


def _rotate() -> str:
    return (arc(12, 12, 8, 0, 312)
            + line([(20.5, 3.5), (20.5, 8.5), (15.5, 8.5)]))


ICONS: Dict[str, Callable[[], str]] = {
    # navigation
    "chevron-left": lambda: line([(15, 5), (8, 12), (15, 19)]),
    "chevron-right": lambda: line([(9, 5), (16, 12), (9, 19)]),
    "chevron-up": lambda: line([(5, 15), (12, 8), (19, 15)]),
    "chevron-down": lambda: line([(5, 9), (12, 16), (19, 9)]),
    "back": lambda: line([(20, 12), (4, 12)]) + line([(10, 6), (4, 12), (10, 18)]),
    "close": lambda: line([(6, 6), (18, 18)]) + line([(18, 6), (6, 18)]),
    "check": lambda: line([(5, 12.5), (10, 17.5), (19, 7)]),
    "more": lambda: disc(5, 12, 1.9) + disc(12, 12, 1.9) + disc(19, 12, 1.9),
    # reader
    "zoom-in": lambda: line([(12, 5), (12, 19)]) + line([(5, 12), (19, 12)]),
    "zoom-out": lambda: line([(5, 12), (19, 12)]),
    "fit-page": _corners,
    "fit-width": lambda: (line([(3, 12), (21, 12)]) + line([(7, 8), (3, 12), (7, 16)])
                          + line([(17, 8), (21, 12), (17, 16)])),
    "search": lambda: ring(10.5, 10.5, 6.5) + line([(15.5, 15.5), (20, 20)]),
    "rotate": _rotate,
    "fullscreen": lambda: (line([(14, 4), (20, 4), (20, 10)]) + line([(20, 4), (13.5, 10.5)])
                           + line([(10, 20), (4, 20), (4, 14)]) + line([(4, 20), (10.5, 13.5)])),
    "restore": lambda: (line([(20, 10), (14, 10), (14, 4)]) + line([(14, 10), (20.5, 3.5)])
                        + line([(4, 14), (10, 14), (10, 20)]) + line([(10, 14), (3.5, 20.5)])),
    "theme": lambda: ring(12, 12, 8.5) + _half_disc(12, 12, 8.5),
    "view-book": lambda: frame(3, 5, 8.5, 14, 1.5) + frame(12.5, 5, 8.5, 14, 1.5),
    "view-single": lambda: frame(6.5, 3.5, 11, 17, 2),
    "view-scroll": lambda: (frame(6, 3, 12, 7.5, 1.5) + frame(6, 13.5, 12, 7.5, 1.5)),
    # sidebar
    "contents": lambda: (line([(9, 6), (20, 6)]) + line([(9, 12), (20, 12)])
                         + line([(9, 18), (20, 18)]) + disc(4.5, 6, 1.4)
                         + disc(4.5, 12, 1.4) + disc(4.5, 18, 1.4)),
    "sidebar": lambda: frame(3.5, 4.5, 17, 15, 2.5) + line([(9.5, 5), (9.5, 19)]),
    "pages": lambda: (frame(4, 4, 6.5, 6.5, 1.2) + frame(13.5, 4, 6.5, 6.5, 1.2)
                      + frame(4, 13.5, 6.5, 6.5, 1.2) + frame(13.5, 13.5, 6.5, 6.5, 1.2)),
    "bookmark": lambda: closed([(6.5, 4), (17.5, 4), (17.5, 20), (12, 15.5), (6.5, 20)]),
    "activities": lambda: ring(12, 12, 8.5) + disc(12, 12, 3.2),
    # library
    "book": lambda: (line([(12, 6.5), (12, 20)])
                     + line([(12, 6.5), (9, 5), (3, 5), (3, 18), (9, 18), (12, 20)])
                     + line([(12, 6.5), (15, 5), (21, 5), (21, 18), (15, 18), (12, 20)])),
    "download": lambda: (line([(12, 4), (12, 15)]) + line([(7.5, 10.5), (12, 15), (16.5, 10.5)])
                         + line([(4.5, 16.5), (4.5, 19.5), (19.5, 19.5), (19.5, 16.5)])),
    "trash": lambda: (line([(4, 7), (20, 7)]) + line([(9, 7), (9, 4.5), (15, 4.5), (15, 7)])
                      + line([(6, 7), (7, 20), (17, 20), (18, 7)])
                      + line([(10, 11), (10, 16)]) + line([(14, 11), (14, 16)])),
    "edit": lambda: (closed([(4, 20), (4, 16), (15.5, 4.5), (19.5, 8.5), (8, 20)])
                     + line([(13, 7), (17, 11)])),
    "refresh": _rotate,
    # activities
    "play": lambda: (polygon([(8.5, 6), (18, 12), (8.5, 18)])
                     + closed([(8.5, 6), (18, 12), (8.5, 18)])),
    "bolt": lambda: (polygon([(13, 3), (5.5, 13.5), (11, 13.5), (10.5, 21), (18.5, 10), (12.5, 10)])
                     + closed([(13, 3), (5.5, 13.5), (11, 13.5), (10.5, 21), (18.5, 10),
                               (12.5, 10)], 1.2)),
    "open-external": lambda: (line([(10, 5), (5, 5), (5, 19), (19, 19), (19, 14)])
                              + line([(12.5, 11.5), (20, 4)])
                              + line([(14.5, 4), (20, 4), (20, 9.5)])),
    # help and input
    "help": lambda: (ring(12, 12, 9) + arc(12, 9.8, 2.7, 190, 420)
                     + line([(13.35, 12.14), (12, 13.6), (12, 14.3)]) + disc(12, 17.3, 1.25)),
    "info": lambda: ring(12, 12, 9) + line([(12, 11), (12, 17)]) + disc(12, 7.4, 1.25),
    "backspace": lambda: (closed([(9, 5), (21, 5), (21, 19), (9, 19), (3, 12)])
                          + line([(12, 9.5), (17, 14.5)]) + line([(17, 9.5), (12, 14.5)])),
}


def svg(path: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
        f'viewBox="0 0 {GRID} {GRID}">\n'
        f'  <path d="{path}"/>\n'
        f"</svg>\n"
    )


def write_all(out_dir: str = OUT_DIR) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, draw in sorted(ICONS.items()):
        path = os.path.join(out_dir, f"{PREFIX}{name}-symbolic.svg")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(svg(draw()))
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in write_all():
        print(os.path.relpath(written_path, ROOT))
