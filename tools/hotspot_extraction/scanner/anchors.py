"""
Publisher Anchor Reconciliation & Folio Mapping.

Reconciles detected activity regions with publisher interactive metadata
(activities_meta/<book_id>.json) and printed folio page mappings.
Unmatched publisher anchors are synthesized into anchored ActivityRegion instances
with panel snapping, measure alignment, and de-overlapping.
"""

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .primitives import PagePrimitives, TextSpan
from .layout import (
    PageLayout,
    Column,
    _text_lines,
)
from .regions import (
    ActivityRegion,
    detect_panels,
    rect_overlap,
    rect_area,
    clamp,
    PAD,
)

# Anchor matching thresholds (consistent with interactive-links.js)
LABELLED_DRIFT = 10.0    # % of page height tolerance for label-matched entries
UNLABELLED_DRIFT = 5.0   # % of page height tolerance for unlabelled anchors
WRONG_SIDE_PENALTY = 15.0  # % penalty when icon sits in the opposite margin
ANCHOR_ABOVE = 24.0      # pt: maximum distance an anchor icon can sit above its line


@dataclass
class PublisherOge:
    """An interactive page element from publisher metadata (kitapogeList)."""
    id: str
    title: str
    sayfano: int          # Printed page number
    posx: float           # Publisher anchor x (percentage: 0..100)
    posy: float           # Publisher anchor y (percentage: 0..100)
    data: str             # Activity URL or identifier


def parse_oge_label(title: str, printed_page: Optional[int] = None) -> Optional[str]:
    """
    Extract canonical activity label from publisher title string.

    Strips printed page prefixes (e.g. '42/a' -> 'a'), editorial revision
    suffixes ('-ed', '.ed', 'ed2'), and 'soru' expressions.

    Returns:
        Clean label ('a', 'b', '1', etc.) or None if unlabelled/descriptive.
    """
    if not title:
        return None
    s = str(title).lower().strip()

    # Strip printed page prefix (e.g. '42/a', '42-a', '42.a')
    if printed_page is not None and printed_page > 0:
        s = re.sub(rf"^0*{printed_page}\s*[-/\\.:]?\s*", "", s).strip()
        # In Turkish books, titles sometimes carry 0-indexed page numbers
        s = re.sub(rf"^0*{printed_page - 1}\s*[-/\\.:_]?\s*", "", s).strip()

    s = re.sub(r"^\s*\d{1,4}\s*[-/:]\s*", "", s).strip()

    # Strip editorial revision marks (e.g. '.ed', '-ed', 'ed2', 'ed')
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"[\s._\-]*\bed\s*\d*$", "", s).strip()
        s = re.sub(r"[\s._\-]+$", "", s).strip()

    # Match 'soru' patterns (e.g. '5. Soru' -> '5')
    soru = re.match(r"^(\d{1,2})\s*\.?\s*soru", s, re.IGNORECASE)
    if soru:
        return soru.group(1)

    # Single letter or digit
    if re.fullmatch(r"[a-zçğıöşü]", s) or re.fullmatch(r"\d{1,2}", s):
        return s

    # Page number typed into label without separator (e.g. '247n' for n on p24)
    tail = re.match(r"^\d{1,4}\s*[/\-.:]?\s*([a-zçğıöşü])$", s)
    if tail:
        return tail.group(1)

    return None


def load_publisher_oges(meta_path: str) -> List[PublisherOge]:
    """
    Load and filter valid interactive page elements from activities_meta/<id>.json.

    Only loads interactive elements placed on a specific sheet (ogeturu == 1,
    sayfaustuoge is True, sayfano > 0, and data is non-empty).
    """
    if not os.path.exists(meta_path):
        return []

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []

    items = meta.get("kitapogeList", [])
    valid_oges: List[PublisherOge] = []

    for o in items:
        if (
            o.get("ogeturu") == 1
            and o.get("data")
            and o.get("sayfaustuoge")
            and o.get("sayfano")
        ):
            valid_oges.append(
                PublisherOge(
                    id=str(o["id"]),
                    title=str(o.get("baslik", "")),
                    sayfano=int(o["sayfano"]),
                    posx=float(o.get("posx", 0.0)),
                    posy=float(o.get("posy", 0.0)),
                    data=str(o.get("data", "")),
                )
            )

    return valid_oges


