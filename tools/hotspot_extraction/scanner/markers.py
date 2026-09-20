"""
Typographic Marker & Nested Sub-Question Detection.

Detects activity markers (e.g. 'a', 'b', '1.', '1. Adım') across Turkish and Latin
alphabets using typographic feature clustering, gutter clearance, hanging indent
verification, and a monotonic sequence state machine.
"""

from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional, Tuple

from .layout import (
    COLUMN_TOLERANCE,
    Column,
    PageLayout,
    detect_layout,
)
from .primitives import PagePrimitives, TextSpan

# Gutter & indent constants
GUTTER_RATIO = 0.5       # a label needs this much of its own size clear on its left
HANGING_INDENT = 26.0    # pt: how far the instruction text sits to the right
MAX_SUBQUESTION_GAP = 4.0  # pt: horizontal alignment tolerance for sub-questions

# Regex patterns for activity markers and step markers
LABEL_RE = re.compile(
    r"^\(?\s*([a-zA-ZçğıöşüÇĞIİÖŞÜ]|\d{1,2})\s*[.):\]]?\s*\)?$",
    re.UNICODE,
)
STEP_RE = re.compile(
    r"^\(?\s*(\d{1,2})\s*[.):\]]?\s*\)?\s*[Aa][Dd][ıiIİ][Mm]\s*[.:]?\s*$",
    re.UNICODE,
)
NUMBER_RE = re.compile(r"^\(?\s*(\d{1,2})\s*[.)]?\s*\)?$")


@dataclass
class SubQuestion:
    """A nested question (e.g. '1', '2', '3') within an activity."""
    label: str                   # "1", "2", etc.
    number: int
    span: TextSpan


@dataclass
class DetectedMarker:
    """A detected activity marker initiating an activity region."""
    label: str                   # e.g. "a", "b", "1. Adım", "1"
    normalized_value: float      # Numeric sort order (a=1, b=2, ç=3.5, etc.)
    span: TextSpan
    column_index: int
    is_step: bool                # True for "1. Adım"
    items: List[SubQuestion] = field(default_factory=list)


@dataclass
class ParsedLabel:
    """Intermediate parsed label representation."""
    kind: str                    # "letter", "digit", or "step"
    value: float                 # Normalized sort value
    text: str                    # Cleaned label string
    is_step: bool


def normalize_turkish_char(ch: str) -> str:
    """Normalize Turkish characters handling dotted/dotless I and diacritics."""
    ch = ch.strip()
    if ch == "İ":
        return "i"
    if ch == "I":
        return "ı"
    ch = ch.lower()
    if ch == "i\u0307":
        return "i"
    return ch


def _build_label_values() -> Dict[str, float]:
    """Build value mapping for Latin and Turkish letters."""
    values: Dict[str, float] = {}
    for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz", 1):
        values[ch] = float(i)
    values["ç"] = 3.5
    values["ğ"] = 7.5
    values["ı"] = 8.5
    values["ö"] = 15.5
    values["ş"] = 19.5
    values["ü"] = 21.5
    return values


LABEL_VALUES = _build_label_values()


def _build_label_successors() -> Dict[float, List[float]]:
    """Build legal successors map for alphabet runs."""
    successors: Dict[float, List[float]] = {}
    for n in range(1, 101):
        successors[float(n)] = [float(n + 1)]

    for v in LABEL_VALUES.values():
        nexts = [v2 for v2 in LABEL_VALUES.values() if v2 > v and v2 - v <= 1.0]
        successors[v] = nexts
    return successors


LABEL_SUCCESSORS = _build_label_successors()


def parse_label(text: str) -> Optional[ParsedLabel]:
    """Parse text into a structured label if it matches marker grammar."""
    raw = text.strip()
    if not raw:
        return None

    # 1. Step marker check (e.g. "1. Adım", "2. adım:")
    m_step = STEP_RE.match(raw)
    if m_step:
        val = int(m_step.group(1))
        if val >= 1:
            return ParsedLabel(
                kind="step",
                value=float(val),
                text=raw,
                is_step=True,
            )

    # 2. Single letter or number check (e.g. "a", "b)", "1.", "1")
    m_lbl = LABEL_RE.match(raw)
    if m_lbl:
        tok = m_lbl.group(1)
        if tok.isdigit():
            val = int(tok)
            if val >= 1:
                return ParsedLabel(
                    kind="digit",
                    value=float(val),
                    text=raw,
                    is_step=False,
                )
        else:
            ch = normalize_turkish_char(tok)
            val = LABEL_VALUES.get(ch, float(ord(ch) - 96) if len(ch) == 1 else 0.0)
            if val > 0:
                return ParsedLabel(
                    kind="letter",
                    value=val,
                    text=raw,
                    is_step=False,
                )

    return None


