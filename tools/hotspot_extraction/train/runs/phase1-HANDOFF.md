# Phase 1 handoff: expose the detector to the fitter

Done 2026-09-27 on branch `phase1-expose-detector`, cut from
`phase0-training-loop` (Phase 0 is not merged into `main`). All five tasks are
complete and every acceptance item is met on the 27 local books.

## Commits

- The commit that contains this file, `feat(train): phase 1 …`.
  `git log -1 --format=%H -- tools/hotspot_extraction/train/runs/phase1-HANDOFF.md`
  prints its hash.

## What changed

### Task 1: the census is a tool

`train/census.py` holds Appendix A's census. It reads bakes and diagnostics
only, runs one child per book, prints a per-book table, and accepts `--json`,
`--only` (id prefixes allowed) and `--compare <earlier.json>`, which prints
`before→after` per cell. On the Phase 0 bakes it reproduces §0.4 exactly:
1,274 cuts, of which 506 straddle, 309 side, 249 bottom and 210 top
(`runs/phase0-census.json`).

### Task 2: the snap thresholds are knobs

`regions.snap_edges`: the literals became module globals
`SNAP_EXPAND_SHARE = 0.35`, `SNAP_PANEL_RATIO = 2.5` and `SNAP_PASSES = 3`. They
are in `SPEC` with ranges 0.1–0.9, 1.0–8.0 and 1–6. `passes` now defaults to
`None` and resolves to `SNAP_PASSES` at call time, because
`test_no_knob_is_a_function_default` forbids a knob as a default argument.
Behaviour is unchanged: a bake of `79cbecfa` was byte-identical apart from the
stamp. The defaults' profile hash is now **`3aae0c22cfb6`** (was
`27b80aacc68e`), because the profile has three more fields.

### Task 3: the blind cleanups see the page

- `pipeline.PageResult.geometry` carries the `PageGeometry` that growth built
  (`trace.geometry`). When growth left none, it carries the one
  `reconcile_anchors` rebuilt for an anchor-only sheet; `reconcile_anchors` now
  stores that rebuilt geometry on the trace.
- The following now pass it to `clean_page_activities`:
  - `serializer.serialize_book_regions(pages_geometry=…)`, fed by `scan.py`
  - `objective._replay_pages`
  - `learn.examples_for_book`
  - `anchors.reconcile_anchors`' fallback path, which uses `trace.geometry` when
    it has one. That path is only reached without primitives, which the
    pipeline never does.
- **Measured, as the brief asked:** `79cbecfa` baked before and after gives
  **114 → 114 cuts**, and its regions are byte-identical. The premise did not
  hold for this book. `detect_page` already hands the serializer regions that
  growth (and anchoring, when it synthesises) cleaned against geometry, and
  those regions no longer overlap. So the blind de-overlap had nothing to cut.
  The change is still right: it removes a pass that *would* cut at midpoints
  the moment anything upstream left an overlap. All of the cut reduction in
  this phase comes from task 4.

### Task 4: tall regions, 50 → 0

There were two faults, both general.

1. **The cleanup did not know the sheet.** `clean_page_activities` and
   `snap_edges` guessed the page as `max(595×842, content box + 40)`. On the
   779.5 pt and 796.5 pt sheets, which hold all 50 tall regions, the clamp
   therefore allowed 0.695 × 842 = 585 pt, where the scorer allows
   0.7 × 779.5 = 546 pt. `snap_edges` could expand a region to take a block
   whole right up to 585 pt. All 50 tall regions were such expansions (none
   were anchored).
   - Fix: `PageGeometry` now carries `page_w` and `page_h`. Growth and
     `anchors._page_geometry` fill them from the primitives.
     `PageGeometry.sheet()` returns them, and falls back to the old guess only
     when a caller built a geometry without a size.
