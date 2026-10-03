#!/usr/bin/env python3
"""
Phase 5 of the vision plan: the student's boxes become snapped regions.

The student decides what is an activity and where it ends: it sees the page.
Its edges are a few points off, and the page's own geometry may move them
those few points -- but never so far that the box holds a different set of
lines than the student put in it. For one parsed sheet:

  1. the pixel boxes are converted to PDF points, y up (the `regions.json`
     convention), and clamped to the sheet;
  2. a box taller than a hotspot may be (`TALL_REGION`) is left out: it is the
     page, not an activity on it, and cutting it down would leave a hotspot
     over part of an activity;
  3. an edge that cuts through a line of type steps off it (`seam_snap`): out
     past a line the box holds most of, back inside one it only clips. An edge
     in the ascender room of a line is not through its letters and stays;
  4. `settle` makes the boxes fit the page and each other: a box lying mostly
     inside a more confident one is dropped; a drawn block (panel, answer grid,
     figure) a box holds most of is taken whole on every side that is free and
     brings no foreign line with it; a block it only clips is left, when
     leaving costs little and not one line the box holds; two boxes that still
     overlap are parted at a seam;
  5. a printed activity marker (`prompts.detect_activity_markers`) standing in
     the top-left quarter of a box gives the box its label, headline and
     numbered questions, when it is set as a label (`stands_apart`) and is not
     the first glyph of a line of mathematics; a box with no marker keeps
     `label = None`;
  6. a printed label the student left unboxed between two labels it boxed is a
     question of the same list, and gets a region of its own lines and answer
     rules (`between_siblings`): only where the page itself says where the
     question ends, and never across a picture or a panel;
  7. the publisher's icons are read (`icons.py`): one hung at a region names
     it, and, by the mode the bake was given, one hung at nothing may bring a
     region of its own.

Steps 3 and 4 were measured against the teacher's boxes on labelled pages
(which lines and blocks a region holds, beside which the teacher's box holds):
they are kept only as far as they leave that unchanged.

The rules engine's `clean_page_activities` / `snap_edges` are not used here,
and `scan.py` tells the serializer not to run them either (`clean=False`):
they retreat off a block whenever taking it whole would touch a neighbour,
which is right for regions grown by rules and halves a box the student drew
correctly.

`scan.py --engine vision` calls `vision_page` in place of the rules
detector's `detect_page`; the boxes come from `infer.py`.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.scanner.anchors import _page_geometry  # noqa: E402
from tools.hotspot_extraction.scanner.layout import detect_layout  # noqa: E402
from tools.hotspot_extraction.scanner.markers import _is_bold  # noqa: E402
from tools.hotspot_extraction.scanner.pipeline import PageResult, body_spans_of, ruler_blocks  # noqa: E402
from tools.hotspot_extraction.scanner.primitives import PagePrimitives  # noqa: E402
from tools.hotspot_extraction.scanner.prompts import detect_activity_markers  # noqa: E402
from tools.hotspot_extraction.scanner.regions import (  # noqa: E402
    MIN_HOTSPOT, TALL_REGION, ActivityRegion, GrowthTrace, PageGeometry, build_headline, build_sub_items,
    cuts, legal_planes, legal_planes_x, nearest_plane, rect_area, rect_overlap, rect_union,
)

from tools.hotspot_extraction.vision import icons  # noqa: E402

Rect = Tuple[float, float, float, float]
SEAM_REACH = 14.0        # pt: how far an edge may travel to leave the line of type it rests in
SEAM_GAP = 1.0           # pt: clearance left beside a line of type an edge has stepped off
LINE_SLACK = 0.25        # share of a line's height, top and bottom, that is room for ascenders, not ink
LINE_HELD = 0.5          # share of a line of type inside a box past which the line is the box's
NESTED_SHARE = 0.6       # share of a box lying inside a more confident one past which it is that box again
TAKE_SHARE = 0.5         # share of a drawn block a box must hold to take it whole
LEAVE_COST = 0.25        # share of its own area a box may give up to leave a block it only clips
NEIGHBOUR_GAP = 0.25     # pt: left between two boxes that grow to the same boundary
MARKER_TOL = 6.0         # pt: a marker this far outside the box's top-left quarter still labels it
SIBLING_RUN = 2          # unboxed labels in a row that two boxed ones may vouch for; more is a list the student refused
SIBLING_PAD = 1.0        # pt: left round the lines and answer rules of a region made for an unboxed label


def px_to_points(px: Sequence[float], width_px: float, height_px: float, page_w: float, page_h: float) -> Rect:
    """A pixel box (x0, y0, x1, y1, y down) as PDF points (x0, y0, x1, y1, y up), clamped to the sheet."""
    sx, sy = page_w / float(width_px), page_h / float(height_px)
    x0, x1 = sorted((float(px[0]) * sx, float(px[2]) * sx))
    top, bottom = sorted((float(px[1]) * sy, float(px[3]) * sy))
    return (round(max(0.0, x0), 4), round(max(0.0, page_h - bottom), 4),
            round(min(page_w, x1), 4), round(min(page_h, page_h - top), 4))


def _lines_at(geom: PageGeometry, rect: Rect, edge: int) -> List[dict]:
    """
    The lines of type the rect's `edge` (0 left, 1 bottom, 2 right, 3 top) cuts.

    A line's box is taller than its ink: it reserves room for ascenders and
    descenders the line may not have. The student draws round the ink, so a top
    or bottom edge in the outer `LINE_SLACK` of a line is not through its
    letters and counts as clear of it.
    """
    v = rect[edge]
    out = []
    for ln in geom.lines:
        if edge in (1, 3):
            slack = max(0.5, LINE_SLACK * (ln["y1"] - ln["y0"]))
            if ln["x1"] > rect[0] and ln["x0"] < rect[2] and ln["y0"] + slack < v < ln["y1"] - slack:
                out.append(ln)
        elif ln["y1"] > rect[1] and ln["y0"] < rect[3] and ln["x0"] + 0.5 < v < ln["x1"] - 0.5:
            out.append(ln)
    return out


def _line_rect(ln: dict) -> Rect:
    return (ln["x0"], ln["y0"], ln["x1"], ln["y1"])


def holds(rect: Sequence[float], inner: Sequence[float], share: float = LINE_HELD) -> bool:
    """Whether `rect` holds at least `share` of `inner` (a line of type, a drawn block)."""
    area = rect_area(tuple(inner))
    return area > 0.0 and rect_overlap(tuple(rect), tuple(inner)) / area >= share


def held_lines(rect: Sequence[float], geom: PageGeometry) -> List[dict]:
    """The lines of type the student put in this box: the ones it holds most of."""
    return [ln for ln in geom.lines if holds(rect, _line_rect(ln))]


def seam_snap(rect: Rect, geom: PageGeometry) -> Rect:
    """
    Move every edge that rests inside a line of type off it, keeping what the box holds.

    The student decided which lines belong to the activity; its edges are only a
    few points off. So a line the box holds most of is the box's, and an edge
    resting inside it moves out to just past it. A line the box only clips is
    not, and the edge steps back inside it -- but never past a line the box
    holds, which on a page of formulas or beside a drop capital stands level
    with the one being left. An edge that would have to travel further than
    one line to get clear stays where the student put it.
    """
    r = list(rect)
    mine = held_lines(rect, geom)
    mine_ids = {id(ln) for ln in mine}
    for edge in (3, 1, 0, 2):
        inside = _lines_at(geom, tuple(r), edge)
        if not inside:
            continue
        v = r[edge]
        lo, hi = ("y0", "y1") if edge in (1, 3) else ("x0", "x1")
        out = 1.0 if edge in (2, 3) else -1.0           # the way out of the box, along this axis
        outer = hi if out > 0 else lo
        inner = lo if out > 0 else hi
        if edge in (1, 3):
            reach = max(SEAM_REACH, max(ln["y1"] - ln["y0"] for ln in inside))
            level = [ln for ln in mine if ln["x1"] > r[0] and ln["x0"] < r[2]]
        else:
            reach = SEAM_REACH
            level = [ln for ln in mine if ln["y1"] > r[1] and ln["y0"] < r[3]]
        kept = [ln for ln in inside if id(ln) in mine_ids]
        if kept:
            target = max(out * ln[outer] for ln in kept) * out + out * SEAM_GAP
        else:
            target = min(out * ln[inner] for ln in inside) * out - out * SEAM_GAP
            if level:
                floor = max(out * ln[outer] for ln in level) * out + out * SEAM_GAP
                if out * target < out * floor:
                    target = floor if out * floor < out * v else v
        trial = list(r)
        trial[edge] = target
        if abs(target - v) <= reach and trial[2] > trial[0] and trial[3] > trial[1]:
            r[edge] = target
    return (round(r[0], 4), round(r[1], 4), round(r[2], 4), round(r[3], 4))


def drop_nested(boxes: Sequence[Tuple[float, Rect]]) -> List[Tuple[float, Rect]]:
    """Most confident first; a box lying mostly inside one already kept is the same activity seen twice."""
    kept: List[Tuple[float, Rect]] = []
    for score, r in sorted(boxes, key=lambda b: -b[0]):
        area = rect_area(r)
        if area > 0.0 and all(rect_overlap(r, k) / min(area, rect_area(k)) < NESTED_SHARE for _, k in kept):
            kept.append((score, r))
    return kept


def _free(trial: Sequence[float], i: int, rects: Sequence[Sequence[float]]) -> bool:
    return all(j == i or rect_overlap(tuple(trial), tuple(o)) <= 1.0 for j, o in enumerate(rects))


def _as_far_as_free(r: Sequence[float], edge: int, value: float, i: int, rects: Sequence[Sequence[float]]) -> float:
    """
    `value` for the box's `edge`, pulled back to the first neighbour in the way.

    Two activities printed on one sheet of squared paper, or stacked in one
    panel, share a boundary: each may grow to it and neither past it.
    """
    trial = list(r)
    trial[edge] = value
    for j, o in enumerate(rects):
        if j == i or rect_overlap(tuple(trial), tuple(o)) <= 1.0:
            continue
        if edge == 3:
            value = min(value, o[1] - NEIGHBOUR_GAP)
        elif edge == 1:
            value = max(value, o[3] + NEIGHBOUR_GAP)
        elif edge == 2:
            value = min(value, o[0] - NEIGHBOUR_GAP)
        else:
            value = max(value, o[2] + NEIGHBOUR_GAP)
        trial[edge] = value
    return value


def _centre_in(ln: dict, rect: Sequence[float], tol: float = 2.0) -> bool:
    cx, cy = (ln["x0"] + ln["x1"]) / 2.0, (ln["y0"] + ln["y1"]) / 2.0
    return rect[0] - tol <= cx <= rect[2] + tol and rect[1] - tol <= cy <= rect[3] + tol


def fit_blocks(rects: List[List[float]], geom: PageGeometry, passes: int = 3) -> None:
    """
    Settle every box against the drawn blocks it cuts, in place, without changing what it holds.

    A block the box holds at least `TAKE_SHARE` of belongs to it: each edge
    moves out to the block's, or as far towards it as a neighbour allows,
    unless that would put the box past the tall limit or over a line of type
    that is neither the box's nor the block's -- in which case that edge alone
    stays. A block the box only clips, or could not take, is left behind when
    that costs at most `LEAVE_COST` of the box and not one line the box holds. Otherwise nothing moves: the student's box
    stands, and a drawn shape it crosses (a page banner, a column rule, a
    tinted background) is the page's decoration, not the activity's edge.
    """
    page_w, page_h = geom.sheet()
    max_h = (TALL_REGION - 0.005) * page_h
    blocks = [tuple(b["rect"]) for b in geom.blocks
              if rect_area(b["rect"]) > 0.0 and b["rect"][0] >= -2 and b["rect"][1] >= -2
              and b["rect"][2] <= page_w + 2 and b["rect"][3] <= page_h + 2
              # A shape taller than a hotspot may be is a tinted background, not a block to take or leave.
              and b["rect"][3] - b["rect"][1] <= TALL_REGION * page_h]
    for _ in range(passes):
        moved = False
        for i, r in enumerate(rects):
            mine = {id(ln) for ln in held_lines(r, geom)}
            for b in blocks:
                if not cuts(tuple(r), b):
                    continue
                if holds(r, b, TAKE_SHARE):
                    grown = rect_union(tuple(r), b)
                    for edge in range(4):
                        if grown[edge] == r[edge]:
                            continue
                        out = 1.0 if edge in (2, 3) else -1.0
                        value = _as_far_as_free(r, edge, grown[edge], i, rects)
                        if out * value <= out * r[edge]:
                            continue
                        trial = list(r)
                        trial[edge] = value
                        brought = [ln for ln in held_lines(trial, geom) if id(ln) not in mine]
                        if (trial[3] - trial[1] <= max_h and _free(trial, i, rects)
                                and all(_centre_in(ln, b) for ln in brought)):
                            r[edge] = value
                            mine.update(id(ln) for ln in brought)
                            moved = True
                    if not cuts(tuple(r), b):
                        continue
                # The box only clips the block, or could not take it: step back to the block's nearer
                # edge, if that is cheap.
                options = []
                if r[1] < b[3] < r[3]:
                    options.append((b[3] - r[1], 1, b[3]))
                if r[1] < b[1] < r[3]:
                    options.append((r[3] - b[1], 3, b[1]))
                if r[0] < b[2] < r[2]:
                    options.append((b[2] - r[0], 0, b[2]))
                if r[0] < b[0] < r[2]:
                    options.append((r[2] - b[0], 2, b[0]))
                for _, edge, value in sorted(options):
                    trial = list(r)
                    trial[edge] = value
                    if (trial[2] - trial[0] >= MIN_HOTSPOT and trial[3] - trial[1] >= MIN_HOTSPOT
                            and rect_area(tuple(trial)) >= (1.0 - LEAVE_COST) * rect_area(tuple(r))
                            and not cuts(tuple(trial), b)
                            and mine <= {id(ln) for ln in held_lines(trial, geom)}):
                        r[edge] = value
                        moved = True
                        break
        if not moved:
            break


def part_overlaps(rects: List[List[float]], geom: PageGeometry) -> None:
    """Part every two boxes that still overlap, at the page's seam nearest the middle of the overlap, in place."""
    for _ in range(4):
        moved = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                ox = min(a[2], b[2]) - max(a[0], b[0])
                oy = min(a[3], b[3]) - max(a[1], b[1])
                if ox <= 0 or oy <= 0 or ox * oy <= 1.0:
                    continue
                if oy <= ox:
                    upper, lower = (a, b) if a[1] + a[3] >= b[1] + b[3] else (b, a)
                    top, bottom = min(upper[3], lower[3]), max(upper[1], lower[1])
                    mid = (top + bottom) / 2.0
                    seam = nearest_plane(legal_planes(geom, max(a[0], b[0]), min(a[2], b[2]), top, bottom), mid)
                    cut = seam if seam is not None else mid
                    upper[1], lower[3] = cut + 0.25, cut - 0.25
                else:
                    right, left = (a, b) if a[0] + a[2] >= b[0] + b[2] else (b, a)
                    lo, hi = max(right[0], left[0]), min(right[2], left[2])
                    mid = (lo + hi) / 2.0
                    seam = nearest_plane(legal_planes_x(geom, max(a[1], b[1]), min(a[3], b[3]), lo, hi), mid)
                    cut = seam if seam is not None else mid
                    right[0], left[2] = cut + 0.25, cut - 0.25
                moved = True
        if not moved:
            break