def check_hanging_indent(
    span: TextSpan,
    spans: List[TextSpan],
) -> Tuple[bool, bool]:
    """
    Check if a span stands clear in its gutter and has following body text at a
    hanging indent.

    Returns:
        (clear_left, has_hanging_indent)
    """
    nearest_left = -float("inf")
    right: List[TextSpan] = []
    y_mid = (span.bbox[1] + span.bbox[3]) / 2.0

    for other in spans:
        if other is span:
            continue
        other_y_mid = (other.bbox[1] + other.bbox[3]) / 2.0
        # Check if they share the same line baseline (within 2.5 pt)
        if abs(other_y_mid - y_mid) >= 2.5:
            continue

        if other.bbox[0] < span.bbox[0] - 0.5:
            if other.bbox[2] > span.bbox[0] + 0.5:
                # Genuinely overlapping on the left: not a clear label
                return False, False
            nearest_left = max(nearest_left, other.bbox[2])
        elif other.bbox[0] > span.bbox[2]:
            right.append(other)
        elif other.bbox[2] > span.bbox[2] + 0.5:
            right.append(other)

    clear_left = (span.bbox[0] - nearest_left) >= span.size * GUTTER_RATIO

    has_body_right = False
    cursor = span.bbox[2]
    right.sort(key=lambda s: s.bbox[0])

    for other in right:
        if other.bbox[0] - cursor > HANGING_INDENT:
            break
        otxt = other.text.strip()
        # Non-empty word or substantial advance indicates body text
        if otxt and (re.search(r"[\w]", otxt, re.UNICODE) or (other.bbox[2] - other.bbox[0] >= 2 * span.size)):
            has_body_right = True
            break
        cursor = max(cursor, other.bbox[2])

    return clear_left, (clear_left and has_body_right)


def _is_bold(span: TextSpan) -> bool:
    """Determine if a text span is bold."""
    if span.flags & 16:
        return True
    font_lower = span.font.lower()
    return any(w in font_lower for w in ("bold", "semibold", "heavy", "black", "demi"))


def find_candidate_markers(
    body_spans: List[TextSpan],
    body_font_size: float,
) -> List[dict]:
    """
    Identify potential activity markers by regex match, gutter clearance,
    hanging indent, and typographic features.
    """
    candidates = []
    for span in body_spans:
        parsed = parse_label(span.text)
        if not parsed:
            continue

        clear_left, hanging = check_hanging_indent(span, body_spans)
        if not hanging:
            continue

        # Typographic hierarchy check:
        # Step markers ("1. Adım") are always accepted.
        # Otherwise, candidate markers should have size >= body size (within 0.5 pt)
        # or be bold (e.g. MyriadPro-Bold in math books).
        is_bold_weight = _is_bold(span)
        size_matches = span.size >= body_font_size - 0.5

        if parsed.is_step or is_bold_weight or size_matches:
            candidates.append({
                "span": span,
                "label": parsed,
                "x": span.bbox[0],
                "y": (span.bbox[1] + span.bbox[3]) / 2.0,
            })

    # Filter out smaller candidates in the same typeface when a larger candidate exists
    font_max_size: Dict[str, float] = {}
    for c in candidates:
        s = c["span"]
        if c["label"].is_step or _is_bold(s):
            continue
        font_max_size[s.font] = max(font_max_size.get(s.font, 0.0), s.size)

    filtered = []
    for c in candidates:
        s = c["span"]
        if c["label"].is_step or _is_bold(s):
            filtered.append(c)
            continue
        if s.size >= font_max_size.get(s.font, 0.0) - 0.75:
            filtered.append(c)

    return filtered


