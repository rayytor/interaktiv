# Phase 1: High-Performance Primitive Extraction (PyMuPDF)

## Objective
Build a lightweight, zero-pixel-decoding PDF primitive extractor in Python using `PyMuPDF` (`fitz`). This phase replaces the memory-heavy PDF.js `getTextContent()` and `getOperatorList()` calls with native C-speed data extraction that operates strictly within an 80 MB RAM footprint per process.

---

## Context & Background
In the legacy implementation (`js/activities.js` and `bake_activities.mjs`):
- `page.getOperatorList()` is called to find image rects and vector paths (tinted panels, tables, solution rules).
- PDF.js decodes all embedded images to uncompressed RGBA pixel buffers in memory.
- In `books/1cc573f6-324a-470d-8452-bbf6d3fd0a23.pdf`, sheet 164 embeds a 15,071×9,830 pixel illustration. PDF.js decodes it to ~590 MB of RGBA, pushing resident memory (RSS) to **2.8 GB** (see `tools/hotspot_extraction/tests/_watchdog.mjs`).
- `bake_activities.mjs` reads the entire PDF into memory (`new Uint8Array(fs.readFileSync(file))`), causing heavy base memory allocation.

In this phase, we use `PyMuPDF` (`fitz`), which:
1. Streams PDF pages via memory-mapped I/O.
2. Extracts image bounding boxes directly from the PDF dictionary header (`page.get_image_info(xrefs=True)`) **without decoding pixel data**.
3. Extracts vector paths (`page.get_drawings()`) for background panels, borders, and solution rules in milliseconds.
4. Extracts structured text spans (`page.get_text("rawdict")` / `"words"`) including font name, size, flags (bold, italic), color, and bounding box.

---

## Prerequisites & Dependencies
- Python 3.10+
- `PyMuPDF` library:
  ```bash
  pip install pymupdf
  ```
  *(Note: If using a virtual environment or system Python, verify `import fitz` works).*

---

## Reference Code to Inspect
- `js/activities.js`: Lines 68–76, 2076 (`pageStructure`), and operator list walking logic.
- `tools/hotspot_extraction/bake_activities.mjs`: Lines 220–245 (`folioPass`), lines 150–160 (`releasePdfPage`).
- `tools/hotspot_extraction/tests/_watchdog.mjs`: Details regarding sheet 164 of `1cc573f6` and RSS limits.

---

## Detailed Tasks

### 1. Create Package Structure
Create directory: `tools/hotspot_extraction/scanner/`
Create:
- `tools/hotspot_extraction/scanner/__init__.py`
- `tools/hotspot_extraction/scanner/primitives.py`

### 2. Implement `primitives.py`
Implement a data model and extractor with the following specifications:

#### Data Structures
```python
from dataclasses import dataclass
from typing import List, Tuple, Optional

@dataclass
class TextSpan:
    text: str
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF user points (y-up or y-down; note convention)
    font: str
    size: float
    flags: int       # PyMuPDF flags (bit 0: superscript, bit 1: italic, bit 2: serif, bit 4: bold)
    color: int

@dataclass
class VectorDrawing:
    rect: Tuple[float, float, float, float]   # Bounding box
    fill: Optional[Tuple[float, ...]]         # Fill color RGB/Gray or None
    stroke: Optional[Tuple[float, ...]]       # Stroke color or None
    width: float                              # Line width
    is_rect: bool                             # True if path is a rectangular fill/box
    lines: List[Tuple[float, float, float, float]] # Individual line segments

@dataclass
class ImageRect:
    bbox: Tuple[float, float, float, float]
    width: int   # Pixel width
    height: int  # Pixel height

@dataclass
class PagePrimitives:
    page_num: int            # 1-based index
    width: float             # Page width in pt
    height: float            # Page height in pt
    spans: List[TextSpan]
    drawings: List[VectorDrawing]
    images: List[ImageRect]
```

#### Coordinate System Convention:
> [!IMPORTANT]
> PyMuPDF coordinates use top-left origin (y-down, $y=0$ at page top). PDF user space (used by PDF.js and `regions.json`) uses bottom-left origin (y-up, $y=0$ at page bottom).
> Provide helper functions `to_pdf_coords(rect, page_height)` and `from_pdf_coords(rect, page_height)` to seamlessly convert between systems. In `PagePrimitives`, document whether bboxes are stored in standard PDF coords ($y$-up) or screen coords ($y$-down). Standardizing on PDF coords ($y$-up) matches `regions.json`.

#### Extraction Function:
```python
def extract_page_primitives(doc: fitz.Document, page_num: int) -> PagePrimitives:
    """
    Extracts text spans, vector drawings, and image bounding boxes for a page.
    Crucially: NEVER calls page.get_pixmap() or decodes image pixel streams.
    """
    ...
```
- For Images: Use `page.get_image_info(xrefs=True)`. Each item returns a dict containing `'bbox'` and `'width'`, `'height'`. Do not touch the image stream.
- For Drawings: Use `page.get_drawings()`. Parse rectangular fills, lines, strokes, and bounding boxes.
- For Text: Use `page.get_text("rawdict")`. Walk `blocks` -> `lines` -> `spans`. Capture `span["text"]`, `span["bbox"]`, `span["font"]`, `span["size"]`, `span["flags"]`, `span["color"]`.

### 3. Implement Memory & Speed Benchmark Script
Create: `tools/hotspot_extraction/scanner/bench_primitives.py`
- Benchmarks extraction across:
  1. `books/0e966773-5012-4f57-8be5-d892e8c75f22.pdf` (entire book).
  2. `books/1cc573f6-324a-470d-8452-bbf6d3fd0a23.pdf` (specifically sheet 164 with the 15,071×9,830 image).
- Measures:
  - Resident Memory (RSS via `psutil` or `/proc/self/statm`).
  - Pages per second throughput.
  - Verification that image bounding box on sheet 164 is correctly captured without decoding pixels.

---

## Deliverables
1. `tools/hotspot_extraction/scanner/__init__.py`
2. `tools/hotspot_extraction/scanner/primitives.py`
3. `tools/hotspot_extraction/scanner/bench_primitives.py`

---

## Verification & Acceptance Criteria
Run:
```bash
python3 tools/hotspot_extraction/scanner/bench_primitives.py
```
- [ ] **Memory**: Peak RSS on sheet 164 of `1cc573f6` stays **below 80 MB** (legacy spiked to 2.8 GB).
- [ ] **Speed**: Extraction throughput exceeds **100 pages/second** on a single CPU core.
- [ ] **Completeness**: Text spans, vector drawings (tinted panels/lines), and image bboxes are extracted for every tested page.

---

## Handoff Checklist for Next Agent
- `PagePrimitives` data structure is tested and ready for Phase 2 to consume.
- Coordinate conversion between PyMuPDF ($y$-down) and PDF ($y$-up) is clearly defined and tested.
