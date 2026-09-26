"""
Region Growth, Vector Panel Snapping & Solution Spaces.

Grows detected activity markers into complete, non-overlapping clickable hotspots:
1. Instruction text and body lines following the marker (measure and leading continuity).
2. Drawn vector background panels (dialogue bubbles, tinted exercise cards, reading boxes)
   with all-or-nothing snapping to prevent panel cuts.
3. Solution spaces (ruled answer lines, empty table cells, answer boxes).
4. Sub-question bounding boxes.
5. De-overlapping and padding to ensure clean, non-colliding clickable hotspots.
"""

from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .figures import Figure, detect_figures
from .primitives import PagePrimitives, TextSpan, VectorDrawing
# `GrowthTrace` lives in `trace.py` so that markers, prompts and anchors can
# record into the same object without importing this module back.
from .trace import GrowthTrace
from .layout import (
    COLUMN_CHANNEL,
    COLUMN_CLEAR,
    Column,
    PageLayout,
    _channel_width,
    _column_clearance,
    _text_lines,
    local_font_size,
)
from .markers import (
    DetectedMarker,
    SubQuestion,
    parse_label,
)

# Constants for layout, panels, rules, gaps, and padding
PANEL_MIN_W = 60.0        # pt: minimum width for a text panel
PANEL_MIN_H = 30.0        # pt: minimum height for a text panel
PANEL_LINES = 2           # minimum lines of prose inside a panel
RULE_THICK = 2.5          # pt: max stroke thickness for a rule
RULE_MIN = 20.0           # pt: min length for a rule
RULE_CLUSTER = 6.0        # pt: cluster distance for rules
RULE_ROW = 30.0           # pt: max row height for a rule grid
MIN_CELL = 8.0            # pt: min cell dimension
HOTSPOT_GAP = 3.0         # pt: minimum separation between hotspots
HAIRLINE = 0.25           # pt: an overlap this shallow is a rounded seam, not a dispute
PAD = 8.0                 # pt: standard breathing room padding around text
MIN_HOTSPOT = 6.0         # pt: minimum width/height for a hotspot rect
FLOW_LEADING = 1.8        # multiple of leading that still reads as next line
FLOW_BREAK = 90.0         # pt: gap that ends flow before a list opens
HEADING_RATIO = 1.15      # font size ratio relative to body that indicates a heading
BODY_EVIDENCE = 100.0     # pt: character width evidence to settle body font size
HEADING_MEASURE = 0.6     # fraction of measure for a heading
MIN_STRIP = 24.0          # pt: minimum height for an independent strip
BRIDGE = 12.0             # pt: clear gap that does not interrupt a full-width run
STACK_GAP = 14.0          # pt: vertical gap two pieces of one region may bridge
STACK_OVERLAP = 0.6       # fraction of narrower piece that must overlap horizontally
COLUMN_EDGE = 6.0         # pt: overhang allowed for a block in a column
WHOLE_TOL = 0.05          # tolerance for whole panel/block cut check
TALL_REGION = 0.7         # fraction of the sheet past which a hotspot is no longer one
RETREAT_SHARE = 0.75      # share of a block past which a hotspot keeps it rather than retreating
RETREAT_COST = 0.5        # share of its own area a hotspot may give up to clear a block
GRAPHIC_REACH = 0.55      # share of a graphic that must lie in a band for the band to own it
GRAPHIC_DROP = 72.0       # pt: clear gap below the text past which a graphic is not its illustration
GRAPHIC_SHARE = 0.5       # share of the band past which a drawn shape is furniture, not the diagram
GRAPHIC_LINK = 24.0       # pt: gap to the next drawn shape that still reads as the same diagram
GRAPHIC_PASSES = 8        # how many rings out from the text a diagram may be followed
GRAPHIC_LABEL = 12.0      # pt: how far outside a diagram its own small-print labels may sit
GRAPHIC_HELD = 0.7        # share of a shape that must lie in a block for the block to own it


@dataclass
class SubItemRect:
    """A nested question bounding box inside an activity."""
    id: str
    label: Optional[str]
    number: Optional[int]
    part_index: int
    rect: Tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF points
    text: Optional[str] = None


@dataclass
class ActivityRegion:
    """A fully grown and separated activity region."""
    id: str                                  # e.g. "p33-a"
    label: Optional[str]                     # e.g. "a"
    column: int                              # Column index (0, 1)
    rect: Tuple[float, float, float, float]  # Primary hotspot rect (x0, y0, x1, y1)
    parts: List[Tuple[float, float, float, float]]  # Multi-column/split rects
    headline: Optional[str] = None           # First line of instruction
    items: Optional[List[SubItemRect]] = None  # Numbered sub-questions
    anchored: bool = False                   # grown from a publisher icon
    oge_id: Optional[str] = None             # the manifest entry bound to it


# Geometric utility functions

def clamp(v: float, lo: float, hi: float) -> float:
    """Clamp value v between lo and hi."""
    return max(lo, min(hi, v))


def rect_area(r: Tuple[float, float, float, float]) -> float:
    """Compute area of rectangle (x0, y0, x1, y1)."""
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def rect_overlap(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float]
) -> float:
    """Compute overlap area between two rectangles."""
    return (
        max(0.0, min(a[2], b[2]) - max(a[0], b[0])) *
        max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    )


def rect_intersects(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float]
) -> bool:
    """Check if two rectangles overlap with non-zero area."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def rect_union(
    a: Optional[Tuple[float, float, float, float]],
    b: Optional[Tuple[float, float, float, float]]
) -> Optional[Tuple[float, float, float, float]]:
    """Compute bounding union of two rectangles."""
    if a is None:
        return b
    if b is None:
        return a
    return (
        min(a[0], b[0]),
        min(a[1], b[1]),
        max(a[2], b[2]),
        max(a[3], b[3]),
    )


def encloses(
    outer: Tuple[float, float, float, float],
    inner: Tuple[float, float, float, float],
    tol: float = 0.0
) -> bool:
    """Check if outer rectangle encloses inner rectangle with optional tolerance."""
    return (
        outer[0] <= inner[0] + tol and
        outer[2] >= inner[2] - tol and
        outer[1] <= inner[1] + tol and
        outer[3] >= inner[3] - tol
    )


def contains_point(
    rect: Tuple[float, float, float, float],
    x: float,
    y: float
) -> bool:
    """Check if point (x, y) is inside rect."""
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def cuts(
    rect: Tuple[float, float, float, float],
    block: Tuple[float, float, float, float],
    whole_tol: Optional[float] = None
) -> bool:
    """
    Check if rect cuts block instead of taking it whole or leaving it alone.
    Returns True if overlap share is between whole_tol and 1 - whole_tol.

    `whole_tol` defaults to the module's `WHOLE_TOL` *at call time*, not at
    definition time: a profile rebinds the global, and a default argument
    evaluated once at import would have pinned every internal caller to the
    value the module was loaded with. The scorer passes its own frozen
    tolerance explicitly, so the ruler and the detector stay independent.
    """
    block_area = rect_area(block)
    if block_area <= 0.0:
        return False
    tol = WHOLE_TOL if whole_tol is None else whole_tol
    share = rect_overlap(rect, block) / block_area
    return share > tol and share < 1.0 - tol


def is_prose(span: TextSpan) -> bool:
    """Check if a text span contains prose text (letters/numbers, not a standalone label)."""
    text = span.text.strip()
    if not text:
        return False
    if parse_label(text):
        return False
    return bool(re.search(r"[\w]", text, re.UNICODE))


@dataclass
class TextLine:
    """A line of text grouped by shared baseline."""
    x0: float
    y0: float
    x1: float
    y1: float
    y: float  # Baseline mid
    spans: List[TextSpan]


def group_lines(spans: List[TextSpan]) -> List[TextLine]:
    """Group text spans sharing the same baseline (within 2.5 pt) into lines."""
    sorted_spans = sorted(spans, key=lambda s: -(s.bbox[1] + s.bbox[3]) / 2.0)
    lines: List[TextLine] = []

    for s in sorted_spans:
        y_mid = (s.bbox[1] + s.bbox[3]) / 2.0
        if lines and abs(lines[-1].y - y_mid) < 2.5:
            line = lines[-1]
            line.spans.append(s)
            line.x0 = min(line.x0, s.bbox[0])
            line.x1 = max(line.x1, s.bbox[2])
            line.y0 = min(line.y0, s.bbox[1])
            line.y1 = max(line.y1, s.bbox[3])
        else:
            lines.append(
                TextLine(
                    x0=s.bbox[0],
                    y0=s.bbox[1],
                    x1=s.bbox[2],
                    y1=s.bbox[3],
                    y=y_mid,
                    spans=[s],
                )
            )

    for line in lines:
        line.spans.sort(key=lambda s: s.bbox[0])

    return lines


# Panel detection

def detect_panels(
    drawings: List[VectorDrawing],
    body_spans: List[TextSpan],
    page_h: Optional[float] = None,
    markers: Optional[Sequence[Any]] = None,
) -> List[Tuple[float, float, float, float]]:
    """
    Detect drawn vector background panels (dialogue bubbles, tinted boxes).

    Identifies shapes with width >= PANEL_MIN_W (60 pt) and height >= PANEL_MIN_H (30 pt)
    that contain at least PANEL_LINES (2) lines of prose, filtering out nested panels.
    Multi-question container backgrounds or panels taller than TALL_REGION * page_h
    are filtered out as page furniture.
    """
    inferred_h = (
        page_h
        or (max((d.rect[3] for d in drawings), default=0.0) if drawings else 0.0)
        or (max((s.bbox[3] for s in body_spans), default=0.0) if body_spans else 842.0)
    )
    max_panel_h = (TALL_REGION - 0.005) * inferred_h

    marker_boxes: List[Tuple[float, float, float, float]] = []
    if markers:
        for m in markers:
            mb = getattr(m, "span", m)
            bbox = getattr(mb, "bbox", mb)
            marker_boxes.append(bbox)

    prose_spans = [s for s in body_spans if is_prose(s)]
    candidates: List[Tuple[float, float, float, float]] = []

    for d in drawings:
        r = d.rect
        w = r[2] - r[0]
        h = r[3] - r[1]
        if w < PANEL_MIN_W or h < PANEL_MIN_H or h > max_panel_h:
            continue

        if marker_boxes and sum(1 for mb in marker_boxes if encloses(r, mb, tol=-2.0)) > 1:
            continue

        # Count distinct prose text rows enclosed in this drawing
        rows: Set[int] = set()
        for s in prose_spans:
            s_mid_y = (s.bbox[1] + s.bbox[3]) / 2.0
            if encloses(r, s.bbox, tol=2.0):
                rows.add(round(s_mid_y))

        if len(rows) >= PANEL_LINES:
            candidates.append(r)

    # Sort largest first to keep outermost panels
    candidates.sort(key=lambda p: rect_area(p), reverse=True)
    out: List[Tuple[float, float, float, float]] = []
    for panel in candidates:
        if not any(encloses(held, panel, tol=2.0) for held in out):
            out.append(panel)

    return out


# Solution space detection

def detect_solution_spaces(
    drawings: List[VectorDrawing],
    body_spans: List[TextSpan],
    page_h: Optional[float] = None,
) -> List[dict]:
    """
    Detect solution spaces (ruled answer lines, empty table cells, answer boxes).

    Returns a list of solution blocks:
    [{"rect": (x0, y0, x1, y1), "kind": "cell" | "grid" | "panel"}, ...]

    A ruled lattice is emitted twice over: once as its individual empty cells,
    which is what a sub-question absorbs when it writes its answer into one, and
    once whole, as a `grid`. Only the cells the table leaves blank survive the
    text filter, so a table read as cells alone is a *scatter* -- the worked
    example on sheet 22 of the philosophy book has 176 cells of which 151 are
    emitted -- and a region's edge falls between two of them as easily as
    outside the table. The whole lattice is the thing the page draws, and it is
    what a region has to take or leave entire.
    """
    horizontal: List[Tuple[float, float, float, float]] = []
    vertical: List[Tuple[float, float, float, float]] = []
    panels: List[Tuple[float, float, float, float]] = []

    for d in drawings:
        r = d.rect
        w = r[2] - r[0]
        h = r[3] - r[1]
        if h <= RULE_THICK * 1.5 and w >= RULE_MIN:
            horizontal.append(r)
        elif w <= RULE_THICK * 1.5 and h >= RULE_MIN:
            vertical.append(r)
        if w >= RULE_MIN and h >= MIN_CELL:
            panels.append(r)

    if not horizontal and not vertical:
        return []

    prose_spans = [s for s in body_spans if is_prose(s)]

    def says_something(r: Tuple[float, float, float, float]) -> bool:
        """Check if any prose span has its center inside r."""
        for s in prose_spans:
            cx = (s.bbox[0] + s.bbox[2]) / 2.0
            cy = (s.bbox[1] + s.bbox[3]) / 2.0
            if contains_point(r, cx, cy):
                return True
        return False

    # Cluster rules using connected components
    rules = horizontal + vertical
    n_rules = len(rules)
    parent = list(range(n_rules))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    def near(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
        is_vert_a = (a[2] - a[0] <= RULE_THICK * 1.5)
        is_vert_b = (b[2] - b[0] <= RULE_THICK * 1.5)
        if is_vert_a and is_vert_b:
            vert_overlap = min(a[3], b[3]) - max(a[1], b[1])
            min_h = min(a[3] - a[1], b[3] - b[1])
            return vert_overlap >= 0.5 * min_h and abs(a[0] - b[0]) <= 350.0

        if min(a[2], b[2]) + RULE_CLUSTER < max(a[0], b[0]):
            return False
        if min(a[3], b[3]) + RULE_ROW < max(a[1], b[1]):
            return False
        # Do not link rules across text: if a prose span sits in the vertical gap between them,
        # they belong to different exercises/tables rather than one empty answer grid.
        gap_bot = min(a[3], b[3])
        gap_top = max(a[1], b[1])
        if gap_top - gap_bot > 6.0:
            for s in prose_spans:
                s_y = (s.bbox[1] + s.bbox[3]) / 2.0
                if gap_bot <= s_y <= gap_top:
                    if min(a[2], b[2], s.bbox[2]) > max(a[0], b[0], s.bbox[0]):
                        return False
        return True

    for i in range(n_rules):
        for j in range(i + 1, n_rules):
            if near(rules[i], rules[j]):
                union(i, j)

    clusters: Dict[int, List[Tuple[float, float, float, float]]] = {}
    for i in range(n_rules):
        root = find(i)
        if root not in clusters:
            clusters[root] = []
        clusters[root].append(rules[i])

    blocks: List[dict] = []
    for cluster in clusters.values():
        rows = [r for r in cluster if r[3] - r[1] <= RULE_THICK * 1.5]
        cols_rules = [r for r in cluster if r[2] - r[0] <= RULE_THICK * 1.5]
        if len(rows) < 2 and len(cols_rules) < 2:
            continue

        box = cluster[0]
        for r in cluster[1:]:
            box = rect_union(box, r)

        if len(rows) >= 2:
            ys = sorted(set(round((r[1] + r[3]) / 2.0, 1) for r in rows), reverse=True)
            cols = sorted(set(round((r[0] + r[2]) / 2.0, 1) for r in cols_rules))
            if len(cols) >= 2:
                xs = cols
            else:
                xs = [box[0], box[2]]
                gaps = [ys[k] - ys[k + 1] for k in range(len(ys) - 1)]
                gaps.sort()
                pitch = gaps[len(gaps) // 2] if gaps else RULE_ROW / 2.0
                ys.insert(0, ys[0] + pitch)
        else:
            # Vertical-only rules: columns defined without explicit horizontal rows
            cols = sorted(set(round((r[0] + r[2]) / 2.0, 1) for r in cols_rules))
            if len(cols) < 2:
                continue
            xs = cols
            y_top = max(r[3] for r in cols_rules)
            y_bot = min(r[1] for r in cols_rules)
            if y_top - y_bot < MIN_CELL:
                continue
            ys = [y_top, y_bot]

        # Guard against dense decorative mesh / graph paper grids (e.g. a0e5ed1a)
        # where thousands of tiny rules generate tens of thousands of tiny cells.
        num_cells = (len(ys) - 1) * (len(xs) - 1)
        if num_cells > 200:
            avg_w = (box[2] - box[0]) / max(1, len(xs) - 1)
            avg_h = (box[3] - box[1]) / max(1, len(ys) - 1)
            if avg_w * avg_h < 200.0 or len(ys) > 40:
                blocks.append({"rect": box, "kind": "grid"})
                continue

        cells = []
        for i in range(len(ys) - 1):
            for j in range(len(xs) - 1):
                cell = (xs[j], ys[i + 1], xs[j + 1], ys[i])
                if cell[2] - cell[0] < MIN_CELL or cell[3] - cell[1] < MIN_CELL:
                    continue
                if says_something(cell):
                    continue
                cells.append(cell)

        for cell in cells:
            blocks.append({"rect": cell, "kind": "cell"})

        # A lattice: ruled both ways, so the sheet draws it as one table.
        if len(ys) >= 3 and len(cols) >= 2:
            blocks.append({"rect": box, "kind": "grid"})

        # Silent enclosing card panel
        card = None
        for panel in panels:
            if not encloses(panel, box):
                continue
            if says_something(panel):
                continue
            other_clusters = [
                c for c in clusters.values()
                if c is not cluster and any(contains_point(panel, (r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0) for r in c)
            ]
            if other_clusters:
                continue
            if card is None or rect_area(panel) > rect_area(card):
                card = panel

        if card:
            blocks.append({"rect": card, "kind": "panel"})

    inferred_h = (
        page_h
        or (max((d.rect[3] for d in drawings), default=0.0) if drawings else 0.0)
        or (max((s.bbox[3] for s in body_spans), default=0.0) if body_spans else 842.0)
    )
    max_sol_h = (TALL_REGION - 0.005) * inferred_h
    return [b for b in blocks if (b["rect"][3] - b["rect"][1]) <= max_sol_h]


# Strip & cell segmentation for multi-column and full-width layouts

@dataclass
class Strip:
    top: float
    bottom: float
    active: List[bool]
    absorb: List[bool]
    dead: bool
    cells: List[dict] = field(default_factory=list)


def _column_flow_end(lines: List[dict], column: Column, top: float, bottom: float) -> float:
    """Find the height where a column's flow ends."""
    own = [
        l for l in lines
        if (
            (l["x0"] + l["x1"]) / 2.0 >= column.x0 and
            (l["x0"] + l["x1"]) / 2.0 <= column.x1 and
            l["x0"] >= column.x0 - COLUMN_EDGE and
            l["x1"] <= column.x1 + COLUMN_EDGE and
            l["y1"] <= top + 1.0 and
            l["y0"] >= bottom - 1.0
        )
    ]
    own.sort(key=lambda l: -l["y1"])

    end = top
    for l in own:
        if l["y1"] < end - FLOW_BREAK:
            break
        end = min(end, l["y0"])

    return max(end, bottom)


