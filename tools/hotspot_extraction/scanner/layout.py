"""
Page Layout Segmentation & Typographic Hierarchy Analysis.

Detects document typographic hierarchy (modal body text size), excludes
header/footer margin bands, extracts printed page numbers (folios), and
segments the page into columns via marker clustering and projection profiles.
"""

from dataclasses import dataclass
import re
from typing import List, Optional, Tuple

from .primitives import PagePrimitives, TextSpan

# Layout bands and tolerances (standardized with PDF user space)
HEADER_BAND = 40.0       # pt from top of page
FOOTER_BAND = 44.0       # pt from bottom of page
COLUMN_TOLERANCE = 12.0  # pt spread within which markers/starts belong to the same column
COLUMN_CHANNEL = 10.0    # pt minimum whitespace width for a column gutter
COLUMN_CLEAR = 0.5       # fraction of decided height a real column gutter must stay open
COLUMN_PAIRED = 0.35     # fraction of height candidate columns must run side-by-side
MIN_COLUMN_LINES = 4     # minimum line starts to consider a text cluster a column
FOLIO_MAX = 4            # max digits for a printed page number


@dataclass
class Column:
    """A vertical column channel on the page."""
    index: int
    x0: float
    x1: float
    opens_at: float
    closes_at: float


@dataclass
class PageLayout:
    """The detected geometric layout of a single page."""
    content_box: Tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF user space
    columns: List[Column]
    body_font_size: float


def detect_folio(
    spans: List[TextSpan],
    page_width: float,
    page_height: float,
) -> Optional[int]:
    """
    Extract the printed page number (folio) from the header or footer margins.

    Prefers the footer band over the header band and outer corners/edges over
    centered items.
    """
    candidates = []
    for span in spans:
        txt = span.text.strip()
        if not re.fullmatch(r"\d{1,4}", txt):
            continue
        val = int(txt)
        if val <= 0:
            continue

        in_foot = span.bbox[1] <= FOOTER_BAND
        in_head = span.bbox[3] >= page_height - HEADER_BAND
        if not in_foot and not in_head:
            continue

        edge_dist = min(span.bbox[0], page_width - span.bbox[2])
        candidates.append((val, in_foot, edge_dist))

    if not candidates:
        return None

    # Priority: footer first, then closest to sheet edge
    candidates.sort(key=lambda c: (1 if c[1] else 0, -c[2]), reverse=True)
    return candidates[0][0]


def detect_body_font_size(
    spans: List[TextSpan],
    content_box: Tuple[float, float, float, float],
) -> float:
    """
    Compute the modal body font size for the page by weighting font sizes by
    total character count in the content area.
    """
    _, y0, _, y1 = content_box
    size_weights = {}

    for span in spans:
        txt = span.text.strip()
        if not txt:
            continue
        # Exclude text outside the content vertical boundaries
        if span.bbox[3] < y0 - 2.0 or span.bbox[1] > y1 + 2.0:
            continue

        sz = round(span.size, 1)
        size_weights[sz] = size_weights.get(sz, 0) + len(txt)

    if not size_weights:
        return 10.0

    return max(size_weights.items(), key=lambda item: item[1])[0]


def _text_lines(spans: List[TextSpan]) -> List[dict]:
    """Group spans into horizontal lines, joining words across intra-line spaces."""
    sorted_spans = sorted(spans, key=lambda s: -(s.bbox[1] + s.bbox[3]) / 2.0)
    rows = []
    for s in sorted_spans:
        y_mid = (s.bbox[1] + s.bbox[3]) / 2.0
        if rows and abs(rows[-1]["y"] - y_mid) < 2.5:
            rows[-1]["spans"].append(s)
        else:
            rows.append({"y": y_mid, "spans": [s]})

    lines = []
    for row in rows:
        row_spans = sorted(row["spans"], key=lambda s: s.bbox[0])
        current_line = None
        for s in row_spans:
            if current_line and s.bbox[0] - current_line["x1"] <= COLUMN_CHANNEL:
                current_line["x1"] = max(current_line["x1"], s.bbox[2])
                current_line["y0"] = min(current_line["y0"], s.bbox[1])
                current_line["y1"] = max(current_line["y1"], s.bbox[3])
                current_line["spans"].append(s)
            else:
                current_line = {
                    "x0": s.bbox[0],
                    "x1": s.bbox[2],
                    "y0": s.bbox[1],
                    "y1": s.bbox[3],
                    "spans": [s],
                }
                lines.append(current_line)
    return lines