def settle(boxes: Sequence[Tuple[float, Rect]], geom: PageGeometry) -> List[Rect]:
    """The student's boxes for one sheet, fitted to the page and to each other. See the module docstring."""
    page_h = geom.sheet()[1]
    max_h = (TALL_REGION - 0.005) * page_h
    # A box taller than a hotspot may be is the page itself, not an activity on it. It is left out
    # before anything is measured against it, so the smaller boxes inside it stand; cutting it down
    # to the limit instead would leave a hotspot over the top of an activity and not its foot.
    boxes = [(score, r) for score, r in boxes if r[3] - r[1] <= max_h]
    rects = [list(seam_snap(r, geom)) for _, r in drop_nested(boxes)]
    fit_blocks(rects, geom)
    part_overlaps(rects, geom)
    out: List[Rect] = []
    for r in rects:
        if r[3] - r[1] <= max_h and r[2] - r[0] >= MIN_HOTSPOT and r[3] - r[1] >= MIN_HOTSPOT:
            out.append((round(r[0], 4), round(r[1], 4), round(r[2], 4), round(r[3], 4)))
    return out


def marker_for(rect: Rect, markers: Sequence[Any], taken: set) -> Optional[Any]:
    """The printed marker that opens this box: the first one standing in its top-left quarter."""
    mid_x, mid_y = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
    best = None
    for m in markers:
        if id(m) in taken:
            continue
        b = m.span.bbox
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        if rect[0] - MARKER_TOL <= cx <= mid_x and mid_y <= cy <= rect[3] + MARKER_TOL:
            if best is None or (-cy, cx) < (-(best.span.bbox[1] + best.span.bbox[3]) / 2.0,
                                            (best.span.bbox[0] + best.span.bbox[2]) / 2.0):
                best = m
    return best