2. **Every tall clamp sliced whatever lay at the limit.** Growth, `snap_edges`
   and the final cleanup raised the bottom edge to exactly
   `top − 0.695·H`, which often lands inside an answer grid. Once fault 1 was
   fixed, `snap_edges` refused the tall expansion and retreated sideways
   instead (d13a75d3 rose 48 → 53 cuts in the first attempt).
   - Fix: a new `regions.tall_floor(geom, rect, max_h)` is used at all three
     clamp sites. The candidates are the limit itself, the seams between lines
     (`legal_planes`) and the edges of the drawn blocks. It takes the lowest
     candidate that slices no block the region did not already slice. "Slice"
     here uses a strict 0.1 % tolerance, not the scorer's 5 %, so the floor
     never keeps a grid's top row. It first prefers a candidate clear of type,
     where a height within 1 pt of a line box's edge counts as clear because
     tight leading makes line boxes overlap. It falls back to the limit only
     when nothing spares every block.
   - This is §0.2 rule 4 applied to the height limit: a block that cannot be
     taken whole within 0.7 of the sheet is left out whole. Example: on
     `d13a75d3` p39, a question whose grid would make it 0.74 of the sheet now
     stops at the grid's top edge.
   - It also removed many cuts that were never tall violations, because
     growth's clamp had been cutting answer grids all along. For example,
     `51cdbbce` went from 90 to 42 cuts, and its bottom-in-block cuts from
     47 to 13.

A third fault surfaced once the sheet was right: **a region's own pieces.**
`snap_edges` checked an expansion only against *other* activities, so a piece
taking a panel whole could half-cover its sibling piece (`51cdbbce` p267: 7
overlaps on train and 2 on held-out appeared). Now:
- an expansion may take a sibling piece whole, and the swallowed piece is
  dropped as redundant;
- an expansion may not cover a sibling in part;
- the sub-items of a dropped piece move to the piece that absorbed it. The
  reader looks items up by `partIndex`, so they have to follow.

This groups nothing: the pieces were already one activity.

## Before / after (§0.4 format)

Phase 0: `runs/phase0-baseline.json`, profile `27b80aacc68e`. Phase 1:
`runs/phase1-baseline.json`, profile `3aae0c22cfb6`. The fields are identical
defaults, and the hash moved only because fields were added.

| Split | Books | Loss | Cuts | Cuts/region | Tall | Overlaps | Slivers | Match | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train (before) | 18 | 6.926 | 888 | 0.070 | 40 | 0 | 0 | 91.9 % | 77.9 % |
| train (after) | 18 | 5.869 | 782 | 0.062 | 0 | 0 | 0 | 92.6 % | 77.9 % |
| held-out (before) | 9 | 8.356 | 386 | 0.090 | 10 | 0 | 0 | 95.0 % | 62.8 % |
| held-out (after) | 9 | 7.497 | 320 | 0.075 | 0 | 0 | 0 | 95.0 % | 62.8 % |

Held-out was scored by `--baseline` as in Phase 0. Nothing was fitted in this
phase.

### Per book (scorer counts)

No book gained cuts, lost match, or lost coverage. Match rose on `d13a75d3`
(95.3 → 100 %) and `df1e313c` (93.1 → 96.6 %).

| book | split | cuts | panel | answer | match % | coverage % | regions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 09f62a7e | train | 2→2 | 2→2 | 0→0 | 100.0→100.0 | 65.8→65.8 | 440→440 |
| 0a3fbb41 | train | 49→47 | 37→35 | 12→12 | 89.5→89.5 | 74.7→74.7 | 717→717 |
| 0e966773 | train | 59→45 | 25→11 | 34→34 | 97.6→97.6 | 69.7→69.7 | 530→529 |
| 1903e365 | train | 59→52 | 13→10 | 46→42 | 100.0→100.0 | 61.7→61.7 | 563→563 |
| 1cc573f6 | train | 39→23 | 22→7 | 17→16 | 87.5→87.5 | 84.1→84.1 | 607→606 |
| 3d372038 | train | 4→4 | 1→1 | 3→3 | 95.5→95.5 | 79.2→79.2 | 370→370 |
| 4442fde4 | train | 64→61 | 8→7 | 56→54 | —→— | 89.1→89.1 | 789→789 |
| 550e601a | train | 6→5 | 4→3 | 2→2 | 89.8→89.8 | 54.8→54.8 | 174→174 |
| 66098271 | train | 108→108 | 10→11 | 98→97 | 85.7→85.7 | 80.1→80.1 | 899→899 |
| 753fcdb0 | train | 97→97 | 17→17 | 80→80 | 80.2→80.2 | 93.9→93.9 | 1907→1907 |
| 7763e45b | train | 23→23 | 5→5 | 18→18 | 100.0→100.0 | 56.7→56.7 | 431→431 |
| 93bf209f | train | 17→6 | 11→2 | 6→4 | 100.0→100.0 | 77.0→77.0 | 433→433 |
| a0e5ed1a | train | 152→121 | 21→21 | 131→100 | 85.1→85.1 | 93.0→93.0 | 2011→2011 |
| ad3f3275 | train | 0→0 | 0→0 | 0→0 | 100.0→100.0 | 5.6→5.6 | 5→5 |
| afec0d89 | train | 72→72 | 1→1 | 71→71 | 92.9→92.9 | 84.2→84.2 | 636→636 |
| d13a75d3 | train | 48→42 | 6→1 | 42→41 | 95.3→100.0 | 73.7→73.7 | 480→480 |
| d6dd5587 | train | 2→1 | 2→1 | 0→0 | 100.0→100.0 | 79.6→79.6 | 839→838 |
| df1e313c | train | 87→73 | 20→8 | 67→65 | 93.1→96.6 | 86.5→86.5 | 857→857 |
| 11941059 | heldOut | 10→10 | 4→4 | 6→6 | 97.0→97.0 | 82.6→82.6 | 367→367 |
| 3a4479c7 | heldOut | 44→44 | 5→5 | 39→39 | —→— | 69.1→69.1 | 571→571 |
| 51cdbbce | heldOut | 90→42 | 31→13 | 59→29 | 86.8→86.8 | 91.1→91.1 | 1291→1290 |
| 6e4f65dc | heldOut | 0→0 | 0→0 | 0→0 | 100.0→100.0 | 15.4→15.4 | 78→78 |
| 79cbecfa | heldOut | 114→114 | 3→3 | 111→111 | 87.5→87.5 | 59.2→59.2 | 485→485 |
| 7fad03e9 | heldOut | 36→36 | 13→13 | 23→23 | 100.0→100.0 | 63.2→63.2 | 614→614 |
| a7a7886a | heldOut | 0→0 | 0→0 | 0→0 | 100.0→100.0 | 3.1→3.1 | 4→4 |
| c9f63718 | heldOut | 27→25 | 3→1 | 24→24 | 96.3→96.3 | 57.7→57.7 | 179→179 |
| cb558332 | heldOut | 65→49 | 1→1 | 64→48 | 89.7→89.7 | 81.3→81.3 | 687→687 |

