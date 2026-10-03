#!/usr/bin/env python3
"""
The publisher's icons, read after the student's boxes.

The student sees the printed page. The publisher's icons are not printed on
it -- they come from the manifest (`activities_meta`), one point per
interactive entry -- so the student cannot know where they are. They are a
second, independent witness, and every use made of them here is one the rules
engine already makes in `anchors.reconcile_anchors`:

  admit   a box the student is unsure of (below the bake's confidence) stands
          when an icon is hung at it and no surer box is there: two weak
          witnesses agreeing;
  bind    an icon hung at a region the join left unpaired names that region
          (`oge_id`). The join measures an icon against the *top* of a region,
          which is right for a region that starts at its instruction and wrong
          for one that takes the picture above the question, as the student's
          do. The region itself is not touched;
  block   (modes "blocks", "grow") an icon nothing answers, hung at a panel or
          figure the page draws as one object, makes that block a region --
          whole, and only if no region stands on it;
  grow    (mode "grow") an icon still unanswered gets the rules engine's own
          icon-grown region, cut back off its neighbours.

An icon none of these place stays what it was: a pin in the reader, at the
point the publisher hung it, opening the same interactive entry.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from interaktiv_core.linking import pair_cost, parse_oge_label, oge_view, region_view  # noqa: E402
from tools.hotspot_extraction.scanner.anchors import (  # noqa: E402
    BAND_REACH_ABOVE, BAND_REACH_BELOW, PublisherOge, _create_anchored_region, link_page_oges,
)
from tools.hotspot_extraction.scanner.regions import (  # noqa: E402
    MIN_HOTSPOT, TALL_REGION, ActivityRegion, PageGeometry, rect_area, rect_overlap,
)

Rect = Tuple[float, float, float, float]
MODES = ("off", "bind", "blocks", "grow")
ICON_ACROSS = 0.06       # share of the sheet's width an icon may hang outside the thing it opens
MIN_GROWN = 3 * MIN_HOTSPOT   # pt: a grown region cut back to less than this is no region
PANEL_LINES = 3          # lines of type a panel must hold to be a thing an icon opens, not its title bar


def icon_point(oge: PublisherOge, page_w: float, page_h: float) -> Optional[Tuple[float, float]]:
    """Where the publisher hung the icon, in PDF points (y up), or None when the entry has no position."""
    if oge.posx == 0.0 and oge.posy == 0.0:
        return None
    return (oge.posx / 100.0 * page_w, page_h - oge.posy / 100.0 * page_h)


def icon_at(point: Tuple[float, float], rect: Sequence[float], page_w: float) -> bool:
    """
    Whether an icon is hung at `rect`: level with it, and over it or in the margin beside it.

    The slack up and down is the rules engine's (`BAND_REACH_*`): an icon is set
    level with the first line of what it opens or a little above, hardly ever
    below its foot.
    """
    x, y = point
    across = ICON_ACROSS * page_w
    return (rect[0] - across <= x <= rect[2] + across
            and rect[1] - BAND_REACH_BELOW <= y <= rect[3] + BAND_REACH_ABOVE)


def admit(
    sure: Sequence[Tuple[float, Rect]],
    doubtful: Sequence[Tuple[float, Rect]],
    oges: Sequence[PublisherOge],
    page_w: float,
    page_h: float,
) -> List[Tuple[float, Rect]]:
    """
    The doubtful boxes an icon vouches for: for each icon hung at no sure box, the surest doubtful box it heads.

    A box taller than a hotspot may be is never admitted, and an icon that
    already stands at a sure box asks for nothing.
    """
    max_h = (TALL_REGION - 0.005) * page_h
    out: List[Tuple[float, Rect]] = []
    for oge in oges:
        point = icon_point(oge, page_w, page_h)
        if point is None or any(icon_at(point, r, page_w) for _, r in sure):
            continue
        # Hung at its upper half: the publisher sets an icon beside the first lines of what it opens. A
        # sure box may start at a picture and have the icon lower down; a doubtful one gets no such credit.
        at = [(s, r) for s, r in doubtful if r[3] - r[1] <= max_h and icon_at(point, r, page_w)
              and point[1] >= (r[1] + r[3]) / 2.0]
        if at:
            best = max(at, key=lambda b: b[0])
            if best not in out:
                out.append(best)
    return out


def _unplaced(activities: List[ActivityRegion], oges: Sequence[PublisherOge], page_w: float, page_h: float,
              printed_page: int) -> List[PublisherOge]:
    """The entries with a position that neither the join nor a binding has given a region."""
    linked = link_page_oges(activities, list(oges), page_h, printed_page, page_w)
    claimed = {o.id for o in linked.values()} | {a.oge_id for a in activities if a.oge_id}
    return [o for o in oges if o.id not in claimed and icon_point(o, page_w, page_h) is not None]


def bind(activities: List[ActivityRegion], oges: Sequence[PublisherOge], page_w: float, page_h: float,
         printed_page: int) -> int:
    """
    Name, for each icon the join left unpaired, the region it is hung at. Returns how many were bound.

    An icon goes to the tightest region it is hung at, so a question inside a
    wider box is preferred to the box. A region takes one entry: where two
    icons want it, the one nearer its top-left by the join's own cost has it and
    the other stays a pin. A region the join already paired is left to the
    join, and a region whose printed label contradicts the entry's is not the
    entry's, wherever the icon hangs.
    """
    linked = link_page_oges(activities, list(oges), page_h, printed_page, page_w)
    free = [i for i, a in enumerate(activities) if i not in linked and not a.oge_id]
    wants: dict = {}
    for oge in _unplaced(activities, oges, page_w, page_h, printed_page):
        point = icon_point(oge, page_w, page_h)
        label = parse_oge_label(oge.title, printed_page)
        at = [i for i in free if icon_at(point, activities[i].rect, page_w)
              and not (label and activities[i].label and label != activities[i].label.lower().strip())]
        if at:
            wants.setdefault(min(at, key=lambda i: rect_area(activities[i].rect)), []).append(oge)
    for i, rivals in wants.items():
        a = activities[i]
        view = region_view(a.id, a.label, a.rect, page_w, page_h)
        a.oge_id = min(rivals, key=lambda o: pair_cost(view, oge_view(o.id, o.title, printed_page, o.posx, o.posy))).id
    return len(wants)


def _anchored(oge: PublisherOge, page_num: int, rect: Rect, column: int, headline: Optional[str]) -> ActivityRegion:
    r = (round(rect[0], 4), round(rect[1], 4), round(rect[2], 4), round(rect[3], 4))
    return ActivityRegion(id=f"p{page_num}-oge-{oge.id}", label=None, column=column, rect=r, parts=[r],
                          headline=headline or oge.title, items=None, anchored=True)


def take_blocks(activities: List[ActivityRegion], oges: Sequence[PublisherOge], geom: PageGeometry,
                page_num: int, printed_page: int) -> int:
    """
    Make a region of the drawn block each unanswered icon is hung at. Returns how many were made.

    Only a panel or a figure counts -- something the page draws as one object --
    and only one that is a picture or holds at least `PANEL_LINES` lines of type
    (a tinted bar with a heading in it is a title, not a thing to open), is no
    taller than a hotspot may be, and has no region on it: the block is taken
    whole or the icon stays a pin. Of several blocks at the icon the largest is
    the one meant; the smaller ones are its title tab and its inner frames.
    """
    page_w, page_h = geom.sheet()
    max_h = (TALL_REGION - 0.005) * page_h
    made = 0
    for oge in _unplaced(activities, oges, page_w, page_h, printed_page):
        point = icon_point(oge, page_w, page_h)
        blocks = []
        for b in geom.blocks:
            r = tuple(b["rect"])
            if b.get("kind") not in ("panel", "figure") or not icon_at(point, r, page_w):
                continue
            if not (MIN_GROWN <= r[3] - r[1] <= max_h) or r[2] - r[0] < MIN_GROWN:
                continue
            if r[0] < -2 or r[1] < -2 or r[2] > page_w + 2 or r[3] > page_h + 2:
                continue
            if any(rect_overlap(r, a.rect) > 1.0 for a in activities):
                continue
            if b.get("kind") == "panel" and sum(
                    r[0] <= (ln["x0"] + ln["x1"]) / 2.0 <= r[2] and r[1] <= (ln["y0"] + ln["y1"]) / 2.0 <= r[3]
                    for ln in geom.lines) < PANEL_LINES:
                continue
            blocks.append(r)
        if blocks:
            r = max(blocks, key=rect_area)
            activities.append(_anchored(oge, page_num, r, 0 if point[0] < page_w / 2.0 else 1, None))
            made += 1
    return made


def _cut_back(rect: Rect, others: Sequence[Rect]) -> Optional[Rect]:
    """`rect` moved off every one of `others`, giving up as little as it can; None when too little is left."""
    r = list(rect)
    for o in others:
        if rect_overlap(tuple(r), tuple(o)) <= 1.0:
            continue
        options = []
        if r[1] < o[3] < r[3]:
            options.append((o[3] - r[1], 1, o[3] + 0.25))
        if r[1] < o[1] < r[3]:
            options.append((r[3] - o[1], 3, o[1] - 0.25))
        if r[0] < o[2] < r[2]:
            options.append((o[2] - r[0], 0, o[2] + 0.25))
        if r[0] < o[0] < r[2]:
            options.append((r[2] - o[0], 2, o[0] - 0.25))
        if not options:
            return None
        _, edge, value = min(options)
        r[edge] = value
    if r[2] - r[0] < MIN_GROWN or r[3] - r[1] < MIN_GROWN:
        return None
    if any(rect_overlap(tuple(r), tuple(o)) > 1.0 for o in others):
        return None
    return (r[0], r[1], r[2], r[3])


def grow(activities: List[ActivityRegion], oges: Sequence[PublisherOge], prim: Any, layout: Any,
         page_num: int, printed_page: int) -> int:
    """
    Give every icon still unanswered the rules engine's icon-grown region. Returns how many were made.

    The region is `anchors._create_anchored_region`'s, unchanged: the panel or
    figure at the icon, else the run of lines under it. It yields to what is
    already on the sheet -- it is cut back off its neighbours, never they off it
    -- and is dropped when that leaves too little, the icon outside it, or more
    than a hotspot's height.
    """
    page_w, page_h = prim.width, prim.height
    max_h = (TALL_REGION - 0.005) * page_h
    made = 0
    for oge in _unplaced(activities, oges, page_w, page_h, printed_page):
        grown = _create_anchored_region(oge=oge, page_num=page_num, page_width=page_w, page_height=page_h,
                                        primitives=prim, layout=layout, existing_activities=activities)
        rect = _cut_back(grown.rect, [a.rect for a in activities])
        if rect is None or rect[3] - rect[1] > max_h or not icon_at(icon_point(oge, page_w, page_h), rect, page_w):
            continue
        activities.append(_anchored(oge, page_num, rect, grown.column, grown.headline))
        made += 1
    return made


def apply(mode: str, activities: List[ActivityRegion], oges: Sequence[PublisherOge], *, prim: Any, layout: Any,
          geom: PageGeometry, page_num: int, printed_page: Optional[int]) -> List[ActivityRegion]:
    """Run the steps `mode` names over one sheet's regions, in place, and return them."""
    if mode not in MODES:
        raise ValueError(f"icons mode must be one of {MODES}, got {mode!r}")
    if mode == "off" or not oges or printed_page is None:
        return activities
    page_w, page_h = prim.width, prim.height
    if activities:
        bind(activities, oges, page_w, page_h, printed_page)
    if mode in ("blocks", "grow"):
        take_blocks(activities, oges, geom, page_num, printed_page)
    if mode == "grow":
        grow(activities, oges, prim, layout, page_num, printed_page)
    return activities