def stands_apart(marker: Any, spans: Sequence[Any]) -> bool:
    """
    Whether a printed marker is set as a label, and is not the first glyph of a line that reads as one.

    A line of mathematics that opens "b ∈ ℝ" or "2a = ..." hands the marker detector a lone "b" or
    "2" at the left margin. A label says it is one: it carries its bracket or full stop ("a)", "1."),
    or it is set bold, or in another colour than the text it opens. A bare glyph in the weight and
    colour of what follows is the text itself, and a wrong label is worse than none: the join
    refuses the publisher's entry the box belongs to.
    """
    if getattr(marker, "is_prompt", False) or getattr(marker, "is_step", False):
        return True
    span = marker.span
    text = span.text.strip()
    if any(not ch.isalnum() and not ch.isspace() for ch in text) or _is_bold(span):
        return True
    y_mid = (span.bbox[1] + span.bbox[3]) / 2.0
    after = [s for s in spans if s is not span and s.text.strip() and s.bbox[0] >= span.bbox[2] - 0.5
             and abs((s.bbox[1] + s.bbox[3]) / 2.0 - y_mid) < 2.5]
    if not after:
        return False
    return min(after, key=lambda s: s.bbox[0]).color != span.color


def _mid_y(rect: Sequence[float]) -> float:
    return (rect[1] + rect[3]) / 2.0


