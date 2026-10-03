# Phase 7 handoff — why the baked boxes were inaccurate, and the fix

Date: 2026-10-02 (evening). Follows `report.md` and `phase3-5-HANDOFF.md`. Branch `vision-student`, not committed.

**Status: the code is changed, tested, and (with the user's yes) all 27 books are re-baked into
`data/vision/bake-2/` with `--icons bind` and scored; held-out was scored once. The vision engine still does
not ship: the match gate fails, as the user accepted when choosing pins. `data/vision/bake/` (the old vision
bake) and `activities/books/` (the shipped rules bake) are untouched. The low-confidence inference has not
been run.**

**The user's answers, 2026-10-02:** an icon beside something that is not an exercise stays a pin
(`--icons bind`); an activity taller than 70 % of the sheet gets no hotspot.

## What was wrong

`report.md` blamed the match gap on the publisher's icons. That is true for match, but it is not the
accuracy problem. Measured against the teacher's boxes, **the bake was making the student's boxes worse**:

1. **The rules cleanup was still running over the vision regions.** `snap.py` stopped calling
   `clean_page_activities` / `snap_edges` on 2026-10-01, but `serialize_book_regions` calls
   `clean_page_activities(acts, geom)` on every page of every bake, so `snap_edges` moved the edges again
   after `snap.py` had finished. It retreats off any drawn shape a box crosses: a page banner, a column
   rule, ruled "cells" that the block detector reads across two columns.
2. **`seam_snap` went to the nearest clear seam, wherever it was.** An edge 2 pt inside a drop capital's
   box jumped above the last question and dropped it (`09f62a7e` p17). Lines of formulas, whose boxes
   overlap, lost their first line (`753fcdb0` p58).
3. **`fit_blocks` took and left blocks without asking what the box holds.** A box crossing the page banner
   stepped back under it and lost its first line; a box on a tinted background grew over its neighbours' text.
4. **A box taller than 70 % of the sheet was cut down to 70 %**, leaving a hotspot over the top of an
   activity and not its foot.

## What changed

| File | Change |
| --- | --- |
| `scanner/serializer.py` | `serialize_book_regions(..., clean=True)`. `clean=False` writes regions as handed over (the overlap assert still runs). Default unchanged, so the rules bake is byte-for-byte what it was. |
| `scan.py` | `--engine vision` passes `clean=False`; new `--icons {off,bind,blocks,grow}` (default `bind`) and `--conf`; `conf` and `icons` are stamped in `regions.json` and are part of the "is this bake current" check. |
| `vision/snap.py` | `seam_snap`: an edge moves only when it cuts through a line's letters (not its ascender room), out past a line the box holds, back inside one it clips, never past a line it holds, never further than one line. `fit_blocks`: takes a block only if that brings no foreign line, leaves one only if that loses no line the box holds; two boxes on one block grow to their shared boundary. A box taller than a hotspot may be is left out, before nesting is judged, so the boxes inside it stand. |
| `vision/icons.py` (new) | What the publisher's icons do after the boxes are settled: `admit`, `bind`, `take_blocks`, `grow`. See below. |
| `vision/accuracy.py` (new) | Baked regions against the teacher's boxes: IoU, and "same content" (the region holds exactly the lines and blocks the teacher's box holds). The scorecard never measured this. |
| `vision/test_snap_icons.py` (new), `test_phase5.py` | 13 new tests; one old expectation updated. 154 tests pass (`vision/` + `scanner/`). |

## Numbers (8 development books; none is held-out)

Baked regions against the teacher (`runs/phase7-accuracy-*.json`). "Student" is the raw box, the ceiling
snapping can reach; "worse" counts teacher boxes whose best region holds less of the right content than
the student's own box did.

| Pages | Teacher boxes | | IoU ≥ 0.85 | Same content | Worse than the student drew |
| --- | ---: | --- | ---: | ---: | ---: |
| validation, 2 books, never trained on | 133 | student | 0.737 | 0.692 | |
| | | bake before | 0.647 | 0.654 | 10 |
| | | **bake now** | **0.699** | **0.677** | **5** |
| 6 training books | 1,354 | student | 0.863 | 0.849 | |
| | | bake before | 0.696 | 0.750 | 131 |
| | | **bake now** | **0.826** | **0.837** | **24** |

