# Activity Hotspots Scanner Modernization — Phase Roadmap

## Project Goal
Replace the legacy single-threaded, memory-heavy Node.js + PDF.js hotspot scanner (`js/activities.js` + `tools/hotspot_extraction/bake_activities.mjs`) with a high-speed, low-memory Python-based scanner (`PyMuPDF` / `fitz`).

### Key Constraints & Requirements
1. **Memory**: Must run comfortably on an **8 GB RAM machine** without swapping. Peak RAM per process must remain under **100 MB**, and total RAM across 4 parallel workers must remain under **500 MB**.
2. **Speed**: Must scan all 56 textbooks (~15,000 pages) in **under 2 minutes** (compared to hours previously).
3. **No Pixel Decoding**: Image bounding boxes must be read from PDF dictionary/stream headers without rasterizing uncompressed RGBA pixel buffers (solving the 2.8 GB RAM spike on `1cc573f6`).
4. **Exact Output Schema**: Must produce the exact `regions.json` schema (`BAKE_VERSION = 1` / `2`) required by `js/viewer.js` and `server.py`.
5. **Scorecard Quality**: Must match or exceed baseline yield, match rate, and violation counts recorded in `tools/hotspot_extraction/tests/scorecard.json`.

---

## Phase Breakdown

Each phase below is designed to be executed by an AI agent in a distinct session. Each phase file contains its own context, requirements, reference code, deliverables, and verification criteria.

| Phase | Document | Description | Primary Output |
| :--- | :--- | :--- | :--- |
| **Phase 1** | [phase-1-primitive-extraction.md](./phase-1-primitive-extraction.md) | PyMuPDF pipeline setup, zero-decode image bboxes, vector drawing extraction, text span extraction, memory benchmarks. | `tools/hotspot_extraction/scanner/primitives.py` |
| **Phase 2** | [phase-2-layout-and-markers.md](./phase-2-layout-and-markers.md) | Column detection (XY-cut / projection), modal typographic hierarchy, Turkish alphabet & step marker state machine. | `tools/hotspot_extraction/scanner/layout.py`, `markers.py` |
| **Phase 3** | [phase-3-region-growth-and-snapping.md](./phase-3-region-growth-and-snapping.md) | Region expansion, drawn panel snapping (dialogue bubbles, tinted boxes), solution space absorption (tables, rules), de-overlapping. | `tools/hotspot_extraction/scanner/regions.py` |
| **Phase 4** | [phase-4-anchors-and-serialization.md](./phase-4-anchors-and-serialization.md) | Publisher manifest anchor reconciliation (`activities_meta`), printed folio mapping, `regions.json` & diagnostics serialization. | `tools/hotspot_extraction/scanner/anchors.py`, `serializer.py` |
| **Phase 5** | [phase-5-batch-runner-and-validation.md](./phase-5-batch-runner-and-validation.md) | Multi-process batch CLI runner (`--all`, `--workers 4`), scorecard comparison harness, RAM/speed audit across all 56 PDFs. | `tools/hotspot_extraction/scan.py`, validation report |
| **Phase 6** | [phase-6-system-integration.md](./phase-6-system-integration.md) | Server integration (`server.py`), packaging tool update (`package_book.py`), deprecation of legacy runner, documentation. | Updated `server.py`, `README.md` |

---

## Session Instructions for Agents
When starting a phase:
1. Open and read the corresponding `phase-X-...md` file completely.
2. Review the referenced files in the existing codebase before modifying or creating code.
3. Keep all code self-contained inside `tools/hotspot_extraction/scanner/` until Phase 6 integration.
4. Run the verification steps specified in the phase document and ensure all acceptance criteria pass before concluding the session.