def between_siblings(
    rects: Sequence[Rect],
    claimed: Dict[int, Rect],
    labels: Sequence[Any],
    geom: PageGeometry,
) -> List[Tuple[Any, Rect]]:
    """
    Regions for the printed labels the student left unboxed between two labels it boxed.

    On a page of questions 3 to 7 the student may box 3, 4, 6 b), 6 c) and 7 and draw nothing at 5 and
    6 a): they are questions like their neighbours, and a teacher sees a list with two holes in it. The
    student's own boxes vouch for them: a run of at most `SIBLING_RUN` unboxed labels counts only when
    the label next above it and the label next below it in the column each head a box (`claimed`: the
    marker's id to its region), and the run counts on from the one or up to the other (`_in_sequence`).

    A region made here is the label's own lines and the answer rules under them, down to the next
    label. It is refused, and the page keeps its hole, wherever that cannot be read off the page: a
    panel or a picture stands in the stretch (whose it is cannot be told), a line of type starts left
    of the label's own text or runs out of the stretch (another paragraph), or the result would touch
    a region or be too tall or too small to be a hotspot.
    """
    page_h = geom.sheet()[1]
    max_h = (TALL_REGION - 0.005) * page_h
    out: List[Tuple[Any, Rect]] = []
    columns: Dict[int, List[Any]] = {}
    for m in labels:
        if not getattr(m, "is_prompt", False):
            columns.setdefault(m.column_index, []).append(m)

    def boxed(m: Any) -> bool:
        b = m.span.bbox
        cx, cy = (b[0] + b[2]) / 2.0, _mid_y(b)
        return any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects)

    for column in columns.values():
        column.sort(key=lambda m: -_mid_y(m.span.bbox))
        i = 0
        while i < len(column):
            if id(column[i]) in claimed or boxed(column[i]):
                i += 1
                continue
            j = i
            while j < len(column) and id(column[j]) not in claimed and not boxed(column[j]):
                j += 1
            run, i = column[i:j], j
            above = column[i - len(run) - 1] if i - len(run) - 1 >= 0 else None
            below = column[j] if j < len(column) else None
            if (above is None or below is None or id(above) not in claimed or id(below) not in claimed
                    or len(run) > SIBLING_RUN or not _in_sequence(above, run, below)):
                continue
            upper, lower = claimed[id(above)], claimed[id(below)]
            left_most = min(upper[0], lower[0]) - MARKER_TOL
            right = max(upper[2], lower[2])
            ceiling = upper[1]
            made: List[Tuple[Any, Rect]] = []
            for k, m in enumerate(run):
                floor = run[k + 1].span.bbox[3] if k + 1 < len(run) else lower[3]
                rect = _sibling_rect(m, ceiling, floor, left_most, right, geom)
                if rect is None or rect[3] - rect[1] > max_h or any(rect_overlap(rect, r) > 0.0 for r in rects):
                    made = []
                    break
                made.append((m, rect))
                ceiling = rect[1]
            # All of the run or none of it: a hole half filled says the rest are not questions.
            out.extend(made)
    return out