def _blocked_runs(
    blocks: List[dict],
    boundary: float,
    top: float,
    bottom: float
) -> List[Tuple[float, float]]:
    """Heights in [bottom, top] where no channel separates the two sides."""
    edges = {top, bottom}
    for b in blocks:
        for y in (b["y0"], b["y1"]):
            if bottom < y < top:
                edges.add(y)
    ys = sorted(edges, reverse=True)

    runs: List[List[float]] = []
    clear_height = 0.0
    for i in range(len(ys) - 1):
        hi, lo = ys[i], ys[i + 1]
        if hi - lo < 0.25:
            continue
        w = _channel_width(blocks, boundary, hi, lo)
        if w is None:
            continue
        if w >= COLUMN_CHANNEL:
            clear_height += (hi - lo)
            continue
        if runs and clear_height <= BRIDGE:
            runs[-1][0] = lo
        else:
            runs.append([lo, hi])
        clear_height = 0.0

    return [(lo, hi) for lo, hi in runs if hi - lo >= MIN_STRIP]


def _coalesce_strips(strips: List[Strip]) -> List[Strip]:
    """Fuse neighbouring strips whose column layouts match."""
    out: List[Strip] = []
    for s in strips:
        if (
            out and
            out[-1].dead == s.dead and
            out[-1].active == s.active and
            out[-1].absorb == s.absorb
        ):
            out[-1].bottom = min(out[-1].bottom, s.bottom)
        else:
            out.append(
                Strip(
                    top=s.top,
                    bottom=s.bottom,
                    active=list(s.active),
                    absorb=list(s.absorb),
                    dead=s.dead,
                )
            )
    return out


def _cells_for(columns: List[Column], active: List[bool], absorb: List[bool]) -> List[dict]:
    """Generate cells for a strip."""
    cells = []
    current = None
    for i, col in enumerate(columns):
        if current and not active[i] and absorb[i - 1]:
            current["x1"] = col.x1
            continue
        current = {"x0": col.x0, "x1": col.x1, "column": i}
        cells.append(current)
    return cells


def segment_strips(
    blocks: List[dict],
    lines: List[dict],
    columns: List[Column],
    content_top: float,
    content_bottom: float,
    markers: List[DetectedMarker],
) -> List[Strip]:
    """Segment content area into horizontal strips reflecting column openings and flow."""
    opens_at = [col.opens_at for col in columns]
    closes_at = [
        content_bottom if i == 0 else _column_flow_end(lines, col, opens_at[i], content_bottom)
        for i, col in enumerate(columns)
    ]
    boundaries = len(columns) - 1

    base_cuts = [content_top, content_bottom]
    for y in opens_at:
        if content_bottom < y < content_top:
            base_cuts.append(y)
    base_cuts.sort(reverse=True)

    blocked: List[List[Tuple[float, float]]] = [[] for _ in range(boundaries)]
    for i in range(len(base_cuts) - 1):
        top = base_cuts[i]
        bottom = base_cuts[i + 1]
        if top - bottom < 0.5:
            continue
        mid = (top + bottom) / 2.0
        for c in range(boundaries):
            if mid > opens_at[c] or mid <= opens_at[c + 1]:
                continue
            blocked[c].extend(_blocked_runs(blocks, columns[c + 1].x0 - 4.0, top, bottom))

    for c in range(boundaries):
        end = closes_at[c + 1]
        if end <= content_bottom + 0.5:
            continue
        boundary = columns[c + 1].x0 - 4.0
        if _column_clearance(blocks, boundary, end, content_bottom, straddle_closes=True) >= COLUMN_CLEAR:
            continue
        from_col = columns[c]
        for b in lines:
            starts_here = b["x0"] >= from_col.x0 - COLUMN_EDGE and b["x0"] <= from_col.x1
            if starts_here and b["y0"] < end and b["y1"] > end:
                end = b["y0"]
        if end > content_bottom + 0.5:
            blocked[c].append((content_bottom, end))

    cuts_set = set(base_cuts)
    for run_list in blocked:
        for lo, hi in run_list:
            if content_bottom < lo < content_top:
                cuts_set.add(lo)
            if content_bottom < hi < content_top:
                cuts_set.add(hi)

    ys = sorted(cuts_set, reverse=True)
    strips: List[Strip] = []
    for i in range(len(ys) - 1):
        top = ys[i]
        bottom = ys[i + 1]
        if top - bottom < 0.5:
            continue
        mid = (top + bottom) / 2.0
        opened = [mid <= y for y in opens_at]
        active = [isOpen and mid > closes_at[k] for k, isOpen in enumerate(opened)]
        absorb = [any(lo < mid < hi for lo, hi in blocked[k]) for k in range(boundaries)]
        strips.append(
            Strip(
                top=top,
                bottom=bottom,
                active=active,
                absorb=absorb,
                dead=not any(opened),
            )
        )

    if not strips:
        strips = [
            Strip(
                top=content_top,
                bottom=content_bottom,
                active=[True] * len(columns),
                absorb=[False] * boundaries,
                dead=False,
            )
        ]

    strips = _coalesce_strips(strips)

    # Absorb slivers shorter than MIN_STRIP unless holding a marker
    def holds_marker(s: Strip) -> bool:
        for m in markers:
            m_y = (m.span.bbox[1] + m.span.bbox[3]) / 2.0
            if s.bottom < m_y <= s.top:
                return True
        return False

    while len(strips) > 1:
        shortest_idx = -1
        for i, s in enumerate(strips):
            if holds_marker(s):
                continue
            if shortest_idx == -1 or (s.top - s.bottom < strips[shortest_idx].top - strips[shortest_idx].bottom):
                shortest_idx = i

        if shortest_idx == -1:
            break
        sliver = strips[shortest_idx]
        if sliver.top - sliver.bottom >= MIN_STRIP:
            break

        usable = lambda t: t is not None and (sliver.dead or not t.dead)
        height = lambda t: (t.top - t.bottom) if usable(t) else -1.0

        above = strips[shortest_idx - 1] if shortest_idx > 0 else None
        below = strips[shortest_idx + 1] if shortest_idx + 1 < len(strips) else None

        host = above if height(above) >= height(below) else below
        if not usable(host) or host is None:
            break

        host.top = max(host.top, sliver.top)
        host.bottom = min(host.bottom, sliver.bottom)
        strips.pop(shortest_idx)
        strips = _coalesce_strips(strips)

    for s in strips:
        s.cells = _cells_for(columns, s.active, s.absorb)

    return strips