def find_unplaced_anchors(
    activities: List[ActivityRegion],
    oges: List[PublisherOge],
    page_height: float,
    printed_page: int,
    page_width: float = 0.0,
) -> List[PublisherOge]:
    """
    Identify publisher metadata entries on a sheet that do not match any
    detected activity region.

    Matches by label agreement, margin side, and vertical drift.
    """
    page_oges = [o for o in oges if o.sayfano == printed_page]
    if not page_oges:
        return []

    pw = page_width if page_width > 0.0 else 595.0
    pairs: List[Tuple[float, int, int]] = []

    for oi, oge in enumerate(page_oges):
        if oge.posx == 0.0 and oge.posy == 0.0:
            continue

        olabel = parse_oge_label(oge.title, printed_page)
        top = oge.posy
        on_left = oge.posx < 50.0

        for ri, act in enumerate(activities):
            alabel = (act.label or "").lower().strip()
            atop = ((page_height - act.rect[3]) / page_height) * 100.0
            full_width = (act.rect[0] < pw * 0.35) and (act.rect[2] > pw * 0.65)
            act_on_left = act.rect[0] < pw / 2.0

            # Label mismatch rule
            if olabel and alabel and olabel != alabel:
                continue

            drift = abs(top - atop)
            limit = LABELLED_DRIFT if (olabel and alabel) else UNLABELLED_DRIFT
            if drift > limit:
                continue

            side_miss = (not full_width) and (on_left != act_on_left)
            if side_miss and not (olabel and alabel):
                continue

            cost = drift + (WRONG_SIDE_PENALTY if side_miss else 0.0)
            pairs.append((cost, oi, ri))

    # Greedy 1-to-1 matching
    pairs.sort(key=lambda p: p[0])
    used_oge: Set[int] = set()
    used_region: Set[int] = set()

    for cost, oi, ri in pairs:
        if oi in used_oge or ri in used_region:
            continue
        used_oge.add(oi)
        used_region.add(ri)

    # Return unplaced oges that have valid page coordinates
    unplaced: List[PublisherOge] = []
    for oi, oge in enumerate(page_oges):
        if oi not in used_oge and (oge.posx != 0.0 or oge.posy != 0.0):
            unplaced.append(oge)

    return unplaced