def _kind(m: Any) -> str:
    text = m.label.strip(".:) ")
    return "step" if getattr(m, "is_step", False) else "digit" if text.isdigit() else "letter"


def _in_sequence(above: Any, run: Sequence[Any], below: Any) -> bool:
    """
    Whether the unboxed labels count on from the boxed one above them, or up to the boxed one below.

    4 | 5 6 | b) counts on from 4; 4 | 1 | 2 opens the list that 2 belongs to. A numbered step
    ("2. Adım") heads the questions under it and is not one of them, so it is never filled in.
    """
    if any(_kind(m) == "step" for m in run):
        return False
    on = all(_kind(m) == _kind(above) and m.normalized_value == above.normalized_value + k
             for k, m in enumerate(run, 1))
    up = all(_kind(m) == _kind(below) and m.normalized_value == below.normalized_value - k
             for k, m in enumerate(reversed(run), 1))
    return on or up


def _sibling_rect(m: Any, ceiling: float, floor: float, left_most: float, right: float,
                  geom: PageGeometry) -> Optional[Rect]:
    """The lines and answer rules from label `m` down to `floor`, or None when the page does not say so plainly."""
    mb = m.span.bbox
    top = min(mb[3], ceiling - NEIGHBOUR_GAP)
    if mb[0] < left_most or mb[2] >= right or floor >= mb[1] or top <= _mid_y(mb):
        return None
    held: List[Rect] = []
    opened = False
    for ln in geom.lines:
        r = _line_rect(ln)
        if r[2] <= mb[0] - SIBLING_PAD or r[0] >= right + SIBLING_PAD or not floor < _mid_y(r) < top:
            continue
        on_label = r[1] < _mid_y(mb) < r[3]
        # A line of the question hangs under its text, right of the label; one that starts further
        # left, or runs out past the neighbours' right edge, is another paragraph.
        if r[2] > right + MARKER_TOL or (on_label and r[0] < mb[0] - SIBLING_PAD) or (not on_label and r[0] < mb[2] - SIBLING_PAD):
            return None
        opened = opened or on_label
        held.append(r)
    if not opened:
        return None
    for b in geom.blocks:
        r = tuple(b["rect"])
        if r[2] <= mb[0] or r[0] >= right or r[3] <= floor or r[1] >= top:
            continue
        if b.get("kind") in ("panel", "figure"):
            return None
        if floor < _mid_y(r) < top:
            if r[1] < floor or r[2] > right + MARKER_TOL:
                return None
            held.append(r)
    rect = (round(min(r[0] for r in held) - SIBLING_PAD, 4),
            round(max(floor + NEIGHBOUR_GAP, min(r[1] for r in held) - SIBLING_PAD), 4),
            round(max(r[2] for r in held) + SIBLING_PAD, 4),
            round(top, 4))
    if rect[2] - rect[0] < MIN_HOTSPOT or rect[3] - rect[1] < MIN_HOTSPOT:
        return None
    return rect