Scorecard on the same 8 books (`runs/phase7-compare-dev8-icons-*.json`):

| Engine | Regions | Cuts | Cuts/region | Overlaps | Slivers | Tall | Match | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rules | 6689 | 382 | 0.057 | 0 | 0 | 0 | 0.921 | 0.540 |
| vision, before | 3646 | 170 | 0.047 | 0 | 0 | 0 | 0.554 | 0.630 |
| vision now, `--icons bind` | 3467 | 341 | 0.098 | 0 | 0 | 0 | 0.563 | 0.582 |
| vision now, `--icons blocks` | 3513 | 357 | 0.102 | 0 | 0 | 0 | 0.647 | 0.586 |
| vision now, `--icons grow` | 3639 | 393 | 0.108 | 0 | 0 | 0 | 0.835 | 0.589 |

**Read the cuts column with care.** The count doubled, and the boxes are better. 178 of the 341 cuts are on
a drawn "block" that two regions each hold a real share of: a ruled grid the detector reads across the
column gutter from one activity's table into its neighbour's notepad (`0e966773` p78), or across two
questions' coordinate grids (`a0e5ed1a` p241). No region can take such a block whole without swallowing
the next activity. The old bake "cleared" them by stepping a box back off its own content. Without those
the rate is 0.048. This is a fault of the ruler's block detection (`detect_solution_spaces`), not of the
regions; it was not changed here, because the same ruler scores the rules engine.

## All 27 books (`data/vision/bake-2/`, `--icons bind`; bake 132 s, peak 1.1 GB RSS)

Scorecard (`runs/phase7-compare-all.json`, `runs/phase7-unmatched.json`):

| Split | Engine | Regions | Cuts | Cuts/region | Overlaps | Slivers | Tall | Match | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| held-out (9) | rules | 4275 | 320 | 0.075 | 0 | 0 | 0 | 0.950 | 0.419 |
| held-out (9) | vision, old bake | 3060 | 142 | 0.046 | 0 | 0 | 0 | 0.345 | 0.520 |
| held-out (9) | **vision, now** | 2950 | 204 | 0.069 | 0 | 0 | 0 | 0.359 | 0.461 |
| train (18) | rules | 12685 | 782 | 0.062 | 0 | 0 | 0 | 0.926 | 0.506 |
| train (18) | vision, old bake | 7627 | 263 | 0.035 | 0 | 0 | 0 | 0.557 | 0.576 |
| train (18) | **vision, now** | 7191 | 430 | 0.060 | 0 | 0 | 0 | 0.544 | 0.517 |

Gate: overlaps, slivers, tall are 0; cuts/region is below rules on held-out in total (0.069 against 0.075),
and in 4 of 9 held-out books taken singly; match is within 2 points of rules in 0 of the 25
books that have publisher entries. Coverage fell because the tall boxes (large, full of answer space) are gone.

Baked regions against the teacher (`runs/phase7-accuracy-{valid,train,heldout}-{before,after}.json`):

| Split | Teacher boxes (not tall) | | IoU ≥ 0.5 | IoU ≥ 0.85 | Same content | Worse than the student drew |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| train, 16 books | 3,266 | student | 0.893 | 0.818 | 0.809 | |
| | | old bake | 0.886 | 0.706 | 0.752 | 196 |
| | | **now** | 0.891 | 0.791 | 0.800 | 49 |
| validation, 2 books | 133 | student | 0.797 | 0.737 | 0.692 | |
| | | old bake | 0.782 | 0.647 | 0.654 | 10 |
| | | **now** | 0.789 | 0.699 | 0.677 | 5 |
| held-out, 9 books | 812 | student | 0.639 | 0.580 | 0.632 | |
| | | old bake | 0.631 | 0.558 | 0.616 | 29 |
| | | **now** | 0.634 | 0.565 | 0.616 | 25 |

**What this says.** On books the student knows, the bake now hands on almost exactly what the student drew.
On the held-out books the bake was never the main loss and the fix moves little: there the student itself
finds only 64 % of the teacher's boxes at IoU 0.5 and holds the right content for 63 %. The accuracy
problem that remains on unseen books is the model (it misses activities and merges neighbours), and that
is fixed with labels and training, not geometry.