def _create_anchored_region(
    oge: PublisherOge,
    page_num: int,
    page_width: float,
    page_height: float,
    primitives: Optional[PagePrimitives] = None,
    layout: Optional[PageLayout] = None,
    existing_activities: Optional[List[ActivityRegion]] = None,
) -> ActivityRegion:
    """
    Synthesize an ActivityRegion for an unmatched publisher anchor.

    Snaps to enclosing drawn vector panels (e.g. 'Let's Discover' tinted cards)
    or column channels, and extracts headline text from the nearest line.
    """
    pw = page_width if page_width > 0.0 else 595.0
    ph = page_height if page_height > 0.0 else 842.0

    ax = (oge.posx / 100.0) * pw
    ay = ph - (oge.posy / 100.0) * ph
    col_idx = 0 if oge.posx < 50.0 else 1

    content_box = layout.content_box if layout else (0.0, 44.0, pw, ph - 40.0)
    top_limit = content_box[3]
    bottom_limit = content_box[1]

    headline = None
    rect: Tuple[float, float, float, float]

    if primitives and layout:
        body_spans = [
            s for s in primitives.spans
            if s.bbox[1] >= bottom_limit - 2.0 and s.bbox[3] <= top_limit + 2.0
        ]
        panels = detect_panels(primitives.drawings, body_spans)
        lines = _text_lines(body_spans)

        # Find line nearest to anchor (at or below ay)
        best_line = None
        best_cost = float("inf")
        for line in lines:
            line_y = (line["y0"] + line["y1"]) / 2.0
            below = ay - line_y
            if below < -ANCHOR_ABOVE:
                continue
            across = abs(line["x0"] - ax) / pw
            cost = max(below, 0.0) + across * pw * 0.25
            if cost < best_cost:
                best_cost = cost
                best_line = line

        # Check if anchor or best line falls inside a drawn panel
        snapped_panel = None
        for p in panels:
            # Check if anchor point is inside or best line overlaps
            in_anchor = (p[0] - 5.0 <= ax <= p[2] + 5.0) and (p[1] - 5.0 <= ay <= p[3] + 5.0)
            in_line = False
            if best_line:
                best_line_y = (best_line["y0"] + best_line["y1"]) / 2.0
                in_line = (
                    p[0] - 5.0 <= best_line["x0"] <= p[2] + 5.0
                    and p[1] - 5.0 <= best_line_y <= p[3] + 5.0
                )
            if in_anchor or in_line:
                snapped_panel = p
                break

        if best_line:
            # Build headline from line items
            txts = [s.text.strip() for s in best_line["spans"] if s.text.strip()]
            if txts:
                headline = " ".join(txts)

        if snapped_panel:
            p_x0, p_y0, p_x1, p_y1 = snapped_panel
            # If headline sits directly above panel (e.g. 'Let's Discover' title), include it
            top_bound = max(p_y1, (best_line["y1"] + PAD) if best_line else p_y1)
            rect = (
                clamp(p_x0 - PAD, 0.0, pw),
                clamp(p_y0 - PAD, bottom_limit - PAD, ph),
                clamp(p_x1 + PAD, 0.0, pw),
                clamp(top_bound + PAD, 0.0, top_limit + PAD),
            )
        else:
            col_target = next((c for c in layout.columns if c.index == col_idx), None)
            if col_target:
                x0 = clamp(col_target.x0 - PAD, 0.0, pw)
                x1 = clamp(col_target.x1 + PAD, 0.0, pw)
            else:
                x0 = 40.0 if col_idx == 0 else pw / 2.0
                x1 = pw / 2.0 if col_idx == 0 else pw - 40.0

            y_top = min(top_limit, ay + 15.0)
            y_bot = max(bottom_limit, ay - 80.0)
            rect = (x0, y_bot, x1, y_top)
    else:
        # Fallback geometric bounds
        x0 = 40.0 if col_idx == 0 else pw / 2.0
        x1 = pw / 2.0 if col_idx == 0 else pw - 40.0
        y_top = min(top_limit, ay + 15.0)
        y_bot = max(bottom_limit, ay - 80.0)
        rect = (x0, y_bot, x1, y_top)

    if not headline:
        headline = oge.title

    act_id = f"p{page_num}-oge-{oge.id}"
    rounded_rect = (
        round(rect[0], 4),
        round(rect[1], 4),
        round(rect[2], 4),
        round(rect[3], 4),
    )

    return ActivityRegion(
        id=act_id,
        label=None,
        column=col_idx,
        rect=rounded_rect,
        parts=[rounded_rect],
        headline=headline,
        items=None,
        anchored=True,
    )