def longest_label_run(ordered: List[dict]) -> List[dict]:
    """
    Find the longest valid monotonic sequence of labels using dynamic programming.

    Tolerates gaps with minor penalties, allows restarts at value 1.0, and
    eliminates isolated rogue symbols.
    """
    n = len(ordered)
    if not n:
        return []

    # If multiple kinds exist (e.g. step markers and letter markers), process separately
    kinds = list(dict.fromkeys(e["label"].kind for e in ordered))
    if len(kinds) > 1:
        runs = [longest_label_run([e for e in ordered if e["label"].kind == k]) for k in kinds]
        runs = [r for r in runs if len(r) > 1 or (len(r) == 1 and (r[0]["label"].value == 1.0 or r[0]["label"].is_step))]
        if runs:
            kept_ids = {id(e) for r in runs for e in r}
            return [e for e in ordered if id(e) in kept_ids]

    if n == 1:
        # Single marker is kept only if it starts a sequence or is a step marker
        single = ordered[0]
        if single["label"].value == 1.0 or single["label"].is_step:
            return [single]
        return []

    best = [1.0] * n
    prev_idx = [-1] * n

    for i in range(n):
        val = ordered[i]["label"].value
        restart = (val == 1.0)
        for j in range(i):
            prev_val = ordered[j]["label"].value
            ascends = (ordered[j]["label"].kind == ordered[i]["label"].kind and prev_val < val)
            if not ascends and not restart:
                continue

            penalty = max(0.0, (val - prev_val - 1.0) * 0.01) if ascends else 0.0
            score = best[j] + 1.0 - penalty
            if score > best[i]:
                best[i] = score
                prev_idx[i] = j

    tail = max(range(n), key=lambda i: best[i])
    if best[tail] < 2.0 and not (ordered[tail]["label"].value == 1.0 or ordered[tail]["label"].is_step):
        return []

    out = []
    curr = tail
    while curr != -1:
        out.append(ordered[curr])
        curr = prev_idx[curr]
    return list(reversed(out))


def detect_subquestions(
    act_span: TextSpan,
    next_act_span: Optional[TextSpan],
    body_spans: List[TextSpan],
    col_x0: float,
    col_x1: float,
    bottom_limit: float = 44.0,
) -> List[SubQuestion]:
    """
    Detect consecutive numbered sub-questions ('1', '2', '3'...) within the
    scope of an activity.
    """
    top = act_span.bbox[1]
    bottom = next_act_span.bbox[3] if next_act_span else bottom_limit

    candidates: List[Tuple[int, TextSpan]] = []
    for s in body_spans:
        if s is act_span or (next_act_span and s is next_act_span):
            continue

        y_mid = (s.bbox[1] + s.bbox[3]) / 2.0
        if not (bottom <= y_mid <= top and col_x0 <= s.bbox[0] <= col_x1):
            continue

        txt = s.text.strip()
        m = NUMBER_RE.match(txt)
        if not m:
            continue

        num = int(m.group(1))
        if num < 1 or num > 99:
            continue

        # Verify clear left gutter so we don't pick up mid-line numbers
        clear_left, hanging = check_hanging_indent(s, body_spans)
        if clear_left:
            candidates.append((num, s))

    if not candidates:
        return []

    # Group candidates by horizontal alignment x0
    by_x: Dict[float, List[Tuple[int, TextSpan]]] = {}
    for num, s in candidates:
        matched_group = None
        for gx in by_x:
            if abs(s.bbox[0] - gx) <= MAX_SUBQUESTION_GAP:
                matched_group = gx
                break
        if matched_group is None:
            matched_group = s.bbox[0]
            by_x[matched_group] = []
        by_x[matched_group].append((num, s))

    best_seq: List[Tuple[int, TextSpan]] = []
    for gx, group in by_x.items():
        # Sort top to bottom
        group.sort(key=lambda it: -it[1].bbox[1])
        seq: List[Tuple[int, TextSpan]] = []
        expected = 1
        for num, s in group:
            if num == expected:
                seq.append((num, s))
                expected += 1
            elif num == 1 and len(seq) == 0:
                seq.append((num, s))
                expected = 2

        if len(seq) >= 2 and len(seq) > len(best_seq):
            best_seq = seq

    return [
        SubQuestion(
            label=str(num),
            number=num,
            span=s,
        )
        for num, s in best_seq
    ]