Per book:

| Book | Split | Rules regions | Rules cuts/region | Rules match | Vision regions | Vision cuts/region | Vision match | Old vision cuts/region | Old vision match |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `11941059` | heldout | 367 | 0.027 | 0.970 | 219 | 0.000 | 0.606 | 0.000 | 0.606 |
| `3a4479c7` | heldout | 571 | 0.077 | - | 165 | 0.000 | - | 0.018 | - |
| `51cdbbce` | heldout | 1290 | 0.033 | 0.868 | 1013 | 0.114 | 0.316 | 0.032 | 0.263 |
| `6e4f65dc` | heldout | 78 | 0.000 | 1.000 | 171 | 0.111 | 0.026 | 0.037 | 0.051 |
| `79cbecfa` | heldout | 485 | 0.235 | 0.875 | 222 | 0.086 | 0.125 | 0.092 | 0.000 |
| `7fad03e9` | heldout | 614 | 0.059 | 1.000 | 279 | 0.061 | 0.333 | 0.119 | 0.000 |
| `a7a7886a` | heldout | 4 | 0.000 | 1.000 | 167 | 0.000 | 0.750 | 0.000 | 0.750 |
| `c9f63718` | heldout | 179 | 0.140 | 0.963 | 119 | 0.176 | 0.815 | 0.224 | 0.889 |
| `cb558332` | heldout | 687 | 0.071 | 0.897 | 595 | 0.022 | 0.621 | 0.027 | 0.517 |
| `09f62a7e` | train | 440 | 0.004 | 1.000 | 208 | 0.043 | 0.143 | 0.004 | 0.095 |
| `0a3fbb41` | train | 717 | 0.066 | 0.895 | 488 | 0.115 | 0.789 | 0.132 | 0.789 |
| `0e966773` | train | 529 | 0.085 | 0.976 | 503 | 0.086 | 0.921 | 0.058 | 0.915 |
| `1903e365` | train | 563 | 0.092 | 1.000 | 319 | 0.028 | 0.000 | 0.039 | 0.000 |
| `1cc573f6` | train | 606 | 0.038 | 0.875 | 473 | 0.013 | 0.312 | 0.045 | 0.344 |
| `3d372038` | train | 370 | 0.011 | 0.955 | 125 | 0.016 | 0.864 | 0.016 | 0.864 |
| `4442fde4` | train | 789 | 0.077 | - | 499 | 0.092 | - | 0.047 | - |
| `550e601a` | train | 174 | 0.029 | 0.898 | 125 | 0.088 | 0.673 | 0.083 | 0.735 |
| `66098271` | train | 899 | 0.120 | 0.857 | 513 | 0.018 | 0.393 | 0.002 | 0.714 |
| `753fcdb0` | train | 1907 | 0.051 | 0.802 | 851 | 0.105 | 0.062 | 0.025 | 0.062 |
| `7763e45b` | train | 431 | 0.053 | 1.000 | 185 | 0.000 | 0.143 | 0.005 | 0.143 |
| `93bf209f` | train | 433 | 0.014 | 1.000 | 318 | 0.013 | 0.533 | 0.025 | 0.467 |
| `a0e5ed1a` | train | 2011 | 0.060 | 0.851 | 865 | 0.148 | 0.213 | 0.039 | 0.106 |
| `ad3f3275` | train | 5 | 0.000 | 1.000 | 88 | 0.000 | 0.400 | 0.000 | 0.600 |
| `afec0d89` | train | 636 | 0.113 | 0.929 | 330 | 0.006 | 0.286 | 0.006 | 0.500 |
| `d13a75d3` | train | 480 | 0.087 | 1.000 | 242 | 0.021 | 0.500 | 0.013 | 0.488 |
| `d6dd5587` | train | 838 | 0.001 | 1.000 | 599 | 0.003 | 0.645 | 0.002 | 0.677 |
| `df1e313c` | train | 857 | 0.085 | 0.966 | 460 | 0.020 | 0.552 | 0.032 | 0.586 |