def _column_of(rect: Rect, layout: Any) -> int:
    cx = (rect[0] + rect[2]) / 2.0
    columns = sorted(layout.columns, key=lambda c: c.x0)
    for i, c in enumerate(columns):
        if c.x0 <= cx <= c.x1:
            return i
    return 0


def vision_page(
    prim: PagePrimitives,
    *,
    page_num: int,
    boxes: Sequence[Dict[str, Any]],
    width_px: float,
    height_px: float,
    conf: float = 0.0,
    printed_page: Optional[int] = None,
    page_oges: Sequence[Any] = (),
    icon_mode: str = "off",
    siblings: bool = True,
) -> PageResult:
    """
    One sheet's result from the student's boxes, in the shape `detect_page` returns.

    `boxes` are `{"px": [x0, y0, x1, y1], "score": float}` in the pixels of the
    rendered page the student saw (`width_px` x `height_px`). A box scoring
    below `conf` is one the student is unsure of: it is used only when a
    publisher icon vouches for it (`icons.admit`). `page_oges` are the
    publisher's entries for this sheet only, and `icon_mode` says what is done
    with them once the boxes are settled (`icons.MODES`); "off" ignores them.
    `siblings` lets an unboxed label between two boxed ones have a region
    (`between_siblings`).
    """
    layout = detect_layout(prim)
    markers = detect_activity_markers(prim, layout=layout, trace=GrowthTrace())
    geom = _page_geometry(prim, layout, markers)
    body_spans = body_spans_of(prim, layout)
    solution_blocks = [b for b in geom.blocks if b.get("kind") not in ("panel", "figure")]
    use_icons = icon_mode != "off" and bool(page_oges) and printed_page is not None

    activities: List[ActivityRegion] = []
    taken: set = set()
    points = [(float(b.get("score", 1.0)), px_to_points(b["px"], width_px, height_px, prim.width, prim.height))
              for b in boxes]
    points = [(score, r) for score, r in points if r[2] > r[0] and r[3] > r[1]]
    sure = [b for b in points if b[0] >= conf]
    labels = [m for m in markers if stands_apart(m, prim.spans)]
    if use_icons:
        sure += icons.admit(sure, [b for b in points if b[0] < conf], page_oges, prim.width, prim.height)
    # Top of the sheet first, then left to right, so ids and marker claims follow reading order.
    rects = sorted(settle(sure, geom), key=lambda r: (-r[3], r[0]))
    claimed: Dict[int, Rect] = {}

    def region(rect: Rect, m: Optional[Any], n: int) -> ActivityRegion:
        label = headline = items = None
        slug = f"v{n}"
        column = _column_of(rect, layout)
        if m is not None:
            inside = [s for s in body_spans
                      if s.bbox[0] >= rect[0] - 2.0 and s.bbox[2] <= rect[2] + 2.0
                      and s.bbox[1] >= rect[1] - 2.0 and s.bbox[3] <= rect[3] + 2.0]
            label = m.label.strip(".:) ") or None
            slug = m.slug or label or slug
            headline = build_headline(m, inside)
            items = build_sub_items(m, rect, page_num, body_spans, solution_blocks, parts=[rect]) or None
            column = m.column_index
        return ActivityRegion(id=f"p{page_num}-{slug}", label=label, column=column, rect=rect,
                              parts=[rect], headline=headline, items=items, anchored=False)

    for n, rect in enumerate(rects, 1):
        m = marker_for(rect, labels, taken)
        if m is not None:
            taken.add(id(m))
            claimed[id(m)] = rect
        activities.append(region(rect, m, n))
    if siblings:
        for m, rect in between_siblings(rects, claimed, labels, geom):
            activities.append(region(rect, m, len(activities) + 1))
        activities.sort(key=lambda a: (-a.rect[3], a.rect[0]))
    if use_icons:
        icons.apply(icon_mode, activities, page_oges, prim=prim, layout=layout, geom=geom,
                    page_num=page_num, printed_page=printed_page)

    return PageResult(layout=layout, markers=markers, activities=activities,
                      blocks=ruler_blocks(prim, layout, markers), anchor_ids=[],
                      geometry=geom if activities else None)