def reconcile_anchors(
    activities: List[ActivityRegion],
    oges: List[PublisherOge],
    page_height: float,
    printed_page: int,
    page_width: float = 0.0,
    page_num: Optional[int] = None,
    primitives: Optional[PagePrimitives] = None,
    layout: Optional[PageLayout] = None,
    markers: Optional[List[Any]] = None,
) -> List[ActivityRegion]:
    """
    Reconcile publisher anchors with detected activities for a page.

    Matches existing activities to publisher metadata. Any unmatched anchor
    creates a new anchored ActivityRegion entry (anchored = True).

    Args:
        activities: List of detected ActivityRegion instances on this page.
        oges: List of PublisherOge metadata entries for the document.
        page_height: Height of the PDF page in points.
        printed_page: Printed folio page number corresponding to this sheet.
        page_width: Optional page width in points.
        page_num: Optional 1-based PDF sheet number.
        primitives: Optional PagePrimitives for panel snapping and text lines.
        layout: Optional PageLayout for column boundary alignment.
        markers: Optional list of DetectedMarker from layout analysis.

    Returns:
        Updated List[ActivityRegion] containing both detected lettered activities
        and synthesized anchored activities with clean separation.
    """
    pw = page_width
    if pw <= 0.0 and primitives:
        pw = primitives.width
    elif pw <= 0.0 and activities:
        pw = max(a.rect[2] for a in activities) + 40.0
    elif pw <= 0.0:
        pw = 595.0

    p_num = page_num
    if p_num is None and activities:
        # Extract page number from first activity id: 'p34-a' -> 34
        m = re.match(r"^p(\d+)-", activities[0].id)
        if m:
            p_num = int(m.group(1))
    if p_num is None:
        p_num = printed_page

    unplaced = find_unplaced_anchors(
        activities=activities,
        oges=oges,
        page_height=page_height,
        printed_page=printed_page,
        page_width=pw,
    )

    if not unplaced:
        return list(activities)

    # If primitives and layout are available, regrow with anchor markers for exact separation
    if primitives and layout:
        from .markers import DetectedMarker, detect_markers
        from .regions import grow_activity_regions

        existing_markers = markers
        if existing_markers is None:
            existing_markers = detect_markers(primitives, layout=layout)

        anchor_markers = []
        for oge in unplaced:
            ax = (oge.posx / 100.0) * pw
            ay = page_height - (oge.posy / 100.0) * page_height
            col_idx = 0 if oge.posx < 50.0 else 1

            candidate_spans = [
                s for s in primitives.spans
                if abs((s.bbox[1] + s.bbox[3]) / 2.0 - ay) < 35.0
                and (s.bbox[0] < pw / 2.0 if col_idx == 0 else s.bbox[0] >= pw / 2.0)
            ]
            candidate_spans.sort(key=lambda s: abs((s.bbox[1] + s.bbox[3]) / 2.0 - ay))

            best_span = (
                candidate_spans[0]
                if candidate_spans
                else TextSpan(
                    text=oge.title,
                    bbox=(ax, ay, ax + 10.0, ay + 10.0),
                    font="",
                    size=10.0,
                    flags=0,
                    color=0,
                )
            )

            anchor_markers.append(
                DetectedMarker(
                    label=f"oge-{oge.id}",
                    normalized_value=999.0,
                    span=best_span,
                    column_index=col_idx,
                    is_step=False,
                    items=[],
                )
            )

        combined_markers = list(existing_markers) + anchor_markers
        by_col: Dict[int, List[Any]] = {c.index: [] for c in layout.columns}
        for m in combined_markers:
            col_target = m.column_index if m.column_index in by_col else 0
            by_col[col_target].append(m)

        for c in layout.columns:
            by_col[c.index].sort(key=lambda m: -(m.span.bbox[1] + m.span.bbox[3]) / 2.0)

        ordered_markers = []
        for c in sorted(layout.columns, key=lambda col: col.x0):
            ordered_markers.extend(by_col[c.index])

        regrown = grow_activity_regions(primitives, layout, ordered_markers)
        for act in regrown:
            if "oge-" in act.id:
                act.label = None
                act.anchored = True

        return regrown

    # Fallback when primitives are not available
    reconciled = list(activities)
    for oge in unplaced:
        anchored_act = _create_anchored_region(
            oge=oge,
            page_num=p_num,
            page_width=pw,
            page_height=page_height,
            primitives=primitives,
            layout=layout,
            existing_activities=reconciled,
        )
        reconciled.append(anchored_act)

    return reconciled
