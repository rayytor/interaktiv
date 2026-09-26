"""
Page Layout Segmentation & Typographic Hierarchy Analysis.

Detects document typographic hierarchy (modal body text size), excludes
header/footer margin bands, extracts printed page numbers (folios), and
segments the page into columns via marker clustering and projection profiles.
"""

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Tuple

from .primitives import PagePrimitives, TextSpan

# Layout bands and tolerances (standardized with PDF user space)
HEADER_BAND = 40.0       # pt from top of page
FOOTER_BAND = 44.0       # pt from bottom of page
COLUMN_TOLERANCE = 12.0  # pt spread within which markers/starts belong to the same column
COLUMN_CHANNEL = 10.0    # pt minimum whitespace width for a column gutter
COLUMN_CLEAR = 0.5       # fraction of decided height a real column gutter must stay open
COLUMN_PAIRED = 0.35     # fraction of height candidate columns must run side-by-side
MIN_COLUMN_LINES = 4     # minimum line starts to consider a text cluster a column
COLUMN_MIN_W = 54.0      # pt: narrowest strip that can hold a column of text rather than a hanging indent
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


# A line has to carry this much type before it is evidence of the body size.
BODY_LINE_CHARS = 20      # stripped characters on the line
BODY_LINE_WIDTH = 60.0    # pt: how far the line runs


def local_font_size(spans: List[TextSpan], fallback: float = 10.0) -> float:
    """
    The modal type size over a set of spans, weighted by character count.

    Used both for the page as a whole and for a single activity's band, so that
    a question set in a size the page at large does not use is still measured
    against itself.
    """
    size_weights: Dict[float, int] = {}
    for span in spans:
        txt = span.text.strip()
        if not txt:
            continue
        sz = round(span.size, 1)
        size_weights[sz] = size_weights.get(sz, 0) + len(txt)
    if not size_weights:
        return fallback
    return max(size_weights.items(), key=lambda item: item[1])[0]


def detect_body_font_size(
    spans: List[TextSpan],
    content_box: Tuple[float, float, float, float],
) -> float:
    """
    The modal body type size for the page.

    Weighting every span by its character count reads the page's *ink*, not its
    prose, and on a sheet carrying a full-page map or a dense diagram the ink is
    mostly labels: on sheet 29 of the history book the map's place names total
    947 characters at 3.9 pt against 866 at the real body 9.5 pt, so the page
    was said to be set in 3.9 pt type. Everything downstream then reads the
    body as a heading -- `flow_content` breaks on the first line of every
    activity, and the region collapses to the marker glyph.

    So the evidence is restricted to lines that look like running text: a label
    on a map is a short span standing alone, while a line of prose carries a
    sentence's worth of characters and runs a measure. Lines are used rather
    than spans because justified text arrives in fragments that only add up to
    a sentence once joined.
    """
    _, y0, _, y1 = content_box
    inside = [
        s for s in spans
        if s.text.strip() and not (s.bbox[3] < y0 - 2.0 or s.bbox[1] > y1 + 2.0)
    ]
    if not inside:
        return 10.0

    running = [
        line for line in _text_lines(inside)
        if line["x1"] - line["x0"] >= BODY_LINE_WIDTH
        and sum(len(s.text.strip()) for s in line["spans"]) >= BODY_LINE_CHARS
    ]
    if running:
        return local_font_size([s for line in running for s in line["spans"]])

    # A sheet with no running text at all -- a title page, a plate -- still has
    # to answer, and there the ink is all there is.
    return local_font_size(inside)


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


def _cluster_by_x(items: List[dict], key="x0", tol: Optional[float] = None) -> List[List[dict]]:
    """
    Cluster items by a horizontal coordinate within tolerance.

    `tol` resolves to `COLUMN_TOLERANCE` at call time. As a default argument it
    would have been frozen at import, so a profile that widened the column
    tolerance would have left this one clustering pass on the old value.
    """
    if tol is None:
        tol = COLUMN_TOLERANCE
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


