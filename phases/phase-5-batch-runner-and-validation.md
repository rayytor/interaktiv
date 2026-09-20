# Phase 5: Multi-Process Batch Runner & Scorecard Validation

## Objective
Build a high-throughput, multi-process batch runner in Python that can scan all 56 textbooks (~15,000 pages) in parallel under **500 MB of total RAM** within **2 minutes**. Validate the generated bakes against the existing `scorecard.mjs` test harness.

---

## Context & Background
In the legacy Node.js setup (`fetch_and_bake.py` + `bake_activities.mjs`):
- Books could only be processed one at a time because V8 memory spikes (up to 2.8 GB on a single page) prevented running parallel processes without crashing an 8 GB RAM system.
- Scanning all books took well over an hour.
- In this phase, because the Python/PyMuPDF scanner uses only ~40–80 MB of RAM per process, we can safely launch **4 to 8 parallel workers** via `concurrent.futures.ProcessPoolExecutor`.
- Total RAM across all 4 workers is `< 400 MB` (less than 5% of an 8 GB system).
- The entire catalogue can be baked in **under 60–90 seconds**.

---

## Reference Code to Inspect
- `tools/hotspot_extraction/fetch_and_bake.py`: CLI arguments, catalogue walking, fingerprint checking.
- `tools/hotspot_extraction/tests/scorecard.mjs`: Test harness that grades bakes (`--from-bake`).
- `tools/hotspot_extraction/tests/scorecard.json`: Baseline scorecard metrics.
- `kitap_pdf_linkleri.txt`: List of all 56 book IDs and URLs.

---

## Detailed Tasks

### 1. Implement Batch Runner (`scan.py`)
Create: `tools/hotspot_extraction/scan.py`
Supported CLI arguments:
```
usage: scan.py [-h] [--all] [--only IDS] [--workers N] [--out DIR] [--force] [--quiet]

High-speed local activity hotspot scanner.

options:
  --all           Scan all books found in books/ or kitap_pdf_linkleri.txt
  --only IDS      Comma-separated list of book IDs to scan
  --workers N     Number of parallel worker processes (default: min(4, os.cpu_count()))
  --out DIR       Output directory for bakes (default: activities/books)
  --force         Re-scan even if the existing bake is up to date
  --quiet         Suppress per-page progress output
```

#### Concurrency & Memory Management
- Use `concurrent.futures.ProcessPoolExecutor(max_workers=workers)`.
- Use `psutil` or `/proc/self/statm` to log worker memory and ensure total RSS stays bounded.
- Support incremental skipping: If `bakeIsCurrent()` matches the PDF's fingerprint, skip without re-baking unless `--force` is specified.

### 2. Implement Scorecard Comparison Harness
Create: `tools/hotspot_extraction/compare_scorecard.py`
- Runs the new scanner on the installed books.
- Executes `node tools/hotspot_extraction/tests/scorecard.mjs --from-bake`.
- Compares results against `tools/hotspot_extraction/tests/scorecard.json`:
  - **Yield**: Number of detected regions and questions.
  - **Join**: Match rate with publisher interactive items (`kitapogeList`).
  - **Violations**: Must have **0 overlapping hotspots**, **0 slivers**, and minimize/eliminate **cut panels**.
- Produces a markdown summary table comparing Legacy vs New Scanner.

### 3. Benchmark Execution Across All Books
Run the batch scanner across all available PDFs and record:
- Total wall-clock time.
- Peak RAM usage.
- Pages scanned per second.

---

## Deliverables
1. `tools/hotspot_extraction/scan.py`
2. `tools/hotspot_extraction/compare_scorecard.py`
3. `tools/hotspot_extraction/BENCHMARK_REPORT.md` (Summary of memory, speed, and scorecard comparison)

---

## Verification & Acceptance Criteria
Run:
```bash
python3 tools/hotspot_extraction/scan.py --all --workers 4
python3 tools/hotspot_extraction/compare_scorecard.py
```
- [ ] **Memory**: Peak total memory across all 4 workers never exceeds **500 MB** at any time.
- [ ] **Speed**: Entire batch of installed books finishes in **under 90 seconds**.
- [ ] **Quality**: Scorecard match rate meets or exceeds the baseline in `scorecard.json`.
- [ ] **Zero Overlaps**: 0 overlapping hotspot violations.

---

## Handoff Checklist for Next Agent
- High-speed batch scanner is validated and production-ready.
- Phase 6 will integrate it into the server, packaging scripts, and developer workflows.
