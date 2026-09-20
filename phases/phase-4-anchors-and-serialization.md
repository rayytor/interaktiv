# Phase 4: Publisher Anchor Reconciliation & `regions.json` Serialization

## Objective
Reconcile detected activity regions with publisher interactive metadata (`activities_meta/<book_id>.json`), calculate printed folio mappings, and serialize the output into the exact `regions.json` (and `diagnostics.json.gz`) format required by the frontend reader (`js/viewer.js`) and server (`server.py`).

---

## Context & Background
- **Publisher Metadata**: The publisher lists interactive digital activities in `activities_meta/<book_id>.json` under `kitapogeList`. Entries with `ogeturu === 1` and `sayfaustuoge === 1` are anchored to specific printed pages (`sayfano`) at coordinates `(posx, posy)`.
- **Printed Folio Mapping**:
  - The printed page number visible on a sheet is not always equal to the PDF sheet number (e.g. cover, title page, and table of contents shift page numbers).
  - Folio numbers must be detected from the header/footer text primitives.
  - Compute a modal `folioOffset = pageNum - printedPage` to resolve unnumbered sheets.
- **Anchor Snapping (`anchoredActivities`)**:
  - For each interactive item on a page, find the detected activity whose top edge is closest to `posy`.
  - Items that do not match an existing lettered activity become **anchored activities** (`anchored: True`).
- **Exact Serialization Schema**:
  - The generated `regions.json` must be a 100% drop-in match for what `bake_activities.mjs` produces so `js/viewer.js` can render it without any code changes.

---

## Reference Code to Inspect
- `tools/hotspot_extraction/bake_activities.mjs`:
  - Lines 72–87: `fingerprint(file)` function (computes `<size>-<sha256 of head, middle, tail>`).
  - Lines 126–147: `serializeActivity(act)`.
  - Lines 230–260: `folioPass`.
- `tools/hotspot_extraction/tests/_harness.mjs`:
  - Lines 42–55: `interactiveOges(bookId)`.
- `js/interactive-links.js`: Anchor hit-testing and association logic.
- `activities/books/03eb95bb-3a93-4f99-a2df-36d02f9c40d9/regions.json`: Reference sample output.

---

## Detailed Tasks

### 1. Create Anchor & Serialization Modules
Create:
- `tools/hotspot_extraction/scanner/anchors.py`
- `tools/hotspot_extraction/scanner/serializer.py`

### 2. Implement `anchors.py`
```python
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from .regions import ActivityRegion

@dataclass
class PublisherOge:
    id: str
    title: str
    sayfano: int          # Printed page number
    posx: float           # Publisher anchor x (pt)
    posy: float           # Publisher anchor y (pt)
    data: str             # Activity URL or identifier

def load_publisher_oges(meta_path: str) -> List[PublisherOge]:
    """Loads and filters valid interactive page elements from activities_meta/<id>.json."""
    ...

def reconcile_anchors(
    activities: List[ActivityRegion],
    oges: List[PublisherOge],
    page_height: float,
    printed_page: int
) -> List[ActivityRegion]:
    """
    Matches publisher anchors to detected activities.
    Unmatched anchors create new anchored ActivityRegion entries.
    """
    ...
```

### 3. Implement `serializer.py`
```python
import hashlib
import json
import gzip
import os
from typing import Dict, Any

BAKE_VERSION = 2
DIAGNOSTICS_VERSION = 1

def compute_fingerprint(pdf_path: str) -> str:
    """
    Computes `<size>-<hash>` of head, middle, and tail (256 KB slices).
    Must match fingerprint() in bake_activities.mjs identically.
    """
    ...

def serialize_book_regions(
    book_id: str,
    pdf_path: str,
    page_count: int,
    pages_activities: Dict[int, List[ActivityRegion]],
    folio_map: Dict[int, int],
    folio_offset: int,
    calibration_info: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Builds the exact JSON structure for regions.json.
    """
    ...
```

#### JSON Output Format (`regions.json`):
```json
{
  "version": 2,
  "fingerprint": "146149975-...",
  "pageCount": 165,
  "calibration": {
    "enabled": true,
    "confidence": "strong",
    "markerSize": 11,
    "styles": ["sans-serif|1|-0.2|11.0"]
  },
  "folio": {
    "offset": 2,
    "byPage": { "3": 1, "4": 2, ... }
  },
  "pages": {
    "33": {
      "pageWidth": 569.76,
      "pageHeight": 796.54,
      "columns": [[51.0, 275.0], [286.0, 520.0]],
      "activities": [
        {
          "id": "p33-a",
          "label": "a",
          "column": 0,
          "rect": [51.0, 420.0, 275.0, 560.0],
          "parts": [[51.0, 420.0, 275.0, 560.0]],
          "headline": "Work in groups...",
          "items": [
            {
              "id": "p33-a-1",
              "label": "1",
              "number": 1,
              "partIndex": 0,
              "rect": [68.0, 450.0, 275.0, 480.0]
            }
          ]
        }
      ]
    }
  }
}
```

### 4. Implement Diagnostic Sidecar (`diagnostics.json.gz`)
Write `diagnostics.json.gz` alongside `regions.json`:
- Contains extracted panels, solution blocks, and markers for the scorecard harness to verify that regions do not cut panels (`cutprobe.mjs` / `scorecard.mjs`).

### 5. Create End-to-End Single-Book Test
Create: `tools/hotspot_extraction/scanner/test_serialize.py`
- Runs extraction, layout, region growth, anchor reconciliation, and serialization on `books/0e966773-5012-4f57-8be5-d892e8c75f22.pdf`.
- Saves output to a temporary directory.
- Verifies that JSON parses and matches the schema of the existing `activities/books/0e966773.../regions.json`.

---

## Deliverables
1. `tools/hotspot_extraction/scanner/anchors.py`
2. `tools/hotspot_extraction/scanner/serializer.py`
3. `tools/hotspot_extraction/scanner/test_serialize.py`

---

## Verification & Acceptance Criteria
Run:
```bash
python3 tools/hotspot_extraction/scanner/test_serialize.py
```
- [ ] `fingerprint` matches the output of `bake_activities.mjs` for the same file.
- [ ] `regions.json` conforms to `BAKE_VERSION = 2` schema.
- [ ] Publisher interactive oges (`kitapogeList`) match detected regions with high precision.
- [ ] `diagnostics.json.gz` is properly compressed and readable by `scorecard.mjs`.

---

## Handoff Checklist for Next Agent
- Fully functional single-book scanner is ready.
- Next phase will build the multi-process batch runner to process all 56 PDFs in parallel.
