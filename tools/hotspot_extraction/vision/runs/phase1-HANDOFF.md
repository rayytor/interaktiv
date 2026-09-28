# Phase 1 handoff — render the corpus once

Date: 2026-09-28. Plan: `tools/hotspot_extraction/vision/PLAN.md`, Phase 1.
Previous handoff: `phase0-HANDOFF.md`.

## What was built

| Item | State |
| --- | --- |
| `vision/render.py` | done. `books/*.pdf` → `data/vision/pages/<book-id>/<page:04d>.jpg` (1024 px wide, height follows the page aspect, JPEG q90), `index.jsonl` (one line per page), `fingerprint.txt`. One child process per book (`ProcessPoolExecutor(max_tasks_per_child=1)`), `--workers 2` default, per-page pixmap dropped after saving, `malloc_trim` every 50 pages, peak RSS per book from `ru_maxrss` plus a parent-side sampler like `scan.py`. Skips a page whose JPEG exists and is in the index; `--force` redoes; a book whose stored fingerprint differs from the PDF is re-rendered in full. `--only`, `--pages 30-32` for smoke runs, `--summary` writes the run JSON. Files are written to `.part` and renamed, so a crash never leaves a half JPEG or half index. |
| `vision/test_render.py` | done, 5 tests: rendering one page twice is byte-equal; the index line's pixel size equals the JPEG's; x and y scale agree within 0.1 %; `render_book` is resumable (second run renders nothing, index and fingerprint present); discovery yields unique UUID ids. |
| `vision/runs/phase1-render.json` | the full run's summary, per book |
| `data/vision/pages/` (gitignored) | 27 books, 6,185 JPEGs, 1.42 GB |
| `scanner/regions.py` | one-line fix: `Any` was used in a type annotation without being imported. Harmless on the app's Python 3.14 (annotations are lazy there) but an import error on the 3.12 vision venv, which `render.py` hit when importing `compute_fingerprint`. |

Page numbers are 1-based, the same as `regions.json` and the reader. The
index line is what the plan specified; pixel `(px, py)` (y down) maps to the
PDF point `(px / sx, height_pt - py / sy)` with `sx = width_px / width_pt`,
`sy = height_px / height_pt`, and the test shows `sx == sy` to 0.1 %.

## One plan assumption corrected: 27 books, not 29

`books/` holds 29 PDFs but two are legacy copies: `books/full_pdf.pdf` is
`0e966773` (STEPWISE 10 SB) and `books/matematik.pdf` is `51cdbbce`
(Matematik), byte for byte (same `compute_fingerprint`; `books_manager.py`
maps them the same way in `legacy_local_map`). `render.py` folds files with
an equal fingerprint into one book and keeps the UUID-named id, so the ids
match `activities/books/` and `train/splits.json`, and no book can end up
in both the training and the held-out set under two names. That is why the
count is 6,185 pages rather than the plan's 6,767 (the extra 582 were the
two copies). §0.3 rule 6 already said "27 local books".

Only the cover of each book is a two-page spread (about 1,150 pt wide);
every other page is a single page of 553–595 pt. So a fixed 1024 px width
gives the same ~1.8 px/pt on every content page, and the plan's choice
stands. Cover pages come out 1024×~690.

## Numbers (the "done when" table)

| Measure | Value |
| --- | ---: |
| Books | 27 (0 failed) |
| Pages = JPEGs = index lines | 6,185 (checked per book against `page_count`) |
| Total size | 1.42 GB (1,519,969,053 bytes), 246 KB per page on average |
| Wall clock, 3 workers | 204 s (30 pages/s); 600 s of worker CPU in total |
| Peak RSS, one worker | 1,134 MB (`cb558332`, Fizik); next 798 MB (`753fcdb0`); median about 420 MB |
| Peak RSS, all workers together | 2,032 MB; whole run 2,093 MB |
| Re-run with everything present | 1.6 s, 0 rendered, 6,185 kept |
| Tests | 5 passed in 2.1 s |

Memory note for later phases: the peak is the PDF, not the pixmap. A 1024 px
page is 4.4 MB of RGB; the 1.1 GB peak on the Fizik book is pymupdf holding
that PDF's large images while rendering. With 3 workers the run stayed
around 2 GB total on a machine with ~9 GB free, so `--workers 3` is safe;
`--workers 4` would also fit but there is no need, the run is 3½ minutes.

Rule 8 arithmetic: the smoke run (60 pages of three books) projected about
9 minutes for the full corpus with 3 workers, under 1 GB of RAM and 1.5 GB
of disk out of 794 GB free, so the full run was started without an ask.
It took 3.4 minutes.

## Reproduce

```bash
cd ~/Projects/interaktiv
.venv-vision/bin/python -m pytest tools/hotspot_extraction/vision/test_render.py -q
.venv-vision/bin/python tools/hotspot_extraction/vision/render.py --only d13a75d3,a0e5ed1a,0a3fbb41 --pages 1-20   # smoke, 6 s
.venv-vision/bin/python tools/hotspot_extraction/vision/render.py --workers 3 --summary tools/hotspot_extraction/vision/runs/phase1-render.json   # full, 3.4 min
.venv-vision/bin/python tools/hotspot_extraction/vision/render.py --workers 3   # again: 1.6 s, everything kept
```

Completeness check used after the run (every book: index pages are
1..page_count, one JPEG per page, every JPEG 1024 px wide, no `.part`
files): see the `phase1-render.json` per-book `files`/`page_count` fields;
they agree for all 27 books.

Page 31 of `0a3fbb41` from this run is pixel-identical in size to Phase 0's
`runs/phase0-page31.jpg` (1024×1432; same page box 569.764×796.535 pt), so
Phase 0's drawing code and the teacher call apply unchanged to the corpus.

## For Phase 2

- `data/vision/pages/<book-id>/index.jsonl` is the page list `select.py`
  should sample from; `page_count` per book is `len(index)`.
- Gemini's `[ymin, xmin, ymax, xmax]` in 0–1000 → pixels is
  `x * width_px / 1000`, `y * height_px / 1000` (as in
  `runs/phase0_draw.py`), then to PDF points as above.
- `render.py --only <id> --pages <n>` re-renders a single page in a second
  if a label run ever needs a fresh file.

## Left undone

Nothing from the Phase 1 task list. Not done on purpose: no page was
labelled, nothing was trained, `scan.py` untouched.

Commits: branch `phase1-render`. `db5cb26` is the phase commit (render.py,
test_render.py, the run summary, this handoff, the regions.py import fix); the
follow-up commit records that hash here (`git log --oneline main..phase1-render`).
