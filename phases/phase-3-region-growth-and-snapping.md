# Phase 3: Region Growth, Vector Panel Snapping & Solution Spaces

## Objective
Implement region expansion and boundary snapping in Python. This phase takes the markers detected in Phase 2 and grows their bounding boxes to cover:
1. Instruction text and body lines following the marker.
2. Drawn vector background panels (dialogue bubbles, tinted exercise cards, reading boxes).
3. Solution spaces (ruled lines, empty table cells, answer boxes).
4. Sub-question bounding boxes.
5. De-overlapping and padding rules to ensure clean, non-colliding clickable hotspots.

---

## Context & Background
In `js/activities.js`:
- A naive bounding box ("everything between marker N and marker N+1") is incorrect. For example, on page 33, activity `a` has two lines of instruction above a dialogue in a speech bubble spanning across both columns.
- The region must cover the instruction **and** the entire speech bubble, but must not swallow unrelated photographs, section titles, or adjacent activities.
- **Drawn Panels (`textPanels`)**: If an activity encloses part of a drawn panel or speech bubble, it must snap to the **entire** panel frame ("all-or-nothing" rule). Slicing a panel in half is considered a rule violation in `tests/scorecard.mjs` (`panelsCut`).
- **Solution Space (`solutionBlocks`)**: Workbooks include ruled lines or table cells for students to write answers. The region must include these solution spaces.
- **Overlaps & Padding**: Hotspots must be separated by `HOTSPOT_GAP = 3 pt` and padded by `PAD = 8 pt`. Any rect smaller than `MIN_HOTSPOT = 6 pt` is dropped.

---

## Reference Code to Inspect
- `js/activities.js`:
  - Lines 122–135: Constants for panels and solution rules (`PANEL_MIN_W`, `PANEL_MIN_H`, `RULE_THICK`, `RULE_MIN`, `RULE_ROW`, etc.).
  - Lines 340–416: `separateRects()` (pushing apart overlapping rects).
  - Lines 119–163 (in `ACTIVITY_DETECTION_PLAN.md`): Step 2.6 "Grow the region (the part that matters)".
  - Lines 2500–2600: Panel absorption and clipping logic.
- `tools/hotspot_extraction/tests/cutprobe.mjs`: Details on how panel cuts are measured.
- `tools/hotspot_extraction/tests/scorecard.mjs`: Lines 80–100 (`cuts()` function and `WHOLE_TOL = 0.05`).

---

## Detailed Tasks

### 1. Create Region Growth Module
Create: `tools/hotspot_extraction/scanner/regions.py`

### 2. Implement Data Structures
```python
from dataclasses import dataclass
from typing import List, Optional, Tuple

@dataclass
class SubItemRect:
    id: str
    label: Optional[str]
    number: Optional[int]
    part_index: int
    rect: Tuple[float, float, float, float]  # [x0, y0, x1, y1] in PDF points
    text: Optional[str] = None

@dataclass
class ActivityRegion:
    id: str                                  # e.g. "p33-a"
    label: Optional[str]                     # e.g. "a"
    column: int                              # Column index (0, 1)
    rect: Tuple[float, float, float, float]  # Primary hotspot rect [x0, y0, x1, y1]
    parts: List[Tuple[float, float, float, float]] # Multi-column/split rects
    headline: Optional[str] = None           # First line of instruction
    items: Optional[List[SubItemRect]] = None# Numbered sub-questions
    anchored: bool = False
```

### 3. Implement Panel Detection & Snapping (`detect_panels`)
- Use `VectorDrawing` from Phase 1.
- Identify rectangular or rounded path fills:
  - `width >= PANEL_MIN_W` (60 pt) and `height >= PANEL_MIN_H` (30 pt).
  - Contains at least 2 lines of text.
- **Snapping Rule**: If an activity's text flow enters a drawn panel, expand the activity bounding box to cover the entire panel rectangle.

### 4. Implement Solution Space Detection (`detect_solution_spaces`)
- Detect ruled lines:
  - Vector paths with stroke width `<= RULE_THICK` (2.5 pt) and length `>= RULE_MIN` (20 pt).
  - Clustered vertically (e.g. 3–5 horizontal ruled lines for student answers).
- Detect empty table grids / answer boxes:
  - Rectangular outlines that contain no prose text.
- Absorb solution blocks located directly beneath an activity or its sub-items.

### 5. Implement Flow Growth & Clipping (`grow_activity_region`)
- **Measure & Continuity**:
  - Starting from the marker span, absorb subsequent text lines that match the reading measure and leading distance (`FLOW_LEADING = 1.8 * line_height`).
  - Stop at headings (`font_size >= 1.15 * body_size`), page breaks, or subsequent activity markers.
  - Extract `headline`: Concatenate text items on the marker's baseline and immediately following lines up to the first blank gap.
- **Sub-Questions**:
  - For each sub-item (`1, 2, 3...`), assign a bounding box starting from the sub-item marker to the next sub-item (or end of activity).
- **Clipping & Padding**:
  - Clip to page content box (inside header/footer margins).
  - Add `PAD = 8 pt` breathing room around text (unless bounded by a drawn panel edge).

### 6. Implement De-overlapping (`separate_rects`)
- Port `separateRects()` from `js/activities.js` (lines 340–416):
  - Find overlapping pairs of activity rects.
  - Adjust boundaries so rects maintain at least `HOTSPOT_GAP = 3 pt` of clear space.
  - Drop degenerate rects where `width < MIN_HOTSPOT` or `height < MIN_HOTSPOT` (6 pt).

### 7. Implement Region Visualizer & Test Script
Create: `tools/hotspot_extraction/scanner/test_regions.py`
- Tests region growth on `books/0e966773-5012-4f57-8be5-d892e8c75f22.pdf` (pages 33, 34, 36, 164).
- Checks for panel cuts against vector drawings (using the `cuts()` formula from `scorecard.mjs`).
- Asserts that no two hotspots overlap.

---

## Deliverables
1. `tools/hotspot_extraction/scanner/regions.py`
2. `tools/hotspot_extraction/scanner/test_regions.py`

---

## Verification & Acceptance Criteria
Run:
```bash
python3 tools/hotspot_extraction/scanner/test_regions.py
```
- [ ] **No Overlaps**: All generated activity rects have `>= 3 pt` separation.
- [ ] **No Panel Cuts**: Bounding boxes for activities on drawn panels cover the entire panel (no sliced speech bubbles or cut exercise cards).
- [ ] **Headlines**: Headlines are cleanly extracted for tooltips and zoom captions.
- [ ] **Sub-items**: Sub-questions have valid nested rects.

---

## Handoff Checklist for Next Agent
- Complete `ActivityRegion` objects are generated per page.
- Ready for Phase 4 to reconcile publisher anchors and serialize to `regions.json`.
