# Phase 6: System Integration & Workflow Migration

## Objective
Integrate the new Python hotspot scanner (`scan.py`) into the Interaktiv application ecosystem, replacing calls to the legacy Node.js baker across build scripts, packaging pipelines, and server utilities, while preserving the browser client-side fallback.

---

## Context & Background
Currently:
- `tools/hotspot_extraction/package_book.py` and `build_library.py` invoke `node tools/hotspot_extraction/bake_activities.mjs` via subprocess.
- `tools/build_school.sh` invokes the packaging pipeline.
- `server.py` serves the baked `regions.json` via `/api/activities/regions`.
- In this final phase:
  - We update `package_book.py` and build scripts to call `python3 tools/hotspot_extraction/scan.py`.
  - We keep `js/activities.js` intact in the browser as a client-side fallback for ad-hoc PDFs opened via the local file picker.
  - We update project documentation (`README.md`, `ACTIVITY_DETECTION_PLAN.md`).

---

## Reference Code to Inspect
- `tools/hotspot_extraction/package_book.py`: Lines 19–24, 48–52, and the subprocess invocation of `bake_activities.mjs`.
- `tools/hotspot_extraction/build_library.py`: Library packaging flow.
- `tools/build_school.sh`: School edition packaging script.
- `server.py`: Lines 260–293 (`/api/activities/regions`).
- `README.md`: Activity hotspots section.

---

## Detailed Tasks

### 1. Update `package_book.py`
In `tools/hotspot_extraction/package_book.py`:
- Replace the invocation of `bake_activities.mjs` with `scan.py`:
  ```python
  cmd = [
      sys.executable,
      os.path.join(HERE, "scan.py"),
      "--only", book_id,
      "--out", os.path.join(bundle_dir, "regions.json"),
      "--force" if force else "",
  ]
  ```
- Ensure return codes and error logs are cleanly handled.

### 2. Update `build_library.py` and `build_school.sh`
- Ensure any flags or options passed to the baker match `scan.py`.
- Verify that batch packaging runs smoothly with the new scanner.

### 3. Server Integration (`server.py`)
- Verify that `/api/activities/regions` seamlessly serves the newly generated `regions.json` files.
- (Optional enhancement) If a book is requested that has not been baked yet, allow `server.py` to trigger a quick JIT background bake using `scan.py` (which now takes only ~1 second instead of minutes).

### 4. Browser Client-Side Verification
- Open the web application (`http://localhost:8000` or via `server.py`).
- Verify that opening a packaged textbook:
  1. Loads the baked `regions.json` immediately.
  2. Renders activity hotspots with correct coordinates and padding.
  3. Clicking an activity enters focus zoom mode smoothly.
  4. Sub-question hotspots are clickable in focus mode.
- Verify that dragging an arbitrary PDF into the reader still falls back to live detection (`js/activities.js`).

### 5. Documentation Updates
- Update `README.md` to document the new `scan.py` command and its performance characteristics (under 2 minutes, <500 MB RAM for 56 books).
- Update `ACTIVITY_DETECTION_PLAN.md` with notes on the Python scanner architecture.

---

## Deliverables
1. Updated `tools/hotspot_extraction/package_book.py`
2. Updated `tools/hotspot_extraction/build_library.py`
3. Updated `README.md`
4. End-to-end verification report

---

## Verification & Acceptance Criteria
- [ ] Packaging a book with `package_book.py` uses `scan.py` and completes in < 2 seconds.
- [ ] `npm test` or existing test suites pass.
- [ ] Opening the reader in the browser displays hotspots correctly from the newly generated bakes.
- [ ] No regressions in text selection, focus zoom, or Esc restore in `js/viewer.js`.

---

## Completion Sign-Off
Once Phase 6 is complete, the migration from legacy Node/PDF.js baking to the high-performance Python scanner is fully accomplished!