def _boundary_candidates(
    body_spans: List[TextSpan],
    blocks: List[dict],
    candidate_markers: Optional[List[TextSpan]],
) -> List[dict]:
    """
    Every x where a column might start, and what argues for it.

    Two things can propose a boundary. A cluster of activity markers proposes
    one because a letter set in the margin is where a column of exercises
    begins. A cluster of line starts proposes one because that is what a column
    of prose looks like from below, and it proposes it whether or not any letter
    was ever printed beside it -- which is the case a marker-only reading cannot
    see at all.

    Both are returned together, tagged with their provenance, because the two
    are held to different standards downstream: a letter is strong enough
    evidence to open a column on the clearance test alone, while bare line
    starts must also show that the two sides run side by side.
    """
    proposals: List[dict] = []

    if candidate_markers:
        marker_dicts = [{"span": m, "x0": m.bbox[0]} for m in candidate_markers]
        for cluster in _cluster_by_x(marker_dicts, key="x0", tol=COLUMN_TOLERANCE):
            proposals.append({
                "x": min(it["x0"] for it in cluster),
                "marked": True,
                "lines": len(cluster),
                "spans": [it["span"] for it in cluster],
            })

    lines = _text_lines(body_spans)
    starts = [{"x0": l["x0"], "line": l} for l in lines]
    for cluster in _cluster_by_x(starts, key="x0", tol=COLUMN_TOLERANCE):
        if len(cluster) < MIN_COLUMN_LINES:
            continue
        proposals.append({
            "x": min(it["x0"] for it in cluster),
            "marked": False,
            "lines": len(cluster),
            "spans": [],
        })

    # Two proposals within a tolerance of each other are the same column edge
    # seen twice -- the letter and the prose that hangs off it. Fuse them, and
    # let the marked one carry the pair, so the column keeps the letter that
    # tells the growth pass where it opens.
    proposals.sort(key=lambda pr: (pr["x"], not pr["marked"]))
    fused: List[dict] = []
    for pr in proposals:
        if fused and pr["x"] - fused[-1]["x"] <= COLUMN_TOLERANCE:
            host = fused[-1]
            host["marked"] = host["marked"] or pr["marked"]
            host["lines"] = max(host["lines"], pr["lines"])
            host["spans"] = host["spans"] + pr["spans"]
            host["x"] = min(host["x"], pr["x"])
        else:
            fused.append(dict(pr))
    return fused


def detect_columns(
    body_spans: List[TextSpan],
    content_top: float,
    content_bottom: float,
    page_width: float,
    candidate_markers: Optional[List[TextSpan]] = None,
) -> List[Column]:
    """
    Detect column channels across the page.

    A column is a fact about the sheet, not about the exercises printed on it.
    The markers are consulted -- a letter in the margin is the clearest evidence
    a column exists, and it is the only thing that says where the column *opens*
    -- but they are not allowed to be the whole answer, because a column carries
    a letter only when the publisher chose to letter it. A self-assessment panel
    numbered 1..10 beside a lettered exercise is a column by every measure the
    page offers except the one a marker-seeded reading looks at, and taking the
    markers as the whole answer merges it into its neighbour and grows that
    neighbour straight through it.

    So both readings propose boundaries and each proposed boundary is then made
    to prove itself against the page's own whitespace. A letter may open a
    column that the prose alone would not have shown; it may never close one
    that the whitespace plainly shows.
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

    proposals = _boundary_candidates(body_spans, blocks, candidate_markers)

    # The leftmost proposal is the page's left edge rather than a split in it,
    # so it opens the first column and is never asked to prove a gutter.
    kept: List[dict] = []
    for pr in proposals:
        if not kept:
            kept.append(pr)
            continue

        # A list whose numbers hang out to the left of their text offers two
        # line-start clusters a dozen points apart, and both of them can pass
        # the whitespace tests -- the numbers are narrow enough to leave a
        # channel open over most of the height, and they sit beside their own
        # text at every line they appear on. What gives it away is the width:
        # the strip between the two is too narrow to set a column of prose in,
        # because it was never a column, only an indent. Fuse it back into the
        # column it hangs off, keeping the left edge, which is where the column
        # actually starts.
        if pr["x"] - kept[-1]["x"] < COLUMN_MIN_W:
            host = kept[-1]
            host["marked"] = host["marked"] or pr["marked"]
            host["lines"] = max(host["lines"], pr["lines"])
            host["spans"] = host["spans"] + pr["spans"]
            continue

        boundary = pr["x"] - 4.0
        # The channel has to stand open over most of the height it is judged on.
        # This is asked of every boundary, a lettered one included: it is what
        # stops a second list further down the same column from being read as a
        # column of its own.
        if _column_clearance(blocks, boundary, content_top, content_bottom) < COLUMN_CLEAR:
            continue
        # Bare prose must additionally show both sides carrying content at the
        # same heights. Without it, the indented half of a hanging list, or the
        # second half of a wide table, reads as a column and the page shatters
        # into slivers. A letter is evidence enough to skip this test, since a
        # marker cluster is already a deliberate typographic signal.
        if not pr["marked"]:
            if _side_by_side_share(blocks, boundary, content_top, content_bottom) < COLUMN_PAIRED:
                continue
        kept.append(pr)

    if not kept:
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

    # A column opens where its own first marker stands. One with no marker of
    # its own opens with the page's exercise area -- the latest of the lettered
    # openings -- so that adding it cannot bring a masthead or a title band back
    # to life above the first activity on the sheet.
    marked_opens = [
        max(s.bbox[3] + 2.0 for s in pr["spans"])
        for pr in kept if pr["marked"] and pr["spans"]
    ]
    default_open = max(marked_opens) if marked_opens else content_top

    cols: List[Column] = []
    for idx, pr in enumerate(kept):
        left = pr["x"] - 6.0
        if idx + 1 < len(kept):
            next_left = kept[idx + 1]["x"] - 10.0
        else:
            next_left = content_right + 6.0

        if pr["marked"] and pr["spans"]:
            opens_at = max(s.bbox[3] + 2.0 for s in pr["spans"])
            closes_at = min(s.bbox[1] for s in pr["spans"])
        else:
            opens_at = default_open
            closes_at = content_bottom

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
