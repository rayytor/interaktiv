# Phase 2: Layout Segmentation & Typographic Marker Detection

## Objective
Implement column segmentation and typographic marker detection in Python. This phase consumes `PagePrimitives` from Phase 1 and identifies:
1. Document typographic hierarchy (modal body size vs heading/marker sizes).
2. Content layout (header/footer exclusion, column channels).
3. Activity markers (`a, b, c...`, Turkish letters `ç, ğ, ı, ö, ş, ü`, step markers `1. Adım`, numeric markers).
4. Sub-question items (`1, 2, 3...` nested inside activities).

---

## Context & Background
In `js/activities.js`:
- Markers are single-character or step items (`a`, `b`, `c`, `1. Adım`, `1`) that initiate an activity.
- The legacy scanner relies on `calibrate()` to search for specific InDesign internal font names (like `g_d0_f8`) by sampling pages and looking for alphabetical runs.
- InDesign font names can vary or change between PDF exports, making font ID matching fragile.
- In this Python implementation, we introduce **typographic feature clustering**:
  - Detect the **modal body text size** for the page/document (typically 10 pt).
  - Identify candidate markers by size ratio, font weight (`flags & 16` for bold), and left alignment within column gutters.
  - State machine validates that candidate markers form legitimate monotonic sequences (`a -> b -> c` or `1 -> 2 -> 3`).

---

## Reference Code to Inspect
- `js/activities.js`:
  - Lines 18–65: `LABEL_RE`, `STEP_RE`, `LABEL_VALUES` (including Turkish letters `ç: 3.5`, `ğ: 7.5`, `ı: 8.5`, `ö: 15.5`, `ş: 19.5`, `ü: 21.5`), `LABEL_SUCCESSORS`.
  - Lines 67–79: Constants (`COLUMN_TOLERANCE`, `HEADER_BAND`, `FOOTER_BAND`, `PAD`, `GUTTER_RATIO`, etc.).
  - Lines 80–121: Calibration rules and scoring.
  - Lines 164–170 (in `ACTIVITY_DETECTION_PLAN.md`): Sub-question detection rules.
  - `tools/hotspot_extraction/tests/dump_runs.mjs`: Utility showing how markers were evaluated.

---

## Detailed Tasks

### 1. Create Layout & Marker Modules
Create:
- `tools/hotspot_extraction/scanner/layout.py`
- `tools/hotspot_extraction/scanner/markers.py`

### 2. Implement `layout.py`
```python
from dataclasses import dataclass
from typing import List, Tuple
from .primitives import PagePrimitives, TextSpan

@dataclass
class Column:
    index: int
    x0: float
    x1: float
    opens_at: float
    closes_at: float

@dataclass
class PageLayout:
    content_box: Tuple[float, float, float, float]  # (x0, y0, x1, y1) excluding header/footer
    columns: List[Column]
    body_font_size: float
```
- **Header & Footer Exclusion**:
  - Running headers (top 40–45 pt) and footers/page numbers (bottom 40–45 pt) must be excluded from activity analysis.
  - Printed page numbers (folios) should be detected in the footer/header for the folio map.
- **Column Detection (XY-Cut / Projection)**:
  - Cluster text spans horizontally or use vertical projection profiles to detect column boundaries.
  - Standard textbook layouts have 1 or 2 columns (e.g. left column ~50–65 pt, right column ~280–300 pt).
  - Account for full-width sections spanning both columns.

### 3. Implement `markers.py`
```python
from dataclasses import dataclass
from typing import List, Optional, Tuple
from .primitives import TextSpan

@dataclass
class DetectedMarker:
    label: str                   # e.g. "a", "b", "1. Adım", "1"
    normalized_value: float      # Numeric sort order (a=1, b=2, ç=3.5, etc.)
    span: TextSpan
    column_index: int
    is_step: bool                # True for "1. Adım"
    items: List['SubQuestion']   # Nested questions (1, 2, 3...)

@dataclass
class SubQuestion:
    label: str                   # "1", "2", etc.
    number: int
    span: TextSpan
```

#### Turkish Alphabet & Value Mapping
Port the `LABEL_VALUES` and `LABEL_SUCCESSORS` logic from `js/activities.js`:
- Standard letters: `a=1, b=2, c=3, d=4...`
- Turkish insertions: `ç=3.5, ğ=7.5, ı=8.5, ö=15.5, ş=19.5, ü=21.5`.
- Regular expressions for labels:
  - Single letter or number: `^\(?\s*([a-zçğıöşü]|\d{1,2})\s*[.):\]]?\s*\)?$` (case-insensitive, accounting for Turkish dotless `ı`).
  - Step marker: `^\(?\s*(\d{1,2})\s*[.):\]]?\s*\)?\s*[Aa][Dd][ıiIİ][Mm]\s*[.:]?\s*$`.
- Sequence validation:
  - Markers within a column (or across columns in reading order) must form an ascending sequence.
  - Discard rogue letters (e.g. single letters appearing inside matching exercises or equations).

#### Sub-Question Detection
- Inside the scope of an activity, detect standalone numbers `1`, `2`, `3` that form a consecutive sequence.
- Distinguish sub-question numbers from inline dates (e.g. `1993`), footnote references, or math values.

### 4. Implement Test & Inspection Script
Create: `tools/hotspot_extraction/scanner/test_markers.py`
- Runs layout and marker detection on:
  - `books/0e966773-5012-4f57-8be5-d892e8c75f22.pdf`
  - `books/51cdbbce-66f4-4baa-95d7-634bf11b7e44.pdf` (matematik)
- Prints detected columns, markers (`a`, `b`, `c`), and nested sub-questions per page.
- Validates sequence monotonicity and absence of false positives.

---

## Deliverables
1. `tools/hotspot_extraction/scanner/layout.py`
2. `tools/hotspot_extraction/scanner/markers.py`
3. `tools/hotspot_extraction/scanner/test_markers.py`

---

## Verification & Acceptance Criteria
Run:
```bash
python3 tools/hotspot_extraction/scanner/test_markers.py
```
- [ ] Columns are accurately detected on 1-column and 2-column pages.
- [ ] Known activity markers (e.g. page 33 of `0e966773`: `a, b, c, d` on left column, `e, f, g` on right column) are detected with 100% precision.
- [ ] Turkish step markers (`1. Adım`, `2. Adım`) are recognized.
- [ ] Sub-questions (`1, 2, 3...`) nested under an activity are correctly identified.

---

## Handoff Checklist for Next Agent
- `DetectedMarker` objects with associated spans and column indices are ready for Phase 3 (Region Growth).
- Header/footer bands and content boundaries are available for clipping.