### Cut census per book (Phase 0 bakes → Phase 1 bakes)

Cuts are not higher on any book, and the total fell by 172. Every class fell
except straddle, which is Phase 3a's (506 → 505). `66098271` is level at 108:
one panel cut moved between two pieces of the same activity on p10.

| book | regions | cuts | answer | panel | straddle | side | bottom | top |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 09f62a7e | 440 | 2 | 0 | 2 | 0 | 1 | 1 | 0 |
| 0a3fbb41 | 717 | 49→47 | 12 | 37→35 | 4 | 22→20 | 16 | 7 |
| 0e966773 | 530→529 | 59→45 | 34 | 25→11 | 14→13 | 23→20 | 19→9 | 3 |
| 11941059 | 367 | 10 | 6 | 4 | 6 | 1 | 1 | 2 |
| 1903e365 | 563 | 59→52 | 46→42 | 13→10 | 16 | 14→9 | 6→4 | 23 |
| 1cc573f6 | 607→606 | 39→23 | 17→16 | 22→7 | 3 | 21→6 | 8→7 | 7 |
| 3a4479c7 | 571 | 44 | 39 | 5 | 33 | 2 | 3 | 6 |
| 3d372038 | 370 | 4 | 3 | 1 | 3 | 1 | 0 | 0 |
| 4442fde4 | 789 | 64→61 | 56→54 | 8→7 | 19 | 26→28 | 12→7 | 7 |
| 51cdbbce | 1291→1290 | 90→42 | 59→29 | 31→13 | 2 | 26→18 | 47→13 | 15→9 |
| 550e601a | 174 | 6→5 | 2 | 4→3 | 0 | 2→1 | 4 | 0 |
| 66098271 | 899 | 108 | 98→97 | 10→11 | 55 | 15→18 | 13→12 | 25→23 |
| 6e4f65dc | 78 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 753fcdb0 | 1907 | 97 | 80 | 17 | 45 | 4 | 28 | 20 |
| 7763e45b | 431 | 23 | 18 | 5 | 18 | 3 | 2 | 0 |
| 79cbecfa | 485 | 114 | 111 | 3 | 76 | 12 | 14 | 12 |
| 7fad03e9 | 614 | 36 | 23 | 13 | 5 | 25 | 5 | 1 |
| 93bf209f | 433 | 17→6 | 6→4 | 11→2 | 2 | 3→1 | 11→2 | 1 |
| a0e5ed1a | 2011 | 152→121 | 131→100 | 21 | 34 | 42→30 | 26 | 50→31 |
| a7a7886a | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| ad3f3275 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| afec0d89 | 636 | 72 | 71 | 1 | 47 | 6 | 6 | 13 |
| c9f63718 | 179 | 27→25 | 24 | 3→1 | 1 | 19 | 6→4 | 1 |
| cb558332 | 687 | 65→49 | 64→48 | 1 | 37 | 19→3 | 2 | 7 |
| d13a75d3 | 480 | 48→42 | 42→41 | 6→1 | 39 | 3→1 | 5→1 | 1 |
| d6dd5587 | 839→838 | 2→1 | 0 | 2→1 | 0 | 2→1 | 0 | 0 |
| df1e313c | 857 | 87→73 | 67→65 | 20→8 | 47 | 17→10 | 14→7 | 9 |
| **TOTAL** | 16964→16960 | 1274→1102 | 1009→919 | 265→183 | 506→505 | 309→240 | 249→174 | 210→183 |