def build_page_cells(
    strips: List[Strip],
    columns: List[Column],
    markers: List[DetectedMarker],
    content_top: float,
    content_bottom: float,
) -> List[dict]:
    """Group strips into blocks and generate cells for each column channel."""
    blocks: List[List[Strip]] = []
    current_block: List[Strip] = []

    def same_layout(s1: Strip, s2: Strip) -> bool:
        if s1.dead != s2.dead or len(s1.cells) != len(s2.cells):
            return False
        for c1, c2 in zip(s1.cells, s2.cells):
            if c1["column"] != c2["column"]:
                return False
            if abs(c1["x0"] - c2["x0"]) > 1.0 or abs(c1["x1"] - c2["x1"]) > 1.0:
                return False
        return True

    for s in strips:
        if not current_block or same_layout(current_block[-1], s):
            current_block.append(s)
        else:
            blocks.append(current_block)
            current_block = [s]
    if current_block:
        blocks.append(current_block)

    cells: List[dict] = []
    for block_idx, block in enumerate(blocks):
        block_cells: List[dict] = []
        for s in block:
            shared = len(s.cells) > 1
            for span in s.cells:
                block_cells.append({
                    "x0": span["x0"],
                    "x1": span["x1"],
                    "column": span["column"],
                    "shared": shared,
                    "top": s.top,
                    "bottom": s.bottom,
                    "dead": s.dead,
                    "block_index": block_idx,
                    "markers": [],
                })
        block_cells.sort(key=lambda c: (c["column"], -c["top"]))
        cells.extend(block_cells)

    # Fuse adjacent vertical cells in same column & block
    for i in range(len(cells) - 1, 0, -1):
        above = cells[i - 1]
        cell = cells[i]
        if (
            above["block_index"] == cell["block_index"] and
            above["column"] == cell["column"] and
            above["dead"] == cell["dead"] and
            above["shared"] == cell["shared"] and
            abs(above["x0"] - cell["x0"]) < 0.5 and
            abs(above["x1"] - cell["x1"]) < 0.5 and
            abs(above["bottom"] - cell["top"]) < 0.5
        ):
            above["bottom"] = cell["bottom"]
            cells.pop(i)

    # Assign markers to cells
    for m in markers:
        m_y = (m.span.bbox[1] + m.span.bbox[3]) / 2.0
        m_cx = (m.span.bbox[0] + m.span.bbox[2]) / 2.0
        best_cell = None
        best_dist = float("inf")
        for cell in cells:
            if m_y > cell["top"] or m_y <= cell["bottom"]:
                continue
            if cell["x0"] <= m_cx <= cell["x1"]:
                best_cell = cell
                break
            dist = min(abs(m_cx - cell["x0"]), abs(m_cx - cell["x1"]))
            if dist < best_dist:
                best_dist = dist
                best_cell = cell
        if best_cell:
            best_cell["markers"].append(m)

    return cells


@dataclass
class PageGeometry:
    """
    What a page draws, in the form the separation passes need to ask about it.

    Both de-overlap passes used to cut a contested span at its midpoint, which
    is a height chosen with no reference to the page: it lands inside a line of
    type as readily as between two, and inside a panel as readily as at its
    edge. Carrying the lines and the drawn blocks lets them cut where the page
    itself already has a seam.
    """
    lines: List[dict]
    blocks: List[dict]          # [{"rect": (x0, y0, x1, y1), "kind": str}, ...]
    content_box: Tuple[float, float, float, float]


def legal_planes(
    geom: Optional[PageGeometry],
    x0: float,
    x1: float,
    top: float,
    bottom: float,
) -> List[float]:
    """
    The heights a rectangle may be cut at inside a contested span.

    A seam is legal where the page has one: the clear space between two lines
    of type, and the edge of anything the page draws as a single object. A
    height inside a line, or inside a block, is not a seam -- cutting there
    slices a sentence or a panel in half, which is the defect these planes
    exist to stop.
    """
    if geom is None or top <= bottom:
        return []

    rows = _lines_within(geom.lines, x0, x1, top, bottom)
    planes: List[float] = []
    for i in range(len(rows) - 1):
        upper, lower = rows[i], rows[i + 1]
        if upper["y0"] > lower["y1"]:
            planes.append((upper["y0"] + lower["y1"]) / 2.0)

    for b in geom.blocks:
        r = b["rect"]
        if r[2] <= x0 or r[0] >= x1:
            continue
        planes.extend((r[1], r[3]))

    def clear(y: float) -> bool:
        if not (bottom < y < top):
            return False
        for row in rows:
            if row["y0"] < y < row["y1"]:
                return False
        for b in geom.blocks:
            r = b["rect"]
            if r[2] <= x0 or r[0] >= x1:
                continue
            if r[1] < y < r[3]:
                return False
        return True

    return sorted({round(y, 4) for y in planes if clear(y)})


def legal_planes_x(
    geom: Optional[PageGeometry],
    y0: float,
    y1: float,
    left: float,
    right: float,
) -> List[float]:
    """
    `legal_planes` across the measure: where a rectangle may be cut vertically.

    The seams here are the clear channels between two lines standing side by
    side, and the vertical edges of drawn blocks. Without them the horizontal
    branch of the separation passes still cut blind, and a bubble reaching into
    a column was sliced down its middle however carefully the vertical branch
    had been taught.
    """
    if geom is None or right <= left:
        return []

    rows = [
        l for l in geom.lines
        if min(l["y1"], y1) - max(l["y0"], y0) > 0
    ]
    planes: List[float] = []
    for b in geom.blocks:
        r = b["rect"]
        if r[3] <= y0 or r[1] >= y1:
            continue
        planes.extend((r[0], r[2]))
    for l in rows:
        planes.extend((l["x0"], l["x1"]))

    def clear(x: float) -> bool:
        if not (left < x < right):
            return False
        for l in rows:
            if l["x0"] < x < l["x1"]:
                return False
        for b in geom.blocks:
            r = b["rect"]
            if r[3] <= y0 or r[1] >= y1:
                continue
            if r[0] < x < r[2]:
                return False
        return True

    return sorted({round(x, 4) for x in planes if clear(x)})


def nearest_plane(planes: List[float], target: float) -> Optional[float]:
    """The legal plane closest to where a blind cut would have fallen."""
    if not planes:
        return None
    return min(planes, key=lambda y: abs(y - target))


def holds_other_marker(
    rect: Tuple[float, float, float, float],
    marker: Optional[DetectedMarker],
    markers: List[DetectedMarker],
) -> bool:
    """Whether some other activity's label stands inside `rect`."""
    return any(
        m is not marker and encloses(rect, m.span.bbox, tol=-2.0)
        for m in markers
    )


def spans_other_activity(
    rect: Tuple[float, float, float, float],
    marker: Optional[DetectedMarker],
    markers: List[DetectedMarker],
) -> bool:
    """
    Whether `rect` reaches across a row another activity starts on.

    Containment is not enough to decide that a block belongs to one activity.
    A workbook often sets a single answer box in the margin beside a run of
    questions -- on sheet 36 of the ELT book one box at x 432..536 stands
    against the rows of `c`, `d` and `e` at once. No marker is inside it, so
    containment says it is free, and the first question to reach it swallows
    the rows of the two below and squeezes them off the sheet. A block level
    with somebody else's label is shared furniture, and shared furniture
    belongs to nobody.
    """
    return any(
        m is not marker
        and rect[1] < (m.span.bbox[1] + m.span.bbox[3]) / 2.0 < rect[3]
        for m in markers
    )


# Panel & solution snapping

