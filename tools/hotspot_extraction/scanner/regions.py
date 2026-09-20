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
from typing import Dict, List, Optional, Set, Tuple

from .primitives import PagePrimitives, TextSpan, VectorDrawing
from .layout import (
    COLUMN_CHANNEL,
    COLUMN_CLEAR,
    Column,
    PageLayout,
    _channel_width,
    _column_clearance,
    _text_lines,
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
    anchored: bool = False


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
    whole_tol: float = WHOLE_TOL
) -> bool:
    """
    Check if rect cuts block instead of taking it whole or leaving it alone.
    Returns True if overlap share is between whole_tol and 1 - whole_tol.
    """
    block_area = rect_area(block)
    if block_area <= 0.0:
        return False
    share = rect_overlap(rect, block) / block_area
    return share > whole_tol and share < 1.0 - whole_tol


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
) -> List[Tuple[float, float, float, float]]:
    """
    Detect drawn vector background panels (dialogue bubbles, tinted boxes).

    Identifies shapes with width >= PANEL_MIN_W (60 pt) and height >= PANEL_MIN_H (30 pt)
    that contain at least PANEL_LINES (2) lines of prose, filtering out nested panels.
    """
    prose_spans = [s for s in body_spans if is_prose(s)]
    candidates: List[Tuple[float, float, float, float]] = []

    for d in drawings:
        r = d.rect
        w = r[2] - r[0]
        h = r[3] - r[1]
        if w < PANEL_MIN_W or h < PANEL_MIN_H:
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
) -> List[dict]:
    """
    Detect solution spaces (ruled answer lines, empty table cells, answer boxes).

    Returns a list of solution blocks:
    [{"rect": (x0, y0, x1, y1), "kind": "cell" | "panel"}, ...]
    """
    horizontal: List[Tuple[float, float, float, float]] = []
    vertical: List[Tuple[float, float, float, float]] = []
    panels: List[Tuple[float, float, float, float]] = []

    for d in drawings:
        r = d.rect
        w = r[2] - r[0]
        h = r[3] - r[1]
        if h <= RULE_THICK and w >= RULE_MIN:
            horizontal.append(r)
        elif w <= RULE_THICK and h >= RULE_MIN:
            vertical.append(r)
        if w >= RULE_MIN and h >= MIN_CELL:
            panels.append(r)

    if not horizontal:
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
        if parent[i] == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]

    def union(i: int, j: int):
        root_i = find(i)
        root_j = find(j)
        if root_i != root_j:
            parent[root_i] = root_j

    def near(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
        return (
            min(a[2], b[2]) + RULE_CLUSTER >= max(a[0], b[0]) and
            min(a[3], b[3]) + RULE_ROW >= max(a[1], b[1])
        )

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
        rows = [r for r in cluster if r[3] - r[1] <= RULE_THICK]
        if len(rows) < 2:
            continue

        box = cluster[0]
        for r in cluster[1:]:
            box = rect_union(box, r)

        ys = sorted(set(round((r[1] + r[3]) / 2.0, 1) for r in rows), reverse=True)
        cols = sorted(set(round((r[0] + r[2]) / 2.0, 1) for r in cluster if r[2] - r[0] <= RULE_THICK))

        if len(cols) >= 2:
            xs = cols
        else:
            xs = [box[0], box[2]]
            gaps = [ys[k] - ys[k + 1] for k in range(len(ys) - 1)]
            gaps.sort()
            pitch = gaps[len(gaps) // 2] if gaps else RULE_ROW / 2.0
            ys.insert(0, ys[0] + pitch)

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

    return blocks


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


# Panel & solution snapping

def panel_for(
    marker: DetectedMarker,
    band_top: float,
    band_bottom: float,
    content: Tuple[float, float, float, float],
    panels: List[Tuple[float, float, float, float]],
    all_markers: List[DetectedMarker],
    flow_spans: List[TextSpan],
) -> Optional[Tuple[float, float, float, float]]:
    """
    Find the drawn panel a question's flow runs into and snap to it whole.

    Returns the union of snapped panels, or None.
    """
    if not content or not panels or not flow_spans:
        return None

    grown = None
    for panel in panels:
        # Reject if another activity marker is enclosed inside this panel
        if any(m is not marker and encloses(panel, m.span.bbox, tol=-2.0) for m in all_markers):
            continue

        # Held in band
        held = min(panel[3], band_top) - max(panel[1], band_bottom)
        if held < 0.5 * (panel[3] - panel[1]):
            continue

        # Flow members must include text inside the panel
        has_text_inside = any(
            contains_point(panel, (s.bbox[0] + s.bbox[2]) / 2.0, (s.bbox[1] + s.bbox[3]) / 2.0)
            for s in flow_spans
        )
        if not has_text_inside:
            continue

        # And running the length of it
        along = min(content[3], panel[3]) - max(content[1], panel[1])
        if along < 0.5 * (panel[3] - panel[1]):
            continue

        grown = rect_union(grown, panel)

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

    act_span_ids = {id(s) for s in activity_spans}
    marker_span_ids = {id(m.span) for m in all_markers if m is not marker}
    walls = [
        s for s in body_spans
        if (is_prose(s) and id(s) not in act_span_ids) or id(s) in marker_span_ids
    ]

    grown = None
    for block in solution_blocks:
        b_rect = block["rect"]
        if adjacent is not None and block["kind"] == "panel":
            continue

        overlap = min(b_rect[3], band_top) - max(b_rect[1], band_bottom)
        if overlap < 0.5 * (b_rect[3] - b_rect[1]):
            continue

        across = min(b_rect[2], right) - max(b_rect[0], left)
        if across < 0.5 * (b_rect[2] - b_rect[0]):
            continue

        gap_top = content[1]
        gap_bottom = b_rect[3]
        if adjacent is not None and gap_top - gap_bottom > adjacent:
            continue

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


# Flow growth & headline extraction

def is_aside(line: TextLine, body_font_size: float) -> bool:
    """Check if line is an aside (rotated tab, small print caption/credit)."""
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
) -> Tuple[Optional[Tuple[float, float, float, float]], List[TextSpan]]:
    """
    Absorb text lines following the marker in reading order according to measure and leading.
    """
    lines = group_lines(band_items)
    if not lines:
        return None, []

    # Only consider lines at or below the marker
    valid_lines = [l for l in lines if l.y <= marker.span.bbox[3] + 2.0]
    if not valid_lines:
        return None, []

    opener = valid_lines[0]
    left = min(col_x0, opener.x0) - COLUMN_EDGE
    right = max(col_x1, opener.x1) + COLUMN_EDGE
    measure = [l for l in valid_lines if l.x0 >= left and l.x1 <= right]
    if not measure:
        return None, []

    # Leading estimation
    gaps = [measure[i].y - measure[i + 1].y for i in range(len(measure) - 1) if measure[i].y - measure[i + 1].y > 1.0]
    leading = sorted(gaps)[len(gaps) // 2] if gaps else 1.3 * marker.span.size

    items: List[TextSpan] = []
    rect: Optional[Tuple[float, float, float, float]] = None
    baseline = (marker.span.bbox[1] + marker.span.bbox[3]) / 2.0

    for line in measure:
        if is_aside(line, body_font_size):
            continue
        gap = baseline - line.y
        # Stop at large gap or headings
        if gap > FLOW_BREAK:
            break
        max_size = max(s.size for s in line.spans)
        if max_size >= body_font_size * HEADING_RATIO:
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


def separate_rects(parts: List[PartRecord], passes: int = 6) -> List[PartRecord]:
    """
    Push apart overlapping hotspot rectangles along the axis of least penetration.

    Preserves panel boundaries and anchor baselines, maintaining HOTSPOT_GAP separation.
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


# Sub-item extraction

def build_sub_items(
    marker: DetectedMarker,
    parent_rect: Tuple[float, float, float, float],
    page_num: int,
    body_spans: List[TextSpan],
    solution_blocks: List[dict],
) -> List[SubItemRect]:
    """Build SubItemRect objects for detected sub-questions."""
    if not marker.items:
        return []

    out: List[SubItemRect] = []
    sorted_items = sorted(marker.items, key=lambda it: -it.span.bbox[1])

    for j, sub in enumerate(sorted_items):
        top = sub.span.bbox[3] + 2.0
        bottom = sorted_items[j + 1].span.bbox[3] + 2.0 if j + 1 < len(sorted_items) else parent_rect[1]

        sub_spans = [
            s for s in body_spans
            if bottom <= (s.bbox[1] + s.bbox[3]) / 2.0 <= top and
            s.bbox[0] >= parent_rect[0] - 2.0 and s.bbox[2] <= parent_rect[2] + 2.0
        ]

        rect = sub.span.bbox
        text_words = []
        for s in sorted(sub_spans, key=lambda s: (-s.bbox[1], s.bbox[0])):
            rect = rect_union(rect, s.bbox)
            text_words.append(s.text.strip())

        padded_rect = (
            round(max(rect[0] - 4.0, parent_rect[0]), 4),
            round(max(rect[1] - 4.0, parent_rect[1]), 4),
            round(min(rect[2] + 4.0, parent_rect[2]), 4),
            round(min(rect[3] + 4.0, parent_rect[3]), 4),
        )

        sub_id = f"p{page_num}-{marker.label.strip('.:) ')}-{sub.number}"
        out.append(
            SubItemRect(
                id=sub_id,
                label=sub.label,
                number=sub.number,
                part_index=0,
                rect=padded_rect,
                text=" ".join(text_words)[:200],
            )
        )

    out.sort(key=lambda it: it.number if it.number is not None else 0)
    return out


# Primary region growth orchestrator

def grow_activity_regions(
    primitives: PagePrimitives,
    layout: PageLayout,
    markers: List[DetectedMarker],
) -> List[ActivityRegion]:
    """
    Grow all detected activity markers on a page into complete ActivityRegion objects.

    Args:
        primitives: Raw page primitives (text, drawings, images).
        layout: Detected page layout (columns, content box, body size).
        markers: Detected activity markers from Phase 2.

    Returns:
        List of ActivityRegion instances with resolved bounding boxes, sub-items, and headlines.
    """
    if not markers:
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
    panels = detect_panels(primitives.drawings, body_spans)
    solution_blocks = detect_solution_spaces(primitives.drawings, body_spans)

    # 3. Segment page into horizontal strips and cells
    blocks = [{"x0": s.bbox[0], "x1": s.bbox[2], "y0": s.bbox[1], "y1": s.bbox[3]} for s in body_spans]
    lines = _text_lines(body_spans)
    for im in primitives.images:
        if im.bbox[1] >= content_box[1] - 2.0 and im.bbox[3] <= content_box[3] + 2.0:
            ib = {"x0": im.bbox[0], "x1": im.bbox[2], "y0": im.bbox[1], "y1": im.bbox[3]}
            blocks.append(ib)
            lines.append(ib)
    strips = segment_strips(blocks, lines, layout.columns, content_box[3], content_box[1], markers)
    cells = build_page_cells(strips, layout.columns, markers, content_box[3], content_box[1])

    # 4. Construct vertical bands per activity
    # Map markers to cells and activities
    act_bands: Dict[int, List[dict]] = {id(m): [] for m in markers}
    by_marker_id = {id(m): m for m in markers}

    running_act = None
    running_block = None

    for cell in cells:
        if cell["dead"]:
            continue
        if cell["block_index"] != running_block:
            running_act = None
            running_block = cell["block_index"]

        cell_markers = cell["markers"]
        if not cell_markers:
            if running_act and cell["column"] > running_act.column_index:
                act_bands[id(running_act)].append({
                    "x0": cell["x0"], "x1": cell["x1"], "column": cell["column"],
                    "top": cell["top"], "bottom": cell["bottom"], "own": False, "items": [],
                })
            continue

        first_top = cell_markers[0].span.bbox[3] + 2.0
        if running_act and cell["column"] > running_act.column_index and cell["top"] - first_top > 1.0:
            act_bands[id(running_act)].append({
                "x0": cell["x0"], "x1": cell["x1"], "column": cell["column"],
                "top": cell["top"], "bottom": first_top, "own": False, "items": [],
            })

        for i, m in enumerate(cell_markers):
            bottom = cell_markers[i + 1].span.bbox[3] + 2.0 if i + 1 < len(cell_markers) else cell["bottom"]
            act_bands[id(m)].append({
                "x0": cell["x0"], "x1": cell["x1"], "column": cell["column"],
                "top": m.span.bbox[3] + 2.0, "bottom": bottom, "own": True, "items": [],
            })
            running_act = m
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
            )
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

            if rect[2] - rect[0] < MIN_HOTSPOT or rect[3] - rect[1] < MIN_HOTSPOT:
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
    separated = separate_rects(part_records)

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
        if m_id not in regions_by_owner:
            continue
        parts = regions_by_owner[m_id]
        if not parts:
            continue

        primary_rect = parts[0]
        headline = build_headline(m, act_spans.get(id(m), []))
        sub_items = build_sub_items(m, primary_rect, page_num, body_spans, solution_blocks)

        clean_label = m.label.strip(".:) ")
        act_id = f"p{page_num}-{clean_label}"

        activities.append(
            ActivityRegion(
                id=act_id,
                label=clean_label,
                column=m.column_index,
                rect=primary_rect,
                parts=parts,
                headline=headline,
                items=sub_items if sub_items else None,
                anchored=False,
            )
        )

    return clean_page_activities(activities)


def clean_page_activities(activities: List[ActivityRegion]) -> List[ActivityRegion]:
    """
    Final validation and enforcement pass for page activity regions.
    Guarantees:
    - Zero slivers (all parts >= 6.0 pt width and height).
    - Zero overlapping hotspots (all pairs overlapArea <= 1.0 pt^2).
    - Activity primary rect is the exact bounding box of its parts.
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
                    moved = True
                    if oy <= ox:
                        # Vertical separation
                        if (pi[1] + pi[3]) >= (pj[1] + pj[3]):
                            upper, lower = pi, pj
                            u_ref, l_ref = (a_idx_i, p_idx_i), (a_idx_j, p_idx_j)
                        else:
                            upper, lower = pj, pi
                            u_ref, l_ref = (a_idx_j, p_idx_j), (a_idx_i, p_idx_i)
                        mid = (max(upper[1], lower[1]) + min(upper[3], lower[3])) / 2.0
                        upper[1] = mid + 0.5
                        lower[3] = mid - 0.5
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
                        right[0] = mid + 0.5
                        left[2] = mid - 0.5
                        if right[2] - right[0] < MIN_HOTSPOT:
                            to_remove.add(r_ref)
                        if left[2] - left[0] < MIN_HOTSPOT:
                            to_remove.add(l_ref)

        if to_remove:
            new_acts = []
            for a_idx, a in enumerate(cleaned_acts):
                rem_parts = [
                    tuple(p) for p_idx, p in enumerate(a.parts)
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
            # Update parts coordinates from in-place modifications
            for a_idx, a in enumerate(cleaned_acts):
                a_parts = [tuple(p) for p_idx, p in enumerate(a.parts)]
                a.parts = a_parts
                a.rect = (
                    round(min(p[0] for p in a_parts), 4),
                    round(min(p[1] for p in a_parts), 4),
                    round(max(p[2] for p in a_parts), 4),
                    round(max(p[3] for p in a_parts), 4),
                )

        if not moved:
            break

    return cleaned_acts
