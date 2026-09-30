"""
Sharp pages at deep zoom.

A whole page is rendered at most `MAX_RENDER_PIXELS` large, which at a board's
200 % scale runs out around 250 % zoom: beyond it the page is painted up from
fewer pixels than the screen has, and small print goes soft exactly when a
teacher zoomed in to read it. So past the cap the part of the page on screen is
rendered again, on its own and at full sharpness, and drawn over the whole-page
texture. The clip is snapped to a grid of `GRID` points and padded by a share
of the screen, so that small pans reuse it and a pan shows the soft page only
at the edge for a moment.
"""

import math
from typing import Optional, Tuple

from ..render.requests import MAX_RENDER_PIXELS

Rect = Tuple[float, float, float, float]

GRID = 16.0
PAD = 0.15
# Only worth it when the whole page is clearly softer than the screen.
MARGIN = 1.2


def needs_detail(page_w: float, page_h: float, render_scale: float) -> bool:
    return page_w * page_h * render_scale * render_scale > MAX_RENDER_PIXELS * MARGIN


def visible_clip(view, scroller) -> Optional[Rect]:
    """
    The part of `view`'s page on screen in `scroller`, padded and snapped, in
    PDF user space as (x0, y0, x1, y1); None if none of it is.
    """
    transform = view.transform()
    ok, b = view.compute_bounds(scroller)
    vw, vh = scroller.get_width(), scroller.get_height()
    if transform is None or not ok or vw <= 0 or vh <= 0:
        return None
    left = max(0.0, -b.get_x() - PAD * vw)
    top = max(0.0, -b.get_y() - PAD * vh)
    right = min(b.get_width(), vw - b.get_x() + PAD * vw)
    bottom = min(b.get_height(), vh - b.get_y() + PAD * vh)
    if right <= left or bottom <= top:
        return None
    corners = [transform.point_to_pdf(x, y) for x in (left, right) for y in (top, bottom)]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    page_w, page_h = transform.page_w, transform.page_h
    x0 = max(0.0, math.floor(min(xs) / GRID) * GRID)
    y0 = max(0.0, math.floor(min(ys) / GRID) * GRID)
    x1 = min(page_w, math.ceil(max(xs) / GRID) * GRID)
    y1 = min(page_h, math.ceil(max(ys) / GRID) * GRID)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)
