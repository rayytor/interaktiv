"""
The rules about which pages are on screen, and how big they are.

These are the parts of `viewer.js` that are pure arithmetic, kept out of the
widgets so that they can be read -- and tested -- without a display attached.
Every function here is a port of a specific piece of the web reader, named in
its docstring, and the ports are deliberately literal: a teacher moving between
the two builds should never find that page 41 faces page 42 in one and page 40
in the other.

The one thing worth knowing before reading them is the book-mode convention.
Page 1 is a cover and stands alone; after that an even page faces the odd page
that follows it, so the spreads are 2|3, 4|5, 6|7. That means "the current page"
in book mode is always the *left* page of the spread, which is why `next_page`
and `prev_page` are asymmetric at the front of the book.
"""

from typing import List, Optional, Sequence, Tuple

# The web's `- 48`: 24 px of air on each side, so a page is never flush against
# the chrome. (`calculateScale`, `viewer.js:680`.)
MARGIN = 24

# `calculateScale`'s floor, and its "never smaller than a 320 px viewport" rule.
MIN_SCALE = 0.2
MIN_VIEWPORT = 320.0

# What a zoom may be asked for, by any route: the dropdown, `+`/`-`, a pinch,
# or ctrl+wheel. The web reader's own clamp (`viewer.js:700`).
ZOOM_MIN = 0.3
ZOOM_MAX = 3.0
ZOOM_STEP = 0.2


def pages_for(page: int, total: int, mode: str) -> List[int]:
    """
    Which pages a spread shows. Port of `renderBookMode` (`viewer.js:752`).

    In book mode this also decides what "the current page" becomes: the web
    rewrites `currentPage` and the page-number box to the left page before it
    draws, so typing 43 into the box lands on the 42|43 spread and the box then
    reads 42.
    """
    if total <= 0:
        return []
    page = min(max(int(page), 1), total)
    if mode != "book" or page == 1:
        return [page]
    left = page if page % 2 == 0 else page - 1
    if left + 1 <= total:
        return [left, left + 1]
    return [left]


def next_page(current: int, total: int, mode: str,
              spread: Optional[Sequence[int]] = None) -> int:
    """
    Where "next" goes. Port of `nextPage` (`viewer.js:626`).

    Book mode's asymmetry at the cover is the whole point: from page 1, which is
    alone, next is page 2 and not page 3, because 2|3 is the first real spread.
    At the other end, a book with an odd page count finishes on a spread that
    already contains the last page, and next then does nothing rather than
    bouncing back to a page already on screen.
    """
    if total <= 0:
        return current
    if mode != "book":
        return min(current + 1, total)
    if current == 1:
        return 2 if total >= 2 else current
    target = current + 2
    if target <= total:
        return target
    if total in (spread if spread is not None else pages_for(current, total, mode)):
        return current
    return total


def prev_page(current: int, total: int, mode: str) -> int:
    """
    Where "previous" goes. Port of `prevPage` (`viewer.js:646`).

    Anywhere in the first spread goes to the cover, not to a half-step: from
    page 2 or 3, back is page 1.
    """
    if mode != "book":
        return max(current - 1, 1)
    if current <= 1:
        return current
    if current <= 3:
        return 1
    return current - 2


def spread_size(sizes: Sequence[Tuple[float, float]], rotation: int = 0) -> Tuple[float, float]:
    """
    The size of a spread in PDF points, with the pages laid side by side.

    Summed rather than assumed: a book is not necessarily uniform. The 443 MB
    chemistry book mixes 570 pt single pages with 1168 pt pages that are already
    a spread in the file, and a layout that took page 1's size for the whole
    book would draw every one of those at half scale.
    """
    if not sizes:
        return (595.0, 842.0)
    if rotation % 180 == 90:
        sizes = [(h, w) for w, h in sizes]
    return (sum(w for w, _ in sizes), max(h for _, h in sizes))


def fit_scale(spread_w: float, spread_h: float, avail_w: float, avail_h: float,
              zoom_mode: str, custom_zoom: float = 1.0) -> float:
    """
    Logical pixels per PDF point. Port of `calculateScale` (`viewer.js:680`),
    floor and 320 px minimum viewport included.

    `avail_w` and `avail_h` are the space left after `MARGIN` has been taken off
    both sides, so callers subtract it rather than having it applied twice.
    """
    if zoom_mode == "custom":
        return custom_zoom
    if spread_w <= 0 or spread_h <= 0:
        return 1.0
    avail_w = max(MIN_VIEWPORT, avail_w)
    avail_h = max(MIN_VIEWPORT, avail_h)
    if zoom_mode == "fit-width":
        return max(MIN_SCALE, avail_w / spread_w)
    return max(MIN_SCALE, min(avail_w / spread_w, avail_h / spread_h))


def pinch_zoom(base: float, scale: float,
               lo: float = ZOOM_MIN, hi: float = ZOOM_MAX) -> float:
    """
    Where a pinch that has grown by `scale` should leave the zoom.

    `Gtk.GestureZoom` reports the distance between the fingers relative to
    where they started, not to the previous report, so this multiplies the
    zoom the gesture *began* at. Multiplying the live zoom instead would
    square the pinch: a gesture held at 1.5x would run away to the ceiling
    without the fingers moving.
    """
    if scale <= 0:
        return base
    return min(hi, max(lo, base * scale))


def wheel_zoom(current: float, delta_y: float, step: float = 0.2,
               lo: float = ZOOM_MIN, hi: float = ZOOM_MAX) -> float:
    """
    Where ctrl+wheel should leave the zoom, for one scroll report.

    A mouse notch reports 1.0 and a touchpad's kinetic scroll reports a
    fraction of one, so the step is scaled by the report and both gestures
    move the page at the rate the hand expects. Scrolling down -- the
    direction that moves a page away -- zooms out.
    """
    if delta_y == 0:
        return current
    factor = step * min(1.0, abs(delta_y))
    scaled = current * (1 - factor if delta_y > 0 else 1 + factor)
    return min(hi, max(lo, scaled))