def panel_for(
    marker: DetectedMarker,
    band_top: float,
    band_bottom: float,
    content: Tuple[float, float, float, float],
    panels: List[Tuple[float, float, float, float]],
    all_markers: List[DetectedMarker],
    flow_spans: List[TextSpan],
    col_x0: Optional[float] = None,
    col_x1: Optional[float] = None,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Find the drawn panels an activity runs into and snap to them whole.

    A panel is taken on either of two grounds. The flow may run into it, which
    is the plain case -- the instruction continues inside a tinted card and the
    card is part of the instruction. Or the panel may simply stand in the
    activity's band, below its text, in its column, with no other activity's
    letter inside it: an exercise that says "consider the steps below" and then
    prints the steps in three stacked boxes is one activity, but its text stops
    at the first box's heading, so nothing of its flow is ever inside any of
    them. Taking only the first ground leaves that activity as its own opening
    sentence and drops the whole of the work it asks for.

    Panels are considered from the top down so that each one taken extends the
    reach of the next, which is how a stack of boxes is picked up whole rather
    than only as far as the first gap.

    Returns the union of snapped panels, or None.
    """
    if not content or not panels:
        return None

    def free_of_other_markers(panel: Tuple[float, float, float, float]) -> bool:
        return not any(
            m is not marker and encloses(panel, m.span.bbox, tol=-2.0) for m in all_markers
        )

    grown = None
    reach = content[1]  # how far down the activity has been carried so far
    for panel in sorted(panels, key=lambda pn: -pn[3]):
        if not free_of_other_markers(panel):
            continue

        # Held in band
        held = min(panel[3], band_top) - max(panel[1], band_bottom)
        if held < 0.5 * (panel[3] - panel[1]):
            continue

        # First ground: the flow runs into the panel and along it.
        has_text_inside = bool(flow_spans) and any(
            contains_point(panel, (s.bbox[0] + s.bbox[2]) / 2.0, (s.bbox[1] + s.bbox[3]) / 2.0)
            for s in flow_spans
        )
        along = min(content[3], panel[3]) - max(content[1], panel[1])
        if has_text_inside and along >= 0.5 * (panel[3] - panel[1]):
            grown = rect_union(grown, panel)
            reach = min(reach, panel[1])
            continue

        # Second ground: the panel stands in the band, under the activity, in
        # its column. The band is bounded by the next activity's own letter, so
        # what is inside it and behind no other letter belongs to this one.
        if col_x0 is None or col_x1 is None:
            continue
        if panel[1] < band_bottom - 1.0 or panel[3] > band_top + 1.0:
            continue
        if panel[0] < col_x0 - COLUMN_EDGE or panel[2] > col_x1 + COLUMN_EDGE:
            continue
        # It has to follow on. A panel separated from everything the activity
        # has reached so far by more than a flow break belongs to whatever sits
        # in that gap, not to this activity.
        if panel[3] < reach - FLOW_BREAK:
            continue

        grown = rect_union(grown, panel)
        reach = min(reach, panel[1])

    return grown


def solution_for(
    marker: DetectedMarker,
    band_top: float,
    band_bottom: float,
    column_x0: float,
    column_x1: float,
    content: Tuple[float, float, float, float],
    solution_blocks: List[dict],
    body_spans: List[TextSpan],
    all_markers: List[DetectedMarker],
    activity_spans: List[TextSpan],
    adjacent: Optional[float] = None,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Absorb empty solution space (ruled answer lines, empty table cells).
    """
    if not content or not solution_blocks:
        return None

    left = min(column_x0, content[0]) - COLUMN_EDGE
    right = max(column_x1, content[2]) + COLUMN_EDGE

    # What counts as somebody else's text standing between this question and its
    # answer space.
    #
    # This used to be "every prose span the flow did not absorb", which makes
    # the flow the arbiter of ownership and is wrong exactly when the flow falls
    # short: an activity whose flow stopped at its option list has the rest of
    # its own question standing in the gap, so it walls itself off from the
    # ruled lines the workbook printed for it. Ownership belongs to the band --
    # the scope the activity was read in -- not to the accident of what the flow
    # reached. Another activity's label is still a wall wherever it stands.
    marker_span_ids = {id(m.span) for m in all_markers if m is not marker}

    def is_own(span: TextSpan) -> bool:
        cx = (span.bbox[0] + span.bbox[2]) / 2.0
        cy = (span.bbox[1] + span.bbox[3]) / 2.0
        return (
            band_bottom - 1.0 <= cy <= band_top + 1.0
            and left <= cx <= right
        )

    walls = [
        s for s in body_spans
        if (is_prose(s) and not is_own(s)) or id(s) in marker_span_ids
    ]

    grown = None
    for block in solution_blocks:
        b_rect = block["rect"]
        if adjacent is not None and block["kind"] != "cell":
            continue

        # A table is one object: it is taken entire or left entire, and it is
        # only this activity's to take if no other activity's label stands in
        # it. Its own cells are still offered separately, for the sub-questions
        # that write into them.
        if block["kind"] == "grid":
            if holds_other_marker(b_rect, marker, all_markers):
                continue
            if b_rect[1] < band_bottom - 1.0 or b_rect[3] > band_top + 1.0:
                continue
            if b_rect[0] < left - COLUMN_EDGE or b_rect[2] > right + COLUMN_EDGE:
                continue

        overlap = min(b_rect[3], band_top) - max(b_rect[1], band_bottom)
        if overlap < 0.5 * (b_rect[3] - b_rect[1]):
            continue

        # How far the block reaches across the activity's measure. Asking that
        # the *block* be half covered fails a full-measure ruled line whenever
        # the column reading is narrower than the line, which is the common case
        # on a single-column sheet misread as two; a block that sits inside the
        # measure is just as much this activity's.
        across = min(b_rect[2], right) - max(b_rect[0], left)
        inside = b_rect[0] >= left - COLUMN_EDGE and b_rect[2] <= right + COLUMN_EDGE
        if not inside and across < 0.5 * (b_rect[2] - b_rect[0]):
            continue

        # The empty space between the question and its ruled lines, whichever
        # way round the two sit. Measuring it as though the lines were always
        # below the text left the test vacuous whenever they were not -- the
        # span came out empty, nothing could stand in it, and a question
        # absorbed every ruled line above it as far as the page went.
        if b_rect[3] <= content[1]:
            gap_bottom, gap_top = b_rect[3], content[1]
        elif b_rect[1] >= content[3]:
            gap_bottom, gap_top = content[3], b_rect[1]
        else:
            # They overlap vertically; there is no gap to cross.
            gap_bottom = gap_top = None

        if gap_top is not None:
            max_gap = adjacent if adjacent is not None else GRAPHIC_DROP
            if gap_top - gap_bottom > max_gap:
                continue

            # Anything else's prose standing in that gap is a wall: the space
            # beyond it answers a different question.
            blocked = any(
                gap_bottom < (w.bbox[1] + w.bbox[3]) / 2.0 < gap_top and
                w.bbox[2] > min(content[0], b_rect[0]) and
                w.bbox[0] < max(content[2], b_rect[2])
                for w in walls
            )
            if blocked:
                continue

        grown = rect_union(grown, b_rect)

    return grown


# Graphic absorption

def graphics_for(
    marker: DetectedMarker,
    band_top: float,
    band_bottom: float,
    band_x0: float,
    band_x1: float,
    content: Optional[Tuple[float, float, float, float]],
    figures: List[Figure],
    drawings: List[VectorDrawing],
    all_markers: List[DetectedMarker],
    text_lines: Sequence[dict] = (),
    band_font_size: float = 10.0,
    blocks: Sequence[Tuple[float, float, float, float]] = (),
) -> Optional[Tuple[float, float, float, float]]:
    """
    The drawn matter this activity's band contains, as one rectangle.

    `flow_content` reads type, and a textbook question is very often half
    picture: an instruction of two lines over a diagram that fills the rest of
    the band. Growing on the text alone stopped the hotspot at the end of the
    instruction, and the panel and solution rules only reached the picture when
    the page happened to have drawn a frame or a full set of answer rules
    around it. Most pages have not, which is why a region so often covered the
    question and none of what the question was about. Worse, the type inside a
    diagram is small, and `is_aside` -- rightly, for captions -- drops small
    type from the flow, so the parts of a chart furthest from the instruction
    were the first to be lost. That is the "only half of it" case exactly.

    Two kinds of matter are gathered. A figure is a whole picture, already
    assembled out of its tiles, and is taken when the band holds it. Vector art
    is gathered differently, by contiguity: a drawn diagram is not one shape but
    dozens -- boxes, arrows, rules -- and no single one of them is the picture.
    Starting from what the activity already holds and reaching out to the drawn
    shapes next to it, over and over until nothing further is in reach, follows
    the diagram to its own edge and stops at the white space around it.

    Everything is bounded by the band, which is the scope the page itself gives
    this activity -- it runs from this marker to the next -- and any shape
    carrying another activity's label is refused outright.

    `blocks` are the drawn panels and answer grids the page was found to have.
    They are not gathered here: a frame is `panel_for`'s to snap to and a ruled
    answer grid is `solution_for`'s, and both of those take a block whole or
    leave it. This pass reaches by contiguity, and a ruled grid is contiguous
    with itself, so without knowing about them it walks a few rules into an
    answer block and stops where the reaching stops -- which is a region ending
    inside a block, the one thing the closure rule exists to prevent. Shapes
    and labels that belong to a block this activity does not already hold are
    therefore left where they are.
    """
    band = (band_x0, band_bottom, band_x1, band_top)
    band_area = rect_area(band)
    if band_area <= 0.0:
        return None

    # Blocks this activity has not already taken whole. A block bigger than the
    # band is the sheet's own ground rather than a frame round this question --
    # `panel_for` declines those too -- and nothing that reaches over one can
    # be said to have cut it.
    def _loose(held: Optional[Tuple[float, float, float, float]]) -> List[Tuple[float, float, float, float]]:
        out = []
        for b in blocks:
            area = rect_area(b)
            if area <= 0.0 or area > band_area:
                continue
            if held is not None and rect_overlap(b, held) >= (1.0 - WHOLE_TOL) * area:
                continue
            out.append(b)
        return out

    def _furniture(r, loose) -> bool:
        """Is this shape a part of a block rather than a picture of its own?"""
        area = rect_area(r)
        return area > 0.0 and any(
            rect_overlap(r, b) >= GRAPHIC_HELD * area for b in loose
        )

    loose = _loose(content)

    taken: Optional[Tuple[float, float, float, float]] = None
    for fig in figures:
        r = fig.rect
        area = rect_area(r)
        if area <= 0.0 or rect_overlap(r, band) < GRAPHIC_REACH * area:
            continue
        if holds_other_marker(r, marker, all_markers):
            continue
        # A picture set below a clear gap is the next block's, even when the
        # band has not ended: the page separates them with white space exactly
        # so a reader does not join them.
        if content and content[1] - r[3] > GRAPHIC_DROP:
            continue
        if _furniture(r, loose):
            continue
        taken = rect_union(taken, r)

    reach = rect_union(content, taken)
    if reach is None:
        return taken

    # The drawn shapes this band could offer. A shape larger than the band is
    # the page's furniture, not this activity's diagram, and one too small to
    # see is a hairline.
    art: List[Tuple[float, float, float, float]] = []
    for d in drawings:
        r = d.rect
        area = rect_area(r)
        if area <= 0.0 or area > GRAPHIC_SHARE * band_area:
            continue
        if max(r[2] - r[0], r[3] - r[1]) < MIN_HOTSPOT:
            continue
        if rect_overlap(r, band) < GRAPHIC_REACH * area:
            continue
        if holds_other_marker(r, marker, all_markers):
            continue
        if spans_other_activity(r, marker, all_markers):
            continue
        if _furniture(r, loose):
            continue
        art.append(r)

    # Follow the diagram outwards, one ring of shapes at a time.
    remaining = art
    for _ in range(GRAPHIC_PASSES):
        near, far = [], []
        for r in remaining:
            gap_x = max(reach[0] - r[2], r[0] - reach[2])
            gap_y = max(reach[1] - r[3], r[1] - reach[3])
            (near if gap_x <= GRAPHIC_LINK and gap_y <= GRAPHIC_LINK else far).append(r)
        if not near:
            break
        for r in near:
            reach = rect_union(reach, r)
            taken = rect_union(taken, r)
        remaining = far

    if taken is None:
        return None

    # A diagram's own labels -- the axis words under a scale, the "%100" at the
    # end of an arrow -- are set smaller than the body, which is exactly what
    # `is_aside` drops from the flow so that captions and credits stay out of
    # an activity. Small type standing on or against the drawn matter is not a
    # caption, though: it is the diagram saying what its parts are, and leaving
    # it out clips the picture at its own labels.
    loose = _loose(rect_union(content, taken))
    for line in text_lines:
        box = (line["x0"], line["y0"], line["x1"], line["y1"])
        if rect_overlap(box, band) <= 0.0:
            continue
        if _furniture(box, loose):
            continue
        adjacent = (
            box[0] <= taken[2] + GRAPHIC_LABEL and box[2] >= taken[0] - GRAPHIC_LABEL and
            box[1] <= taken[3] + GRAPHIC_LABEL and box[3] >= taken[1] - GRAPHIC_LABEL
        )
        if not adjacent:
            continue
        inside = rect_overlap(box, taken) >= 0.5 * rect_area(box)
        # Judged against the same size `flow_content` judged it against, so the
        # two rules agree on what small print is: whatever the flow set aside
        # here is what this pass is for.
        prose = [sp for sp in line["spans"] if is_prose(sp)]
        small = bool(prose) and all(
            sp.size * HEADING_RATIO < band_font_size for sp in prose
        )
        if inside or small:
            taken = rect_union(taken, box)

    return (
        max(taken[0], band_x0),
        max(taken[1], band_bottom),
        min(taken[2], band_x1),
        min(taken[3], band_top),
    )


# Flow growth & headline extraction

def is_aside(line: TextLine, body_font_size: float) -> bool:
    """
    Check if line is an aside (rotated tab, small print caption/credit).

    `body_font_size` is the reference the line is small *relative to*. Callers
    inside an activity pass that activity's own type size rather than the
    page's, because a whole exercise set in 8 pt beside a 10 pt page is not a
    page of captions.
    """
    prose = [s for s in line.spans if is_prose(s)]
    if not prose:
        return False
    # Check for rotated vertical text
    for s in prose:
        txt = s.text.strip()
        if len(txt) > 1 and (s.bbox[3] - s.bbox[1]) > (s.bbox[2] - s.bbox[0]) * 1.5:
            return True
    # Small print: all prose spans smaller than body font
    if all(s.size * HEADING_RATIO < body_font_size for s in prose):
        return True
    return False


def flow_content(
    marker: DetectedMarker,
    band_items: List[TextSpan],
    col_x0: float,
    col_x1: float,
    body_font_size: float,
    start_y: Optional[float] = None,
) -> Tuple[Optional[Tuple[float, float, float, float]], List[TextSpan]]:
    """
    Absorb text lines following the marker in reading order according to measure and leading.

    `start_y` is the height the flow starts reading from. It defaults to the
    marker, which is where an activity begins in its own column. A band in a
    *later* column is a continuation: its text sits at the top of that column,
    far above the marker that owns it, and reading it from the marker's own
    height would discard every line of it. Such a band passes the top of its own
    column instead.
    """
    lines = group_lines(band_items)
    if not lines:
        return None, []

    if start_y is None:
        start_y = marker.span.bbox[3] + 2.0

    # Only consider lines at or below where the flow starts reading
    valid_lines = [l for l in lines if l.y <= start_y]
    if not valid_lines:
        return None, []

    opener = valid_lines[0]
    left = min(col_x0, opener.x0) - COLUMN_EDGE
    right = max(col_x1, opener.x1) + COLUMN_EDGE
    measure = [l for l in valid_lines if l.x0 >= left and l.x1 <= right]
    if not measure:
        return None, []

    # The type scale this activity is read against. The page-wide figure is a
    # statistic over the whole sheet and collapses wherever the sheet's ink is
    # not its prose -- a full-page map's labels outweighed the body text on
    # sheet 29 of the history book and put the page size at 3.9 pt, so every
    # line of every activity read as a heading and no flow ever started. An
    # activity is measured against its own band, and never against a number
    # smaller than the type it is actually set in.
    band_size = max(
        local_font_size(band_items, body_font_size),
        max((s.size for s in opener.spans), default=body_font_size),
    )

    # Leading estimation
    gaps = [measure[i].y - measure[i + 1].y for i in range(len(measure) - 1) if measure[i].y - measure[i + 1].y > 1.0]
    leading = sorted(gaps)[len(gaps) // 2] if gaps else 1.3 * marker.span.size

    items: List[TextSpan] = []
    rect: Optional[Tuple[float, float, float, float]] = None
    if start_y >= marker.span.bbox[3] + 1.0:
        # A continuation: the gap that ends the flow is measured from the top of
        # the column it continues into, not from a marker in the previous one.
        baseline = min(start_y, valid_lines[0].y + FLOW_LEADING * band_size)
    else:
        baseline = (marker.span.bbox[1] + marker.span.bbox[3]) / 2.0

    for line in measure:
        # The opener is the activity's own first line. Whatever size it is set
        # in, it cannot be another block's heading, and it cannot be an aside of
        # itself -- judging it against the page turned a large-type question
        # into no region at all.
        first = line is opener
        if not first and is_aside(line, band_size):
            continue
        gap = baseline - line.y
        # Stop at large gap or headings
        if gap > FLOW_BREAK:
            break
        max_size = max(s.size for s in line.spans)
        if not first and max_size >= band_size * HEADING_RATIO:
            break

        rect = rect_union(rect, (line.x0, line.y0, line.x1, line.y1))
        items.extend(line.spans)
        baseline = line.y

    return rect, items


def build_headline(marker: DetectedMarker, activity_spans: List[TextSpan]) -> str:
    """
    Extract first line of instruction on the marker baseline and following lines.
    """
    baseline = (marker.span.bbox[1] + marker.span.bbox[3]) / 2.0
    on_baseline = [
        s for s in activity_spans
        if abs((s.bbox[1] + s.bbox[3]) / 2.0 - baseline) < 2.5 and s.bbox[0] >= marker.span.bbox[2] - 1.0
    ]
    on_baseline.sort(key=lambda s: s.bbox[0])

    first_line = []
    cursor = marker.span.bbox[2]
    for s in on_baseline:
        if s.bbox[0] - cursor > 40.0:
            break
        first_line.append(s.text.strip())
        cursor = s.bbox[2]

    if not first_line:
        near = [
            s for s in activity_spans
            if baseline - 30.0 < (s.bbox[1] + s.bbox[3]) / 2.0 <= baseline + 1.0
        ]
        near.sort(key=lambda s: (-s.bbox[1], s.bbox[0]))
        return " ".join(s.text.strip() for s in near[:3])[:140].strip()

    col_left = on_baseline[0].bbox[0] if on_baseline else marker.span.bbox[2]
    following = [
        s for s in activity_spans
        if (s.bbox[1] + s.bbox[3]) / 2.0 < baseline - 2.0 and abs(s.bbox[0] - col_left) < 30.0
    ]
    following.sort(key=lambda s: -s.bbox[1])

    parts = [" ".join(first_line)]
    prev_y = baseline
    for s in following:
        y_mid = (s.bbox[1] + s.bbox[3]) / 2.0
        if prev_y - y_mid > 20.0:
            break
        if re.match(r"^\d{1,2}[.)]?$", s.text.strip()):
            break
        if re.search(r"[.!?:]$", parts[-1]):
            break
        parts.append(s.text.strip())
        prev_y = y_mid
        if len(" ".join(parts)) > 160:
            break

    raw = " ".join(parts)
    return re.sub(r"\s+", " ", raw).strip()[:160]


# De-overlapping

@dataclass
class PartRecord:
    owner_id: str
    column: int
    rect: List[float]  # [x0, y0, x1, y1] mutable
    anchor: Optional[Tuple[float, float, float, float]] = None
    panel: Optional[Tuple[float, float, float, float]] = None


def separate_rects(
    parts: List[PartRecord],
    passes: int = 6,
    geom: Optional[PageGeometry] = None,
) -> List[PartRecord]:
    """
    Push apart overlapping hotspot rectangles along the axis of least penetration.

    Preserves panel boundaries and anchor baselines, maintaining HOTSPOT_GAP
    separation. Given `geom`, the two are pushed apart at the nearest seam the
    page actually has rather than at the midpoint of the contested span -- a
    midpoint falls inside a line of type as often as between two, which is how
    a region came to end halfway through its own last option.
    """
    for _ in range(passes):
        moved = False
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                a = parts[i].rect
                b = parts[j].rect
                t_a = (a[0], a[1], a[2], a[3])
                t_b = (b[0], b[1], b[2], b[3])
                if not rect_intersects(t_a, t_b):
                    continue

                same_owner_same_col = (
                    parts[i].owner_id == parts[j].owner_id and
                    parts[i].column == parts[j].column
                )
                upper_is_a = (a[1] + a[3]) >= (b[1] + b[3])
                upper = a if upper_is_a else b
                lower = b if upper_is_a else a
                upper_part = parts[i] if upper_is_a else parts[j]
                lower_part = parts[j] if upper_is_a else parts[i]

                if same_owner_same_col:
                    if lower_part.panel:
                        upper[1] = max(upper[1], lower_part.panel[3])
                    elif upper_part.panel:
                        lower[3] = min(lower[3], upper_part.panel[1])
                    else:
                        mid = (max(a[1], b[1]) + min(a[3], b[3])) / 2.0
                        upper[1] = max(upper[1], mid)
                        lower[3] = min(lower[3], mid)
                    moved = True
                    continue

                dx = min(a[2], b[2]) - max(a[0], b[0])
                dy = min(a[3], b[3]) - max(a[1], b[1])

                anchor_a = parts[i].anchor
                anchor_b = parts[j].anchor
                upper_anchor = anchor_a if upper_is_a else anchor_b
                lower_anchor = anchor_b if upper_is_a else anchor_a
                upper_panel = upper_part.panel
                lower_panel = lower_part.panel

                if dy <= dx:
                    mid = (max(a[1], b[1]) + min(a[3], b[3])) / 2.0
                    seam = nearest_plane(
                        legal_planes(
                            geom,
                            max(a[0], b[0]), min(a[2], b[2]),
                            min(a[3], b[3]), max(a[1], b[1]),
                        ),
                        mid,
                    )
                    if seam is not None:
                        mid = seam
                    u_min = mid + HOTSPOT_GAP / 2.0
                    l_max = mid - HOTSPOT_GAP / 2.0
                    if lower_panel:
                        u_min = max(u_min, lower_panel[3] + HOTSPOT_GAP / 2.0)
                    if upper_panel:
                        l_max = min(l_max, upper_panel[1] - HOTSPOT_GAP / 2.0)
                    upper[1] = max(upper[1], u_min)
                    lower[3] = min(lower[3], l_max)
                else:
                    mid = (max(a[0], b[0]) + min(a[2], b[2])) / 2.0
                    seam = nearest_plane(
                        legal_planes_x(
                            geom,
                            max(a[1], b[1]), min(a[3], b[3]),
                            max(a[0], b[0]), min(a[2], b[2]),
                        ),
                        mid,
                    )
                    if seam is not None:
                        mid = seam
                    right_is_a = (a[0] + a[2]) >= (b[0] + b[2])
                    right = a if right_is_a else b
                    left = b if right_is_a else a
                    right[0] = max(right[0], mid + HOTSPOT_GAP / 2.0)
                    left[2] = min(left[2], mid - HOTSPOT_GAP / 2.0)

                moved = True

        if not moved:
            break

    return [
        p for p in parts
        if p.rect[2] - p.rect[0] >= MIN_HOTSPOT and p.rect[3] - p.rect[1] >= MIN_HOTSPOT
    ]


def close_blocks(
    parts: List[PartRecord],
    geom: Optional[PageGeometry],
    markers: List[DetectedMarker],
    owners: Dict[str, DetectedMarker],
    passes: int = 6,
) -> List[PartRecord]:
    """
    Take every block a hotspot half-covers, or give it up entirely.

    Growth decides what a region contains by a series of inclusion rules, each
    of which may decline, and the separation passes then trim the result. None
    of that guarantees the thing the rules are for: that a hotspot's edge never
    falls inside a panel, a table or a ruled answer block. The scorecard has
    measured that invariant all along -- `cuts()` -- without anything enforcing
    it, so a sliced panel was something to be noticed after the fact rather than
    something the page could not produce.

    Here it is enforced. A block the part half-covers is taken whole when it
    belongs to that part's activity -- it stands in reach and carries no other
    activity's label -- and released whole otherwise, by pulling the edge back
    to the nearest seam clear of it. Taking may create a new overlap, so the
    pass runs to a fixed point, and release is the tie-break so a part caught
    between two readings settles instead of oscillating.
    """
    if geom is None or not geom.blocks:
        return parts

    sheet_height = max(1.0, geom.content_box[3] - geom.content_box[1])

    for _ in range(passes):
        moved = False

        # Who a block belongs to, when more than one hotspot reaches into it.
        # The part already holding most of it is the one the page put it with;
        # every other part gives it up. Without this arbitration two neighbours
        # both take the same panel, each swallowing the other, and the pair
        # comes out as one huge region and one sliver.
        claimant: Dict[int, Optional[str]] = {}
        for block in geom.blocks:
            best_share, best_owner = 0.0, None
            for part in parts:
                share = rect_overlap(
                    (part.rect[0], part.rect[1], part.rect[2], part.rect[3]),
                    block["rect"],
                )
                if share > best_share:
                    best_share, best_owner = share, id(part)
            claimant[id(block["rect"])] = best_owner

        for part in parts:
            marker = owners.get(part.owner_id)
            for block in geom.blocks:
                b = block["rect"]
                if b[0] < -2 or b[1] < -2 or b[2] > geom.content_box[2] + 50 or b[3] > geom.content_box[3] + 50:
                    continue
                r = (part.rect[0], part.rect[1], part.rect[2], part.rect[3])
                if not cuts(r, b):
                    continue

                grown = rect_union(r, b)
                # Check for conflicts with other claimed blocks to prevent oscillation
                conflicts = False
                for other_blk in geom.blocks:
                    other_b = other_blk["rect"]
                    if other_b is b or id(other_b) == id(b):
                        continue
                    if cuts(grown, other_b):
                        other_claimant = claimant.get(id(other_b))
                        if other_claimant is not None and other_claimant != id(part):
                            conflicts = True
                            break

                takeable = (
                    claimant.get(id(b)) == id(part)
                    and not holds_other_marker(b, marker, markers)
                    and not spans_other_activity(b, marker, markers)
                    and not spans_other_activity(grown, marker, markers)
                    and not holds_other_marker(grown, marker, markers)
                    and not conflicts
                    # A tinted ground the question merely stands on is not the
                    # question's: `panel_for` refuses to snap out to those, and
                    # swallowing one here would undo that.
                    and (rect_area(b) <= 2.0 * rect_area(r) if block.get("kind") == "panel" else True)
                    # Taking is cumulative -- a part that swallows one block is
                    # large enough to swallow the next -- so it needs a ceiling
                    # or a single question walks down the sheet. Past most of
                    # the page a hotspot has stopped pointing at anything, and
                    # the cut it leaves behind is the smaller fault.
                    and (grown[3] - grown[1]) <= (TALL_REGION - 0.005) * sheet_height
                )

                if takeable:
                    part.rect = list(grown)
                    # Record it as a frame. `separate_rects` already refuses to
                    # push a neighbour through a panel boundary, and a block
                    # this part has just taken whole is exactly that: letting
                    # separation trim the edge back afterwards would put it
                    # inside the block again and undo the closure.
                    part.panel = rect_union(part.panel, b)
                    moved = True
                    continue

                # Retreating is for a hotspot that has merely brushed a block,
                # not for one the block sits inside. A part holding most of a
                # block it may not take -- shared furniture, a box standing
                # against three questions at once -- has the block as its own
                # content; pulling back off it would throw away the question
                # rather than tidy its edge, and on the maths workbook that cost
                # regions ninety per cent of their area to buy a cleaner count.
                # The edge is left where it is and the cut is declared.
                if rect_overlap(r, b) >= RETREAT_SHARE * rect_area(b):
                    continue

                # Release: pull whichever edge trespasses back clear of the
                # block, choosing the retreat that gives up the least. A block
                # can intrude from any side -- a bubble reaching into the right
                # of a column, a tinted card overlapping a foot -- and handling
                # only the vertical ones left the horizontal cuts standing.
                retreats = []
                if r[1] < b[3] < r[3]:
                    seam = nearest_plane(
                        legal_planes(geom, r[0], r[2], r[3], b[3]), b[3]
                    )
                    y = seam if seam is not None else b[3]
                    retreats.append((y - r[1], 1, y))
                if r[1] < b[1] < r[3]:
                    seam = nearest_plane(
                        legal_planes(geom, r[0], r[2], b[1], r[1]), b[1]
                    )
                    y = seam if seam is not None else b[1]
                    retreats.append((r[3] - y, 3, y))
                if r[0] < b[2] < r[2]:
                    retreats.append((b[2] - r[0], 0, b[2]))
                if r[0] < b[0] < r[2]:
                    retreats.append((r[2] - b[0], 2, b[0]))

                if not retreats:
                    continue
                # Retreating is only worth it if a hotspot survives it. A block
                # that cannot be taken and cannot be left without wiping out the
                # region keeps its cut: losing the activity entirely is worse
                # than an edge in the wrong place, and it is the case the
                # scorecard already counts.
                retreats.sort(key=lambda t: t[0])
                for _, edge, value in retreats:
                    trial = list(part.rect)
                    if edge in (0, 1):
                        trial[edge] = max(trial[edge], value)
                    else:
                        trial[edge] = min(trial[edge], value)
                    if (trial[2] - trial[0] >= MIN_HOTSPOT
                            and trial[3] - trial[1] >= MIN_HOTSPOT
                            and rect_area(tuple(trial)) >= RETREAT_COST * rect_area(r)):
                        part.rect = trial
                        moved = True
                        break

        if not moved:
            break

    return parts


# Sub-item extraction

def build_sub_items(
    marker: DetectedMarker,
    parent_rect: Tuple[float, float, float, float],
    page_num: int,
    body_spans: List[TextSpan],
    solution_blocks: List[dict],
    parts: Optional[List[Tuple[float, float, float, float]]] = None,
) -> List[SubItemRect]:
    """
    Build SubItemRect objects for detected sub-questions.

    Each question is measured inside the piece of the activity it actually
    stands in. An activity that runs into a second column is two rectangles far
    apart on the sheet, and reading a question's neighbours off the union of
    them takes in the width of the page: the lines of the other column fall in
    the same horizontal band, and the text comes out interleaved -- a question
    from one column and the answer space of another, a word apart. Worse, a
    question below the piece it was measured against clamps to a rectangle whose
    foot is above its head, and an inverted rectangle covers nothing at all, so
    the question it was drawn for cannot be clicked.
    """
    if not marker.items:
        return []

    hosts = list(parts) if parts else [parent_rect]

    def host_of(span: TextSpan) -> Optional[Tuple[float, float, float, float]]:
        """The piece of the activity a question stands in, smallest first."""
        cx = (span.bbox[0] + span.bbox[2]) / 2.0
        cy = (span.bbox[1] + span.bbox[3]) / 2.0
        found = [h for h in hosts if contains_point(h, cx, cy)]
        if not found:
            return None
        return min(found, key=rect_area)

    # A question belongs to one piece, and the piece it belongs to is what the
    # ones after it are measured down to -- so group first, then walk each
    # group in reading order.
    grouped: Dict[int, List[SubQuestion]] = {}
    for sub in marker.items:
        host = host_of(sub.span)
        if host is None:
            continue
        grouped.setdefault(hosts.index(host), []).append(sub)

    out: List[SubItemRect] = []
    for host_index, subs in grouped.items():
        host = hosts[host_index]
        subs = sorted(subs, key=lambda it: -it.span.bbox[1])

        for j, sub in enumerate(subs):
            top = sub.span.bbox[3] + 2.0
            bottom = subs[j + 1].span.bbox[3] + 2.0 if j + 1 < len(subs) else host[1]
            if top <= bottom:
                continue

            sub_spans = [
                s for s in body_spans
                if bottom <= (s.bbox[1] + s.bbox[3]) / 2.0 <= top and
                s.bbox[0] >= host[0] - 2.0 and s.bbox[2] <= host[2] + 2.0
            ]

            rect = sub.span.bbox
            text_words = []
            for s in sorted(sub_spans, key=lambda s: (-s.bbox[1], s.bbox[0])):
                rect = rect_union(rect, s.bbox)
                text_words.append(s.text.strip())

            # Absorb adjacent solution block (e.g. empty table cells or ruled
            # answer lines) -- the space the answer is written in belongs to the
            # question that asks for it.
            if solution_blocks:
                for sb in solution_blocks:
                    s_rect = sb["rect"]
                    ov_y = min(s_rect[3], top) - max(s_rect[1], bottom)
                    if ov_y < 0.5 * (s_rect[3] - s_rect[1]):
                        continue
                    ov_x = min(s_rect[2], host[2] + 4.0) - max(s_rect[0], host[0] - 4.0)
                    if ov_x < 0.5 * (s_rect[2] - s_rect[0]):
                        continue
                    rect = rect_union(rect, s_rect)

            padded_rect = (
                round(max(rect[0] - 4.0, host[0]), 4),
                round(max(rect[1] - 4.0, host[1]), 4),
                round(min(rect[2] + 4.0, host[2]), 4),
                round(min(rect[3] + 4.0, host[3]), 4),
            )
            # Clamping to the host can only shrink a rectangle, and a question
            # whose box survives that with no width or no height left is not a
            # hotspot. Dropping it leaves the activity around it clickable,
            # which an empty rectangle laid over the question would not.
            if (
                padded_rect[2] - padded_rect[0] < MIN_HOTSPOT or
                padded_rect[3] - padded_rect[1] < MIN_HOTSPOT
            ):
                continue

            sub_id = f"p{page_num}-{marker.label.strip('.:) ')}-{sub.label}"
            out.append(
                SubItemRect(
                    id=sub_id,
                    label=sub.label,
                    number=sub.number,
                    part_index=host_index,
                    rect=padded_rect,
                    text=" ".join(text_words)[:200],
                )
            )

    out.sort(key=lambda it: (it.part_index, it.number if it.number is not None else 0))
    return out


def _line_text(line: dict) -> str:
    """The text of a `_text_lines` row, left to right. Images carry none."""
    spans = line.get("spans") or []
    return " ".join(s.text for s in sorted(spans, key=lambda s: s.bbox[0])).strip()


def _lines_within(
    lines: List[dict],
    x0: float,
    x1: float,
    top: float,
    bottom: float,
) -> List[dict]:
    """The rows whose middle falls inside a cell, top first."""
    got = [
        l for l in lines
        if bottom - 1.0 <= (l["y0"] + l["y1"]) / 2.0 <= top + 1.0 and
        x0 - COLUMN_EDGE <= (l["x0"] + l["x1"]) / 2.0 <= x1 + COLUMN_EDGE
    ]
    got.sort(key=lambda l: -l["y1"])
    return got


def reads_on(
    marker: DetectedMarker,
    source_band: dict,
    target_cell: dict,
    lines: List[dict],
    panels: List[Tuple[float, float, float, float]],
    body_font_size: float,
) -> bool:
    """
    Whether an activity's text runs on into the next column, or merely stops.

    A column with no marker of its own is ambiguous: it is either the second
    half of the activity that ends the previous column, or a block that has
    nothing to do with it -- a self-assessment panel, a glossary, a reading box
    set beside the exercises rather than after them. Handing it to whichever
    activity happened to come last is right in the first case and, in the
    second, grows that activity across the whole sheet.

    So the continuation has to be visible in the type. The activity must run out
    of column rather than finish in it, and the column it is said to continue
    into must open the way a continuation opens -- at the top, in body type, not
    behind a heading and not inside a panel the activity itself stands outside
    of.
    """
    source = _lines_within(
        lines, source_band["x0"], source_band["x1"], source_band["top"], source_band["bottom"]
    )
    if not source:
        return False

    # It has to reach the foot of its own column. Prose that stops short of it
    # had all the room it needed and wanted no more.
    last = source[-1]
    if last["y0"] > source_band["bottom"] + FLOW_LEADING * body_font_size:
        return False

    # And it has to break off mid-sentence. A full stop at the foot of a column
    # is an activity that ended there.
    if re.search(r"[.!?:;\u2026]$", _line_text(last)):
        return False

    target = _lines_within(
        lines, target_cell["x0"], target_cell["x1"], target_cell["top"], target_cell["bottom"]
    )
    if not target:
        return False

    first = target[0]
    # A continuation resumes at the top of the column it spills into.
    if first["y1"] < target_cell["top"] - FLOW_LEADING * body_font_size:
        return False

    # A heading opens a new block, never the second half of a sentence.
    spans = first.get("spans") or []
    if spans and max(s.size for s in spans) >= body_font_size * HEADING_RATIO:
        return False

    # Nor does a sentence resume inside a drawn panel that its own first half
    # stands outside of.
    for panel in panels:
        if encloses(panel, (first["x0"], first["y0"], first["x1"], first["y1"]), tol=2.0):
            if not encloses(panel, marker.span.bbox, tol=2.0):
                return False

    return True


# Primary region growth orchestrator

def grow_activity_regions(
    primitives: PagePrimitives,
    layout: PageLayout,
    markers: List[DetectedMarker],
    trace: Optional["GrowthTrace"] = None,
) -> List[ActivityRegion]:
    """
    Grow all detected activity markers on a page into complete ActivityRegion objects.

    Args:
        primitives: Raw page primitives (text, drawings, images).
        layout: Detected page layout (columns, content box, body size).
        markers: Detected activity markers from Phase 2.
        trace: When given, filled with how this sheet was read -- the bands and
            the drawn geometry. `reconcile_anchors` needs both: it binds
            publisher icons against the bands, and it separates the regions it
            synthesises against the geometry.

    Returns:
        List of ActivityRegion instances with resolved bounding boxes, sub-items, and headlines.
    """
    if not markers:
        if trace is not None:
            trace.drop("growth", "no-marker", text="")
        return []

    page_num = primitives.page_num
    page_w = primitives.width
    page_h = primitives.height
    content_box = layout.content_box

    # 1. Filter body spans within content box
    body_spans = [
        s for s in primitives.spans
        if s.bbox[1] >= content_box[1] - 2.0 and s.bbox[3] <= content_box[3] + 2.0
    ]

    # 2. Detect vector panels and solution spaces
    panels = detect_panels(primitives.drawings, body_spans, page_h=page_h, markers=markers)
    solution_blocks = detect_solution_spaces(primitives.drawings, body_spans, page_h=page_h)

    # 3. Segment page into horizontal strips and cells
    blocks = [{"x0": s.bbox[0], "x1": s.bbox[2], "y0": s.bbox[1], "y1": s.bbox[3]} for s in body_spans]
    lines = _text_lines(body_spans)
    # Kept before the image rects join `lines` below: captions and diagram
    # labels are text, and both readers of this list want only text.
    text_lines = list(lines)

    # What the page means as single pictures. A figure is a block like a panel
    # or a ruled answer grid: `close_blocks` will make every hotspot take one
    # whole or stay clear of it, which is what stops an edge falling across a
    # diagram. Without this the picture primitives were only ever segmentation
    # hints, and nothing held the tiles of one figure together. Read before the
    # image rects join `lines` below, because captions are text.
    figures = detect_figures(
        primitives.images, primitives.drawings, text_lines, content_box,
        marker_rects=[m.span.bbox for m in markers],
        gutters=[
            (a.x1 + b.x0) / 2.0
            for a, b in zip(sorted(layout.columns, key=lambda c: c.x0),
                            sorted(layout.columns, key=lambda c: c.x0)[1:])
            if b.x0 > a.x1
        ],
    )

    for im in primitives.images:
        if im.bbox[1] >= content_box[1] - 2.0 and im.bbox[3] <= content_box[3] + 2.0:
            ib = {"x0": im.bbox[0], "x1": im.bbox[2], "y0": im.bbox[1], "y1": im.bbox[3]}
            blocks.append(ib)
            lines.append(ib)
    geom = PageGeometry(
        lines=lines,
        blocks=(
            [{"rect": pn, "kind": "panel"} for pn in panels] +
            [dict(b) for b in solution_blocks] +
            [{"rect": f.rect, "kind": "figure"} for f in figures]
        ),
        content_box=content_box,
    )
    if trace is not None:
        trace.geometry = geom

    strips = segment_strips(blocks, lines, layout.columns, content_box[3], content_box[1], markers)
    cells = build_page_cells(strips, layout.columns, markers, content_box[3], content_box[1])

    # 4. Construct vertical bands per activity
    # Map markers to cells and activities
    act_bands: Dict[int, List[dict]] = {id(m): [] for m in markers}
    by_marker_id = {id(m): m for m in markers}

    running_act = None
    running_block = None
    running_band = None

    for cell in cells:
        if cell["dead"]:
            continue
        if cell["block_index"] != running_block:
            running_act = None
            running_band = None
            running_block = cell["block_index"]

        cell_markers = cell["markers"]
        if not cell_markers:
            if (
                running_act and
                cell["column"] > running_act.column_index and
                running_band is not None and
                reads_on(
                    marker=running_act,
                    source_band=running_band,
                    target_cell=cell,
                    lines=lines,
                    panels=panels,
                    body_font_size=layout.body_font_size,
                )
            ):
                act_bands[id(running_act)].append({
                    "x0": cell["x0"], "x1": cell["x1"], "column": cell["column"],
                    "top": cell["top"], "bottom": cell["bottom"], "own": False, "items": [],
                })
            continue

        first_top = cell_markers[0].span.bbox[3] + 2.0
        if running_act and cell["column"] > running_act.column_index and cell["top"] - first_top > 1.0:
            # The head of the next column, above its own first marker. It is a
            # continuation only on the same evidence a marker-less column is:
            # the head of a column is just as often a block of its own.
            head = {
                "x0": cell["x0"], "x1": cell["x1"], "column": cell["column"],
                "top": cell["top"], "bottom": first_top, "own": False, "items": [],
            }
            if running_band is not None and reads_on(
                marker=running_act,
                source_band=running_band,
                target_cell=head,
                lines=lines,
                panels=panels,
                body_font_size=layout.body_font_size,
            ):
                act_bands[id(running_act)].append(head)

        for i, m in enumerate(cell_markers):
            bottom = cell_markers[i + 1].span.bbox[3] + 2.0 if i + 1 < len(cell_markers) else cell["bottom"]
            own_band = {
                "x0": cell["x0"], "x1": cell["x1"], "column": cell["column"],
                "top": m.span.bbox[3] + 2.0, "bottom": bottom, "own": True, "items": [],
            }
            act_bands[id(m)].append(own_band)
            running_act = m
            running_band = own_band
            running_block = cell["block_index"]

    # Assign body spans to bands
    for s in body_spans:
        s_cx = (s.bbox[0] + s.bbox[2]) / 2.0
        s_cy = (s.bbox[1] + s.bbox[3]) / 2.0
        best_band = None
        best_score = 0.0
        for m_id, bands in act_bands.items():
            for b in bands:
                ov_y = max(0.0, min(s.bbox[3], b["top"]) - max(s.bbox[1], b["bottom"]))
                ov_x = max(0.0, min(s.bbox[2], b["x1"]) - max(s.bbox[0], b["x0"]))
                score = ov_y * ov_x
                if score > best_score:
                    best_score = score
                    best_band = b
        if best_band:
            best_band["items"].append(s)

    # 5. Grow regions for each activity
    part_records: List[PartRecord] = []
    act_spans: Dict[int, List[TextSpan]] = {id(m): [] for m in markers}

    for m in markers:
        m_id = id(m)
        bands = act_bands[m_id]
        if not bands:
            # The marker stands in a cell growth read as dead, or in none at
            # all. It is a marker with no scope, so it can never hold content.
            if trace is not None:
                trace.drop("growth", "no-band", rect=m.span.bbox, text=m.span.text,
                           label=m.label, column=m.column_index)
            continue

        own_band = bands[0]
        own_col = next((c for c in layout.columns if c.index == m.column_index), layout.columns[0])

        for band in bands:
            flow_rect, flow_items = flow_content(
                marker=m,
                band_items=band["items"],
                col_x0=band["x0"],
                col_x1=band["x1"],
                body_font_size=layout.body_font_size,
                start_y=None if band["own"] else band["top"],
            )

            # A continuation band earns a hotspot only by holding text that
            # reads on from the activity. Falling back to the marker's own box
            # here -- the marker stands in the *previous* column -- seeded the
            # snapping below with a rectangle from the wrong column, and panels
            # and ruled lines then grew it across the page.
            if not band["own"] and not flow_rect:
                if trace is not None:
                    trace.drop("growth", "continuation-no-flow",
                               rect=(band["x0"], band["bottom"], band["x1"], band["top"]),
                               text=m.span.text, label=m.label, column=band["column"],
                               band_items=len(band["items"]))
                continue

            act_spans[m_id].extend(flow_items)

            content = rect_union(flow_rect, m.span.bbox) if band["own"] else flow_rect
            if not content:
                content = m.span.bbox

            # Snap to panel if applicable
            panel = panel_for(
                marker=m,
                band_top=band["top"],
                band_bottom=band["bottom"],
                content=content,
                panels=panels,
                all_markers=markers,
                flow_spans=flow_items,
                col_x0=band["x0"],
                col_x1=band["x1"],
            )
            bare = content
            content = rect_union(content, panel)

            # Snap to solution space
            col_target = own_col if band is own_band else next((c for c in layout.columns if c.index == band["column"]), own_col)
            solution = solution_for(
                marker=m,
                band_top=band["top"],
                band_bottom=band["bottom"],
                column_x0=col_target.x0,
                column_x1=col_target.x1,
                content=content,
                solution_blocks=solution_blocks,
                body_spans=body_spans,
                all_markers=markers,
                activity_spans=act_spans[m_id],
            )
            bare = rect_union(bare, solution)
            content = rect_union(content, solution)

            # Snap to the pictures the band holds
            graphic = graphics_for(
                marker=m,
                band_top=band["top"],
                band_bottom=band["bottom"],
                band_x0=band["x0"],
                band_x1=band["x1"],
                content=content,
                figures=figures,
                drawings=primitives.drawings,
                all_markers=markers,
                text_lines=text_lines,
                band_font_size=max(
                    local_font_size(band["items"], layout.body_font_size),
                    m.span.size,
                ),
                blocks=[p for p in panels] + [b["rect"] for b in solution_blocks],
            )
            bare = rect_union(bare, graphic)
            content = rect_union(content, graphic)

            # Padding & boundary limits
            top_limit = max(band["top"], panel[3]) if (band["own"] and panel) else band["top"]
            rect = [
                clamp(content[0] - PAD, 0.0, page_w),
                clamp(content[1] - PAD, content_box[1] - PAD, page_h),
                clamp(content[2] + PAD, 0.0, page_w),
                clamp(content[3] + PAD, 0.0, content_box[3] + PAD),
            ]

            # If panel was snapped, bare text padding does not exceed panel frame
            if panel:
                rect[0] = max(rect[0], bare[0] - PAD) if bare[0] < panel[0] else min(rect[0], panel[0])
                rect[2] = min(rect[2], bare[2] + PAD) if bare[2] > panel[2] else max(rect[2], panel[2])
                rect[1] = max(rect[1], bare[1] - PAD if bare[1] < panel[1] else panel[1])
                rect[3] = max(rect[3], bare[3] + PAD if bare[3] > panel[3] else panel[3])

            # Hotspot height must not exceed TALL_REGION * page_h
            max_act_h = (TALL_REGION - 0.005) * page_h
            if rect[3] - rect[1] > max_act_h:
                rect[1] = max(rect[1], rect[3] - max_act_h)

            # What an activity picks up in a later column stays in that column.
            # The activity's own band may spread -- a full-width strip absorbs
            # the gutter and the cell widens with it -- but a continuation has
            # no such claim on the column it came from.
            if not band["own"]:
                rect[0] = max(rect[0], band["x0"] - PAD)
                rect[2] = min(rect[2], band["x1"] + PAD)

            if rect[2] - rect[0] < MIN_HOTSPOT or rect[3] - rect[1] < MIN_HOTSPOT:
                if trace is not None:
                    trace.drop("growth", "sliver", rect=rect, text=m.span.text,
                               label=m.label, w=rect[2] - rect[0], h=rect[3] - rect[1],
                               min_hotspot=MIN_HOTSPOT, own=band["own"])
                continue

            part_records.append(
                PartRecord(
                    owner_id=str(m_id),
                    column=band["column"],
                    rect=rect,
                    anchor=m.span.bbox if band["own"] else None,
                    panel=panel,
                )
            )

    # 6. De-overlap hotspots
    # Closure first, separation second. A block a part takes whole is recorded
    # on that part as a frame, and separation already refuses to push a
    # neighbour through a frame, so the order settles both rules at once:
    # nothing ends up inside a block, and nothing ends up on top of anything.
    # Running closure again *after* separation instead grew parts that had just
    # been pushed apart, and put them back on top of each other.
    owners = {str(id(m)): m for m in markers}
    part_records = close_blocks(part_records, geom, markers, owners)
    separated = separate_rects(part_records, geom=geom)
    separated = close_blocks(separated, geom, markers, owners)

    # 7. Assemble final ActivityRegion objects
    regions_by_owner: Dict[str, List[Tuple[float, float, float, float]]] = {}
    for p in separated:
        if p.owner_id not in regions_by_owner:
            regions_by_owner[p.owner_id] = []
        regions_by_owner[p.owner_id].append((
            round(p.rect[0], 4),
            round(p.rect[1], 4),
            round(p.rect[2], 4),
            round(p.rect[3], 4),
        ))

    activities: List[ActivityRegion] = []
    for m in markers:
        m_id = str(id(m))
        if m_id not in regions_by_owner or not regions_by_owner[m_id]:
            # Every part this marker grew was refused before separation, or was
            # pushed below a hotspot's minimum by it. Either way the marker is
            # real and the page shows nothing for it, which is the refusal most
            # worth reading.
            if trace is not None:
                trace.drop("growth", "no-part", rect=m.span.bbox, text=m.span.text,
                           label=m.label, column=m.column_index)
            continue
        parts = regions_by_owner[m_id]

        primary_rect = parts[0]
        headline = build_headline(m, act_spans.get(id(m), []))
        sub_items = build_sub_items(m, primary_rect, page_num, body_spans, solution_blocks, parts=parts)

        clean_label = m.label.strip(".:) ")
        # A question the page did not enumerate keeps no label -- see
        # `DetectedMarker.slug` -- but still needs a name of its own here.
        act_id = f"p{page_num}-{m.slug or clean_label}"

        if trace is not None:
            trace.bands[act_id] = list(act_bands.get(id(m), []))

        activities.append(
            ActivityRegion(
                id=act_id,
                label=clean_label or None,
                column=m.column_index,
                rect=primary_rect,
                parts=parts,
                headline=headline,
                items=sub_items if sub_items else None,
                anchored=False,
            )
        )

    return clean_page_activities(activities, geom=geom, trace=trace)


def _evict(
    geom: PageGeometry,
    a: List[float],
    b: List[float],
) -> bool:
    """
    Settle an overlap whose contested span is solid, without slicing anything.

    A span with no seam in it is one object -- a panel, a table, a single line
    of type -- running from end to end. Cutting it is out of the question, and
    leaving the two rectangles on top of each other steals clicks, so the
    rectangle holding less of that object steps off it entirely.

    Returns whether anything moved.
    """
    span = (
        max(a[0], b[0]), max(a[1], b[1]),
        min(a[2], b[2]), min(a[3], b[3]),
    )
    filling = [
        blk["rect"] for blk in geom.blocks
        if encloses(blk["rect"], span, tol=1.0)
    ]
    if not filling:
        return False
    block = max(filling, key=rect_area)

    loser = a if rect_overlap(tuple(a), block) <= rect_overlap(tuple(b), block) else b
    # Off the block by whichever way is shortest, and only if a hotspot is left.
    for cost, edge, value in sorted((
        (block[3] - loser[1], 1, block[3]),
        (loser[3] - block[1], 3, block[1]),
        (block[2] - loser[0], 0, block[2]),
        (loser[2] - block[0], 2, block[0]),
    )):
        trial = list(loser)
        trial[edge] = value
        if (trial[2] - trial[0] >= MIN_HOTSPOT
                and trial[3] - trial[1] >= MIN_HOTSPOT):
            loser[:] = trial
            return True
    return False


def snap_edges(
    activities: List[ActivityRegion],
    geom: PageGeometry,
    page_w: float = 595.0,
    page_h: float = 842.0,
    passes: int = 3,
) -> List[ActivityRegion]:
    """
    Final edge-snapping pass across all activities and drawn blocks.

    Ensures no hotspot edge comes to rest inside a drawn block (panel, solution
    grid/cell, or figure). For every block a region cuts, decides whether to
    expand and take the block whole, or retreat off it to the block boundary.
    """
    if not activities or not geom or not geom.blocks:
        return activities

    if geom.content_box:
        page_w = max(page_w, geom.content_box[2] + 40.0)
        page_h = max(page_h, geom.content_box[3] + 40.0)

    # Filter out invalid or off-sheet blocks
    valid_blocks = [
        blk for blk in geom.blocks
        if not (
            blk["rect"][0] < -2 or blk["rect"][1] < -2
            or blk["rect"][2] > page_w + 2 or blk["rect"][3] > page_h + 2
        )
        and rect_area(blk["rect"]) > 0.0
    ]
    if not valid_blocks:
        return activities

    for _ in range(passes):
        moved = False
        all_parts: List[Tuple[int, int, List[float]]] = []
        for a_idx, act in enumerate(activities):
            for p_idx, p in enumerate(act.parts):
                all_parts.append((a_idx, p_idx, list(p)))

        for a_idx, p_idx, p in all_parts:
            r = tuple(p)
            for blk in valid_blocks:
                b = blk["rect"]
                if not cuts(r, b):
                    continue

                share = rect_overlap(r, b) / rect_area(b)
                grown = rect_union(r, b)

                # 1. Decide if expand is possible and safe
                can_expand = (
                    share >= 0.35
                    and (grown[3] - grown[1]) <= (TALL_REGION - 0.005) * page_h
                    and (rect_area(b) <= 2.5 * rect_area(r) if blk.get("kind") == "panel" else True)
                )
                if can_expand:
                    # Expansion MUST NOT overlap another activity's parts
                    for o_a_idx, o_p_idx, o_p in all_parts:
                        if o_a_idx == a_idx:
                            continue
                        if rect_overlap(grown, tuple(o_p)) > 1.0:
                            can_expand = False
                            break

                if can_expand:
                    p[:] = list(grown)
                    r = tuple(p)
                    moved = True
                    continue

                # 2. Otherwise, attempt retreat off the block
                retreats = []
                if r[1] < b[3] < r[3]:
                    seam = nearest_plane(legal_planes(geom, r[0], r[2], r[3], b[3]), b[3])
                    y = seam if seam is not None else b[3]
                    retreats.append((y - r[1], 1, y))
                if r[1] < b[1] < r[3]:
                    seam = nearest_plane(legal_planes(geom, r[0], r[2], b[1], r[1]), b[1])
                    y = seam if seam is not None else b[1]
                    retreats.append((r[3] - y, 3, y))
                if r[0] < b[2] < r[2]:
                    retreats.append((b[2] - r[0], 0, b[2]))
                if r[0] < b[0] < r[2]:
                    retreats.append((r[2] - b[0], 2, b[0]))

                retreats.sort(key=lambda t: t[0])
                retreated = False
                for _, edge, value in retreats:
                    trial = list(p)
                    if edge in (0, 1):
                        trial[edge] = max(trial[edge], value)
                    else:
                        trial[edge] = min(trial[edge], value)
                    if (trial[2] - trial[0] >= MIN_HOTSPOT
                            and trial[3] - trial[1] >= MIN_HOTSPOT
                            and rect_area(tuple(trial)) >= RETREAT_COST * rect_area(r)):
                        p[:] = trial
                        r = tuple(p)
                        moved = True
                        retreated = True
                        break

                # 3. Fallback: if retreat was impossible, try expand with lower share (>= 0.35)
                # if safe and does not create any overlap
                if not retreated and share >= 0.35:
                    can_expand_fallback = (
                        (grown[3] - grown[1]) <= (TALL_REGION - 0.005) * page_h
                        and (rect_area(b) <= 2.5 * rect_area(r) if blk.get("kind") == "panel" else True)
                    )
                    if can_expand_fallback:
                        for o_a_idx, o_p_idx, o_p in all_parts:
                            if o_a_idx == a_idx:
                                continue
                            if rect_overlap(grown, tuple(o_p)) > 1.0:
                                can_expand_fallback = False
                                break
                    if can_expand_fallback:
                        p[:] = list(grown)
                        r = tuple(p)
                        moved = True

        # Write updated parts back to activities
        max_h = (TALL_REGION - 0.005) * page_h
        for a_idx, p_idx, p in all_parts:
            if p[3] - p[1] > max_h:
                p[1] = p[3] - max_h
            activities[a_idx].parts[p_idx] = (
                round(p[0], 4), round(p[1], 4), round(p[2], 4), round(p[3], 4)
            )
        for act in activities:
            if act.parts:
                act.rect = (
                    round(min(p[0] for p in act.parts), 4),
                    round(min(p[1] for p in act.parts), 4),
                    round(max(p[2] for p in act.parts), 4),
                    round(max(p[3] for p in act.parts), 4),
                )
                if act.rect[3] - act.rect[1] > max_h:
                    act.rect = (
                        act.rect[0],
                        round(act.rect[3] - max_h, 4),
                        act.rect[2],
                        act.rect[3],
                    )

        if not moved:
            break

    return activities


def clean_page_activities(
    activities: List[ActivityRegion],
    geom: Optional[PageGeometry] = None,
    trace: Optional["GrowthTrace"] = None,
) -> List[ActivityRegion]:
    """
    Final validation, de-overlapping, and edge-snapping pass for page activities.

    Guarantees:
    - Zero slivers (all parts >= 6.0 pt width and height).
    - Zero overlapping hotspots (all pairs overlapArea <= 1.0 pt^2), whether
      geometry is provided or separated at midpoints.
    - Activity primary rect is the exact bounding box of its parts.
    - When `geom` is provided, hotspot edges are snapped to drawn block boundaries
      (panels, solution spaces, figures) to eliminate cuts.
    """
    if not activities:
        return []

    # 1. Filter out sliver parts and drop empty activities
    cleaned_acts: List[ActivityRegion] = []
    for act in activities:
        raw_parts = act.parts if act.parts else [act.rect]
        valid_parts = [
            list(p) for p in raw_parts
            if (p[2] - p[0] >= MIN_HOTSPOT and p[3] - p[1] >= MIN_HOTSPOT)
        ]
        if valid_parts:
            act.parts = [tuple(p) for p in valid_parts]
            act.rect = (
                round(min(p[0] for p in valid_parts), 4),
                round(min(p[1] for p in valid_parts), 4),
                round(max(p[2] for p in valid_parts), 4),
                round(max(p[3] for p in valid_parts), 4),
            )
            cleaned_acts.append(act)
        elif trace is not None:
            trace.drop("clean", "sliver-in", rect=act.rect, text=act.headline or "",
                       label=act.label, parts=len(raw_parts))

    # 2. De-overlap pass across all parts on the page
    for _ in range(10):
        all_parts = []
        for a_idx, a in enumerate(cleaned_acts):
            for p_idx, p in enumerate(a.parts):
                all_parts.append((a_idx, p_idx, list(p)))

        moved = False
        to_remove = set()
        for i in range(len(all_parts)):
            for j in range(i + 1, len(all_parts)):
                a_idx_i, p_idx_i, pi = all_parts[i]
                a_idx_j, p_idx_j, pj = all_parts[j]
                if (a_idx_i, p_idx_i) in to_remove or (a_idx_j, p_idx_j) in to_remove:
                    continue

                ox = max(0.0, min(pi[2], pj[2]) - max(pi[0], pj[0]))
                oy = max(0.0, min(pi[3], pj[3]) - max(pi[1], pj[1]))
                if ox * oy > 1.0:
                    # Two edges that were meant to meet can end a hundredth of
                    # a point inside one another -- a seam arrived at twice,
                    # from two directions, each time rounded. Across the width
                    # of a column that hairline is several square points, and
                    # it is counted as one region stealing another's clicks.
                    # Nothing here is in dispute, so it is closed rather than
                    # cut: the shallow edge is pulled back until the two merely
                    # touch. This needs no geometry and runs without it.
                    if oy <= HAIRLINE and oy <= ox:
                        if (pi[1] + pi[3]) >= (pj[1] + pj[3]):
                            pi[1] = pj[3]
                        else:
                            pj[1] = pi[3]
                        moved = True
                        continue
                    if ox <= HAIRLINE:
                        if (pi[0] + pi[2]) >= (pj[0] + pj[2]):
                            pi[0] = pj[2]
                        else:
                            pj[0] = pi[2]
                        moved = True
                        continue

                    if oy <= ox:
                        # Vertical separation
                        if (pi[1] + pi[3]) >= (pj[1] + pj[3]):
                            upper, lower = pi, pj
                            u_ref, l_ref = (a_idx_i, p_idx_i), (a_idx_j, p_idx_j)
                        else:
                            upper, lower = pj, pi
                            u_ref, l_ref = (a_idx_j, p_idx_j), (a_idx_i, p_idx_i)
                        mid = (max(upper[1], lower[1]) + min(upper[3], lower[3])) / 2.0
                        if geom is not None:
                            seam = nearest_plane(
                                legal_planes(
                                    geom,
                                    max(pi[0], pj[0]), min(pi[2], pj[2]),
                                    min(upper[3], lower[3]), max(upper[1], lower[1]),
                                ),
                                mid,
                            )
                            if seam is None:
                                if _evict(geom, pi, pj):
                                    moved = True
                                    continue
                            else:
                                mid = seam
                        upper[1] = mid + 0.5
                        lower[3] = mid - 0.5
                        moved = True
                        if upper[3] - upper[1] < MIN_HOTSPOT:
                            to_remove.add(u_ref)
                        if lower[3] - lower[1] < MIN_HOTSPOT:
                            to_remove.add(l_ref)
                    else:
                        # Horizontal separation
                        if (pi[0] + pi[2]) >= (pj[0] + pj[2]):
                            right, left = pi, pj
                            r_ref, l_ref = (a_idx_i, p_idx_i), (a_idx_j, p_idx_j)
                        else:
                            right, left = pj, pi
                            r_ref, l_ref = (a_idx_j, p_idx_j), (a_idx_i, p_idx_i)
                        mid = (max(right[0], left[0]) + min(right[2], left[2])) / 2.0
                        if geom is not None:
                            seam = nearest_plane(
                                legal_planes_x(
                                    geom,
                                    max(pi[1], pj[1]), min(pi[3], pj[3]),
                                    max(right[0], left[0]), min(right[2], left[2]),
                                ),
                                mid,
                            )
                            if seam is None:
                                if _evict(geom, pi, pj):
                                    moved = True
                                    continue
                            else:
                                mid = seam
                        right[0] = mid + 0.5
                        left[2] = mid - 0.5
                        moved = True
                        if right[2] - right[0] < MIN_HOTSPOT:
                            to_remove.add(r_ref)
                        if left[2] - left[0] < MIN_HOTSPOT:
                            to_remove.add(l_ref)

        # The parts this pass actually moved.
        moved_parts: Dict[Tuple[int, int], Tuple[float, float, float, float]] = {
            (a_idx, p_idx): tuple(rect) for a_idx, p_idx, rect in all_parts
        }

        if to_remove:
            new_acts = []
            for a_idx, a in enumerate(cleaned_acts):
                rem_parts = [
                    moved_parts.get((a_idx, p_idx), tuple(p))
                    for p_idx, p in enumerate(a.parts)
                    if (a_idx, p_idx) not in to_remove
                ]
                if rem_parts:
                    a.parts = rem_parts
                    a.rect = (
                        round(min(p[0] for p in rem_parts), 4),
                        round(min(p[1] for p in rem_parts), 4),
                        round(max(p[2] for p in rem_parts), 4),
                        round(max(p[3] for p in rem_parts), 4),
                    )
                    new_acts.append(a)
            cleaned_acts = new_acts
        else:
            for a_idx, a in enumerate(cleaned_acts):
                a_parts = [
                    moved_parts.get((a_idx, p_idx), tuple(p))
                    for p_idx, p in enumerate(a.parts)
                ]
                a.parts = a_parts
                a.rect = (
                    round(min(p[0] for p in a_parts), 4),
                    round(min(p[1] for p in a_parts), 4),
                    round(max(p[2] for p in a_parts), 4),
                    round(max(p[3] for p in a_parts), 4),
                )

        if not moved:
            break

    # 3. Final edge-snapping against page geometry when available
    page_w = 595.0
    page_h = 842.0
    if geom is not None and geom.content_box:
        page_w = max(page_w, geom.content_box[2] + 40.0)
        page_h = max(page_h, geom.content_box[3] + 40.0)

    if geom is not None:
        cleaned_acts = snap_edges(cleaned_acts, geom, page_w, page_h)

    max_h = (TALL_REGION - 0.005) * page_h
    final_acts = []
    for act in cleaned_acts:
        valid_parts = []
        for p in act.parts:
            if p[2] - p[0] >= MIN_HOTSPOT and p[3] - p[1] >= MIN_HOTSPOT:
                if p[3] - p[1] > max_h:
                    p = (p[0], round(p[3] - max_h, 4), p[2], p[3])
                valid_parts.append(p)
        if valid_parts:
            act.parts = valid_parts
            act.rect = (
                round(min(p[0] for p in valid_parts), 4),
                round(min(p[1] for p in valid_parts), 4),
                round(max(p[2] for p in valid_parts), 4),
                round(max(p[3] for p in valid_parts), 4),
            )
            if act.rect[3] - act.rect[1] > max_h:
                act.rect = (
                    act.rect[0],
                    round(act.rect[3] - max_h, 4),
                    act.rect[2],
                    act.rect[3],
                )
            final_acts.append(act)
        elif trace is not None:
            # Separation, eviction or edge-snapping took the last part below a
            # hotspot's minimum. The activity was found and then lost, which is
            # a different fault from never having found it.
            trace.drop("clean", "sliver-out", rect=act.rect, text=act.headline or "",
                       label=act.label, parts=len(act.parts))

    if trace is not None:
        trace.count("clean.kept", len(final_acts))
    return final_acts