### Regions lost

The catalogue went from 16,964 regions to 16,960. Coverage (sheets with a
region) is unchanged on every book. By rule 2 of §0.2 these are chosen
trades, stated plainly:

- `d6dd5587` p154-2 was a 0.75-of-the-sheet tall violation, and no
  block-sparing height remained for it.
- `51cdbbce` p35-6 was a 511 pt region reaching x = 662 on a 553 pt sheet.
- `1cc573f6` p61: the icon now binds to `p61-4`, which grows further, instead
  of synthesising a region of its own. The match is unchanged.
- `0e966773` p72-a: `p72-e`'s tall clamp now stops at a figure edge, so
  `p72-a`'s `close_blocks` takes a full-width figure. The result overlaps
  `p72-e`, and `separate_rects` drops it. This is growth-stage close/separate
  interplay, left for Phase 3. The book went 59 → 45 cuts.

## Verification

- Tests: `pytest tools/hotspot_extraction/train tools/hotspot_extraction/scanner tests`
  gives **391 passed**: 153 in the train and scanner suites (146 before this
  phase) and 238 in the repo's `tests/`. New tests in
  `scanner/test_stage2.py`:
  - `TestTallRegions` (5): the cleanup measures the given sheet; snapping does
    not grow past it; the floor leaves a block out whole; the floor falls
    between lines; with no seam the floor is the limit.
  - `TestSnapAbsorbsItsOwnPieces` (2): a swallowed sibling is dropped and its
    items follow; a partly covered sibling blocks the expansion.
  - The first two tall tests and the partial-sibling test fail on the Phase 0
    code.
- `cache.py --all`: 27 shards current.
- `objective.py --verify --workers 2`: **27/27 equal**.
- Rebake `scan.py --all --force --trace --workers 2`: 27 baked.
  - Peak total USS was 570 MB. That is over the scan's own 500 MB budget line
    (Phase 0 recorded 511 MB), and far below the machine's limit.
  - It took 273 s against about 95 s in the brief. The detector is not the
    cause: replaying `d13a75d3` and `51cdbbce` from the cache takes 2.7–2.8 s
    under both the Phase 0 and the Phase 1 code. The difference is in PDF
    parsing, or load on the machine.
- `compare_scorecard.py --skip-scan`: aggregate quality is 81.0 %, against
  80.7 % in the Phase 0 report. Per-book match regression and per-book
  violation regression both pass; Phase 0's `d6dd5587` 11 → 12 failure is
  gone. It still FAILS zero-overlaps with 7. These are the same 7 as in
  Phase 0, all on books with no local PDF (`59158d87` ×3, `7a92f6d0` ×2,
  `641b8d6a`, `e2a410e4`), which cannot be rebaked here. Likewise, the
  report's 220 over-tall regions are all on the 29 books with no local PDF;
  there are 0 on the 27 local books.
- Reader: it was not running. It has been started (`python3 -m interaktiv_gtk`)
  on the new bakes.

## Left for later phases

- `clean_page_activities`' de-overlap drops a part (`to_remove`) without
  remapping its sub-items' `part_index`. The new drop in `snap_edges` does
  remap. This is pre-existing, and I did not change it here.
- `legal_planes` finds no seam in tightly set text, because its line boxes
  overlap. `tall_floor` works around that with a 1 pt edge tolerance of its
  own. The de-overlap passes still use the strict test, and a Phase 3 fix to
  side and bottom cuts may want the same tolerance there.
- Phase 2's screen should now see `SNAP_EXPAND_SHARE`, `SNAP_PANEL_RATIO`
  and `SNAP_PASSES` in `SPEC`, which is its precondition check.