def _channel_width(
    blocks: List[dict],
    boundary: float,
    top: float,
    bottom: float,
    straddle_closes: bool = False,
) -> Optional[float]:
    """
    Measure the clear whitespace width across boundary between top and bottom.
    Returns None if nothing exists on one side to separate.
    """
    left_edge = -float("inf")
    right_edge = float("inf")
    straddles = False

    for b in blocks:
        if b["y1"] <= bottom or b["y0"] >= top:
            continue
        if b["x0"] < boundary and b["x1"] > boundary:
            straddles = True
        mid = (b["x0"] + b["x1"]) / 2.0
        if mid < boundary:
            left_edge = max(left_edge, b["x1"])
        else:
            right_edge = min(right_edge, b["x0"])

    if straddle_closes and straddles:
        return 0.0
    if left_edge == -float("inf") or right_edge == float("inf"):
        return None
    return right_edge - left_edge


def _column_clearance(
    blocks: List[dict],
    boundary: float,
    top: float,
    bottom: float,
    straddle_closes: bool = False,
) -> float:
    """Fraction of decided height where the channel remains open (>= COLUMN_CHANNEL)."""
    edges = {top, bottom}
    for b in blocks:
        for y in (b["y0"], b["y1"]):
            if bottom < y < top:
                edges.add(y)
    ys = sorted(edges, reverse=True)

    clear = 0.0
    decided = 0.0
    for i in range(len(ys) - 1):
        hi, lo = ys[i], ys[i + 1]
        if hi - lo < 0.25:
            continue
        w = _channel_width(blocks, boundary, hi, lo, straddle_closes)
        if w is None:
            continue
        decided += (hi - lo)
        if w >= COLUMN_CHANNEL:
            clear += (hi - lo)

    return clear / decided if decided > 0 else 1.0


def _side_by_side_share(
    blocks: List[dict],
    boundary: float,
    top: float,
    bottom: float,
) -> float:
    """Share of the total height where both sides of boundary carry content."""
    edges = {top, bottom}
    for b in blocks:
        for y in (b["y0"], b["y1"]):
            if bottom < y < top:
                edges.add(y)
    ys = sorted(edges, reverse=True)

    paired = 0.0
    for i in range(len(ys) - 1):
        hi, lo = ys[i], ys[i + 1]
        if hi - lo < 0.25:
            continue
        w = _channel_width(blocks, boundary, hi, lo)
        if w is not None:
            paired += (hi - lo)

    height = top - bottom
    return paired / height if height > 0 else 0.0


def _cluster_by_x(items: List[dict], key="x0", tol: float = COLUMN_TOLERANCE) -> List[List[dict]]:
    """Cluster items by a horizontal coordinate within tolerance."""
    sorted_items = sorted(items, key=lambda it: it[key])
    clusters: List[List[dict]] = []
    current: List[dict] = []
    anchor: Optional[float] = None

    for it in sorted_items:
        x = it[key]
        if anchor is None or abs(x - anchor) <= tol:
            if anchor is None:
                anchor = x
            current.append(it)
        else:
            clusters.append(current)
            current = [it]
            anchor = x

    if current:
        clusters.append(current)
    return clusters