Before/after pages to look at: `data/vision/sheets/phase7-before-after/`.

## The publisher's icons (`icons.py`)

- `admit`: a box below the bake's confidence stands when an icon is hung at it and no surer box is.
  **Inert today**: `data/vision/pred/` holds only boxes ≥ 0.6. It needs `infer.py --conf 0.3` into a new
  folder (about 3 min of GPU) and then `scan.py --conf 0.6`. A rough proxy (student-4's 0.2 boxes on the
  16 training books): 73 of 262 unanswered icons have such a box; 189 have none even at 0.2.
- `bind` (default): an icon hung beside a region the join left unpaired names it (`ogeId`), the region is
  not touched. The join measures an icon against a region's top; the student's regions start at the
  picture above the question, so the join called them `drift`. Worth 13 entries of 547 on the 8 books.
- `blocks`: an unanswered icon hung at a panel (holding ≥ 3 lines) or a figure no region stands on makes
  that block a region, whole. This is `report.md` option 1 in its strict form.
- `grow`: any icon still unanswered gets `anchors._create_anchored_region`'s region, cut back off its
  neighbours. This is option 3. On the pages looked at it reproduces the rules engine's fragments (one
  hotspot per table row beside a "Hatırlatma" box, a strip round "ÇÖZÜM").

Match by book against the gate (not more than 2 points under rules): 0 of 8 books pass with `bind` or
`blocks`, 4 of 8 with `grow`. **The match gate cannot be met by the vision engine unless it grows a region
from every icon the way the rules engine does**; on subject books most icons stand where the student
draws nothing.

## Decided, and still open

1. **Decided: pin only.** An icon beside something that is not an exercise makes no hotspot (`--icons bind`).
   The reader already draws a pin for an entry no region claims, so nothing becomes unreachable. `blocks`
   and `grow` stay in the code as switches and are not used.
2. **Decided: tall activities are left out.** 180 of 3,446 teacher boxes on the training books (5.2 %) are
   taller than 70 % of the sheet: a column-high "E-Portfolio" task, a full-page reading with its questions.
3. **Open: the gate itself.** With pins chosen, "match within 2 points of rules on every book" cannot be
   met by design, and the cut count includes blocks that are not objects. The gate needs restating before
   it can say whether the vision engine ships; `accuracy.py` against the teacher is the candidate measure.
4. **Open, needs a yes:** `infer.py --conf 0.3` (about 3 min of GPU) so that `admit` has doubtful boxes to
   work with; and anything that improves the student on unseen subject books (labels, training).

## Not done

- The student still merges neighbouring activities into one box on unseen books (`11941059` p27, three
  activities in one 0.79 box) and misses boxed tasks ("Let's Discover", `0e966773` p34). That is recall
  and segmentation of the model, not snapping; it needs labels or training, both of which need a yes.
- Round-2 hint pages taught the student to box whatever an icon marks ("make sure each has a box"), while
  the prompt says body text gets none. The training set is inconsistent on exactly the §4 question.
- `marker_for` labels a box from any marker-like glyph in its top-left quarter; on `a0e5ed1a` p16 it read
  the `b` of "b ∈ ℝ" as label "b". A wrong label makes the join refuse the right entry.

## Reproduce

```bash
PY=.venv-vision/bin/python; V=tools/hotspot_extraction/vision
DEV=d13a75d3,0e966773,0a3fbb41,a0e5ed1a,09f62a7e,753fcdb0,550e601a,7763e45b
$PY -m pytest $V tools/hotspot_extraction/scanner -q                        # 154 pass
$PY tools/hotspot_extraction/scan.py --engine vision --icons bind --only $DEV --force --workers 3 --quiet \
    --out /tmp/bake-bind/activities/books                                   # 85 s, 0.7 GB
$PY $V/compare.py --vision-root /tmp/bake-bind --only $DEV --workers 3
$PY $V/accuracy.py --split valid --bake-root /tmp/bake-bind
$PY $V/accuracy.py --split valid                                            # the bake in data/vision/bake
# all 27 books (ask first):
$PY tools/hotspot_extraction/scan.py --engine vision --icons bind --all --force --workers 6 --quiet \
    --out data/vision/bake-2/activities/books
```