def detect_markers(
    primitives: PagePrimitives,
    layout: Optional[PageLayout] = None,
) -> List[DetectedMarker]:
    """
    Detect all activity markers on a page and their nested sub-questions.

    Args:
        primitives: PagePrimitives containing extracted text spans.
        layout: Optional pre-computed PageLayout. If None, layout is computed.

    Returns:
        List of DetectedMarker instances sorted in reading order.
    """
    # 1. Collect candidate spans in content area
    content_top = primitives.height - 40.0
    content_bottom = 44.0
    body_spans = [
        s for s in primitives.spans
        if s.bbox[1] >= content_bottom - 2.0 and s.bbox[3] <= content_top + 2.0
    ]

    # Approximate body font size for initial candidate filtering
    if layout is None:
        temp_box = (0.0, content_bottom, primitives.width, content_top)
        from .layout import detect_body_font_size
        body_size = detect_body_font_size(body_spans, temp_box)
    else:
        body_size = layout.body_font_size

    candidates = find_candidate_markers(body_spans, body_size)
    candidate_spans = [c["span"] for c in candidates]

    # 2. Resolve layout with candidate markers
    if layout is None:
        layout = detect_layout(primitives, candidate_markers=candidate_spans)

    # 3. Filter candidate markers to column leaders (excluding indented/nested sub-items)
    from .layout import _column_clearance, COLUMN_CLEAR
    sorted_c = sorted(candidates, key=lambda c: c["x"])
    clusters: List[List[dict]] = []
    curr: List[dict] = []
    anchor: Optional[float] = None
    for c in sorted_c:
        if anchor is None or abs(c["x"] - anchor) <= COLUMN_TOLERANCE:
            if anchor is None:
                anchor = c["x"]
            curr.append(c)
        else:
            clusters.append(curr)
            curr = [c]
            anchor = c["x"]
    if curr:
        clusters.append(curr)

    leaders: List[List[dict]] = []
    col_left = -float("inf")
    blocks = [{"x0": s.bbox[0], "x1": s.bbox[2], "y0": s.bbox[1], "y1": s.bbox[3]} for s in body_spans]
    for cl in clusters:
        left = min(c["x"] for c in cl)
        own = [b for b in blocks if b["x0"] >= col_left - 4.0]
        clearance = _column_clearance(own, left - 4.0, content_top, content_bottom) if leaders else 1.0
        if clearance >= COLUMN_CLEAR:
            leaders.append(cl)
            col_left = left

    leader_span_ids = {id(c["span"]) for cl in leaders for c in cl}
    top_level_candidates = [c for c in candidates if id(c["span"]) in leader_span_ids]

    # 4. Assign top-level candidates to columns
    col_candidates: Dict[int, List[dict]] = {c.index: [] for c in layout.columns}
    for c in top_level_candidates:
        cx = c["span"].bbox[0]
        best_col = None
        best_dist = float("inf")
        for col in layout.columns:
            if col.x0 <= cx <= col.x1:
                best_col = col.index
                break
            dist = min(abs(cx - col.x0), abs(cx - col.x1))
            if dist < best_dist:
                best_dist = dist
                best_col = col.index
        if best_col is not None:
            c["column_index"] = best_col
            col_candidates[best_col].append(c)

    # 5. Sort in reading order: column by column, top to bottom within each column
    ordered_candidates = []
    for col in sorted(layout.columns, key=lambda c: c.x0):
        c_list = col_candidates.get(col.index, [])
        c_list.sort(key=lambda it: -it["y"])
        ordered_candidates.extend(c_list)

    # 6. Validate sequence monotonicity (DP state machine)
    kept_entries = longest_label_run(ordered_candidates)
    if not kept_entries:
        return []

    # 7. Build DetectedMarker objects and extract nested sub-questions
    markers_by_col: Dict[int, List[dict]] = {c.index: [] for c in layout.columns}
    for entry in kept_entries:
        markers_by_col[entry["column_index"]].append(entry)

    detected_markers: List[DetectedMarker] = []
    for col in layout.columns:
        col_markers = markers_by_col.get(col.index, [])
        for i, entry in enumerate(col_markers):
            span = entry["span"]
            label_info = entry["label"]
            next_entry = col_markers[i + 1] if i + 1 < len(col_markers) else None
            next_span = next_entry["span"] if next_entry else None

            # Sub-question items
            sub_items = detect_subquestions(
                act_span=span,
                next_act_span=next_span,
                body_spans=body_spans,
                col_x0=col.x0,
                col_x1=col.x1,
                bottom_limit=content_bottom,
            )

            detected_markers.append(
                DetectedMarker(
                    label=label_info.text,
                    normalized_value=label_info.value,
                    span=span,
                    column_index=col.index,
                    is_step=label_info.is_step,
                    items=sub_items,
                )
            )

    return detected_markers