def detect_columns(
    body_spans: List[TextSpan],
    content_top: float,
    content_bottom: float,
    page_width: float,
    candidate_markers: Optional[List[TextSpan]] = None,
) -> List[Column]:
    """
    Detect column channels across the page.

    Uses candidate markers to seed column left edges when present, verifying
    channel clearance. Falls back to text line projection profiling if no
    markers are available.
    """
    if not body_spans:
        return [
            Column(
                index=0,
                x0=50.0,
                x1=page_width - 50.0,
                opens_at=content_top,
                closes_at=content_bottom,
            )
        ]

    content_right = max([page_width - 30.0] + [s.bbox[2] for s in body_spans])
    blocks = [{"x0": s.bbox[0], "x1": s.bbox[2], "y0": s.bbox[1], "y1": s.bbox[3]} for s in body_spans]

    # Strategy 1: Marker-guided column clustering
    if candidate_markers:
        marker_dicts = [{"span": m, "x0": m.bbox[0]} for m in candidate_markers]
        clusters = _cluster_by_x(marker_dicts, key="x0", tol=COLUMN_TOLERANCE)
        clusters.sort(key=lambda c: min(it["x0"] for it in c))

        leaders: List[List[dict]] = []
        col_left = -float("inf")
        for cluster in clusters:
            left = min(it["x0"] for it in cluster)
            own = [b for b in blocks if b["x0"] >= col_left - 4.0]
            clearance = _column_clearance(own, left - 4.0, content_top, content_bottom) if leaders else 1.0
            if clearance >= COLUMN_CLEAR:
                leaders.append(cluster)
                col_left = left

        if leaders:
            cols = []
            for idx, cl in enumerate(leaders):
                left = min(it["span"].bbox[0] for it in cl) - 6.0
                if idx + 1 < len(leaders):
                    next_left = min(it["span"].bbox[0] for it in leaders[idx + 1]) - 10.0
                else:
                    next_left = content_right + 6.0

                opens_at = max(it["span"].bbox[3] + 2.0 for it in cl)
                closes_at = min(it["span"].bbox[1] for it in cl)
                cols.append(
                    Column(
                        index=idx,
                        x0=round(left, 4),
                        x1=round(next_left, 4),
                        opens_at=round(opens_at, 4),
                        closes_at=round(closes_at, 4),
                    )
                )
            return cols

    # Strategy 2: Text-line projection profile clustering (fallback)
    lines = _text_lines(body_spans)
    starts = [{"x0": l["x0"], "line": l} for l in lines]
    line_clusters = _cluster_by_x(starts, key="x0", tol=COLUMN_TOLERANCE)
    line_clusters = [c for c in line_clusters if len(c) >= MIN_COLUMN_LINES]
    line_clusters.sort(key=lambda c: min(it["x0"] for it in c))

    kept_clusters: List[List[dict]] = []
    column_left = -float("inf")
    for cluster in line_clusters:
        left = min(it["x0"] for it in cluster)
        own = [b for b in blocks if b["x0"] >= column_left - 4.0]
        if kept_clusters:
            sb = _side_by_side_share(own, left - 4.0, content_top, content_bottom)
            cl = _column_clearance(own, left - 4.0, content_top, content_bottom)
            if sb < COLUMN_PAIRED or cl < COLUMN_CLEAR:
                continue
        kept_clusters.append(cluster)
        column_left = left

    if kept_clusters:
        cols = []
        for idx, cl in enumerate(kept_clusters):
            left = min(it["x0"] for it in cl) - 6.0
            if idx + 1 < len(kept_clusters):
                next_left = min(it["x0"] for it in kept_clusters[idx + 1]) - 10.0
            else:
                next_left = content_right + 6.0
            cols.append(
                Column(
                    index=idx,
                    x0=round(left, 4),
                    x1=round(next_left, 4),
                    opens_at=round(content_top, 4),
                    closes_at=round(content_bottom, 4),
                )
            )
        return cols

    # Single-column fallback
    min_x = min(s.bbox[0] for s in body_spans)
    return [
        Column(
            index=0,
            x0=round(max(0.0, min_x - 6.0), 4),
            x1=round(content_right + 6.0, 4),
            opens_at=round(content_top, 4),
            closes_at=round(content_bottom, 4),
        )
    ]


def detect_layout(
    primitives: PagePrimitives,
    candidate_markers: Optional[List[TextSpan]] = None,
) -> PageLayout:
    """
    Compute the page layout for a single page.

    Args:
        primitives: Extracted page primitives.
        candidate_markers: Optional list of pre-identified marker text spans.

    Returns:
        PageLayout containing content_box, columns, and modal body_font_size.
    """
    content_top = primitives.height - HEADER_BAND
    content_bottom = FOOTER_BAND

    # Body spans excluding running header and footer margin bands
    body_spans = [
        s for s in primitives.spans
        if s.bbox[1] >= content_bottom - 2.0 and s.bbox[3] <= content_top + 2.0
    ]

    min_x = min((s.bbox[0] for s in body_spans), default=50.0)
    max_x = max((s.bbox[2] for s in body_spans), default=primitives.width - 50.0)
    content_box = (
        round(max(0.0, min_x - 6.0), 4),
        round(content_bottom, 4),
        round(min(primitives.width, max_x + 6.0), 4),
        round(content_top, 4),
    )

    body_font_size = detect_body_font_size(body_spans, content_box)

    if candidate_markers is None:
        from .markers import find_candidate_markers
        candidates = find_candidate_markers(body_spans, body_font_size)
        candidate_markers = [c["span"] for c in candidates]

    columns = detect_columns(
        body_spans=body_spans,
        content_top=content_top,
        content_bottom=content_bottom,
        page_width=primitives.width,
        candidate_markers=candidate_markers,
    )

    return PageLayout(
        content_box=content_box,
        columns=columns,
        body_font_size=body_font_size,
    )
