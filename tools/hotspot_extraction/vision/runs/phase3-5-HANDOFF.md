# Phases 3–5 handoff — first student, end to end

Date: 2026-10-01. Plan: `tools/hotspot_extraction/vision/PLAN.md` (see the dated note above Phase 3).
Previous handoff: `phase2-HANDOFF.md`. Branch `vision-student`, not committed.

**Status: the whole chain runs (labels → dataset → student → boxes → snapped bake → comparison).
The first student is not good enough to replace the rules bake. `activities/books/` is untouched.**

## What was built

| File | What it does |
| --- | --- |
| `dataset.py` | Trusted pages from one or more `verdicts.jsonl` → `data/vision/dataset/{train,valid,heldout}/` (symlinked JPEGs, `_annotations.coco.json`), split by book, `--check`. |
| `train.py` | RF-DETR Medium fine-tune; `--smoke`, `--resume`; no flip, no crop; one line per run in `runs/train-runs.jsonl`. |
| `evaluate.py` | mAP50, mAP50-95, precision and recall at a confidence, boxes on empty pages, per book. |
| `infer.py` | One `data/vision/pred/<book>/boxes.json` per book (confidence ≥ 0.5, NMS 0.5). |
| `snap.py` | Boxes → points → seam snap → marker label / headline / questions → `clean_page_activities`. |
| `scan.py --engine vision` | Reads `boxes.json` instead of running the rules detector; stamps `engine` and `checkpoint`. |
| `compare.py` | Both engines' bakes through `score_baked`; `--sheets N` draws pages rules beside vision. |
| `test_dataset.py`, `test_evaluate.py`, `test_phase5.py` | 44 tests in `vision/` pass; the 88 scanner tests pass. |

## Numbers

- Labels: 718 CLI pages (573 trusted) + 66 API pages (56 trusted) → 327 train / 65 valid / 187 held-out pages,
  702 / 101 / 352 boxes, 26–43 % empty pages.
- Flash-Lite probe (`gemini-3.5-flash-lite`, 66 pages): agreement 0.55 with the API labels, 0.56 with the CLI
  labels (the two trusted sources agree at 0.67 on these pages); 99 of 176 requests rate-limited. Not a teacher.
- Training `student-1`: 40 epochs in 547 s, peak VRAM 4.3 GB, best epoch 30.
- Validation (`runs/eval-student-1-valid.json`): mAP50 0.862, mAP50-95 0.745, precision 0.814, recall 0.822 at
  confidence 0.5; 0 of 28 empty pages got a box. ELT workbook `550e601a` 0.963; geography `7763e45b` 0.670.
- Held-out: **not scored yet** (once per phase; kept for the student that is meant to ship).
- Engines on the two validation books (`runs/phase5-compare-valid.json`):

| Engine | Regions | Cuts | Cuts/region | Overlaps | Slivers | Tall | Match | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rules | 605 | 28 | 0.046 | 0 | 0 | 0 | 0.911 | 0.525 |
| vision | 432 | 23 | 0.053 | 0 | 0 | 0 | 0.821 | 0.637 |

## What the pages show (`data/vision/sheets/engines/`)

Better with vision: a crossword taken whole with all its photos (`550e601a` p38); no hotspot on a unit title or a
QR strip where the rules engine draws one (`7763e45b` p196, p229).
Worse with vision: activities missed outright (`550e601a` p19 keeps one of three); the cleanup damages good
boxes — `snap_edges` retreats off a figure whose rect pokes into the next column and the box loses its dialogue
(`0a3fbb41` p28), a table activity is cut to its second table plus a one-line sliver (`7763e45b` p155), and
de-overlap leaves a thin strip above a question (`7763e45b` p197).

## Update, later on 2026-10-01: snapping fix, round 1 complete, self-training

- **Snapping.** `snap.py` no longer calls `clean_page_activities` / `snap_edges`. Its own `settle` drops a box
  lying mostly inside a more confident one, takes a block the box mostly holds on every free side, leaves a
  block it only clips when that is cheap, and parts remaining overlaps at a seam. `0a3fbb41` p28 keeps its
  dialogue. The rules engine's code is unchanged.
- **Round 1 is complete**: the 497 missing pages were labelled through the Antigravity CLI (archived script,
  run from outside the repo; labels in `~/Projects/interaktiv-local-teacher/data/labels/agy/v1-g38`, reached
  through the gitignored symlink `data/vision/labels/agy`). 1,215 pages, 1,001 trusted
  (`runs/phase2-qa-round1.json`); 570 requests in 20 minutes.
- **Self-training** (`self_label.py`): a page is taken only when every box the student sees on it scores
  0.9 or more; training books only; the picks go through `qa.py`.
- The validation books are pinned (`550e601a`, `7763e45b`) so students are comparable.

| Student | Trained on | mAP50 | mAP50-95 | Precision | Recall | Boxes on empty pages |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| student-1 | 327 teacher pages | 0.862 | 0.745 | 0.814 | 0.822 | 0 / 28 |
| student-2 | 327 teacher + 732 self (by student-1) | 0.863 | 0.765 | 0.839 | 0.772 | 1 / 28 |
| student-3 | 602 teacher pages (full round 1) | 0.826 | 0.749 | 0.800 | 0.713 | 0 / 28 |
| student-4 | 602 teacher + 221 self (by student-3) | 0.877 | 0.782 | 0.950 | 0.752 | 0 / 28 |

Student-4 is the one to carry forward (precision first). Engines on the two validation books with student-4
and the new snapping (`runs/phase5-compare-valid.json`):

| Engine | Regions | Cuts | Cuts/region | Overlaps | Slivers | Tall | Match | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rules | 605 | 28 | 0.046 | 0 | 0 | 0 | 0.911 | 0.525 |
| vision | 322 | 13 | 0.040 | 0 | 0 | 0 | 0.786 | 0.631 |

Still wrong with student-4 (sheets in `data/vision/sheets/engines/`): a prose panel boxed as an activity while
the two questions under it are missed (`7763e45b` p129); a dialogue boxed instead of the activity that uses
it (`550e601a` p39); where "questions 15–17 use this table" the table goes to no question or to one
(`7763e45b` p167) — the shared-block rule the teacher prompt still lacks. Recall on the geography book is
0.57: the student is cautious, which is the trade the user chose, but it is the number to raise next.

## Held-out score and the all-books comparison (student-4, 2026-10-01 evening)

Held-out, scored once (`runs/eval-student-4-heldout.json`, 340 pages, 672 teacher boxes): mAP50 0.776,
mAP50-95 0.673, precision 0.827, recall 0.677, 9 of 83 empty pages got a box. ELT books 0.91–0.96 mAP50;
subject books 0.51–0.82.

All 27 books baked with the vision engine into `data/vision/bake/` (264 s, peak 0.6 GB RAM), both engines scored
(`runs/phase5-compare-all.json`, sheets in `data/vision/sheets/engines-all/`):

| Split | Engine | Regions | Cuts | Cuts/region | Overlaps | Slivers | Tall | Match | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| held-out (9) | rules | 4275 | 320 | 0.075 | 0 | 0 | 0 | 0.950 | 0.419 |
| held-out (9) | vision | 3062 | 140 | 0.046 | 0 | 0 | 0 | 0.395 | 0.572 |
| train (18) | rules | 12685 | 782 | 0.062 | 0 | 0 | 0 | 0.926 | 0.506 |
| train (18) | vision | 7338 | 348 | 0.047 | 0 | 0 | 0 | 0.552 | 0.615 |

**The vision engine does not ship.** Violations are lower, but of the 952 publisher entries the rules bake
joins 887 and the vision bake 491: 198 sit on pages where the student drew nothing, 183 have a region on the
page that the join calls too far from the icon (`drift`), 49 are label mismatches. The rules engine grows a
region from every publisher icon, so its match is high by construction; the student has no such signal.

**Shared block: none of them** (user, 2026-10-01). Prompt v2 says so; the 214 `re-ask` pages were re-asked
with it (`labels/agy/v2-reask-g38`).

## Prompt v2 re-ask and student-5

214 `re-ask` pages re-asked with prompt v2 (237 requests, 466 s): 136 now trusted, 78 rejected
(`runs/phase2-qa-round1-reask.json`); 1,137 of 1,215 teacher pages trusted. Dataset: 896 train / 79 valid /
384 held-out pages. On the enlarged validation set (79 pages, 141 boxes; `runs/eval-student-{4,5}-valid79.json`):

| Student | Trained on | mAP50 | mAP50-95 | Precision | Recall | Boxes on empty pages |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| student-4 | 602 teacher + 221 self | 0.853 | 0.768 | 0.905 | 0.745 | 0 / 28 |
| student-5 | 675 teacher (v1 + v2 re-ask) + 221 self | 0.853 | 0.782 | 0.746 | 0.731 | 1 / 28 |

Student-5 is not better: same mAP50, clearly lower precision at confidence 0.5. **Student-4 stays the best
checkpoint.** Five students between 0.83 and 0.88 mAP50 on validation suggest the limit is no longer the
number of labels: the two teacher sources agree with each other at only 0.67 (IoU 0.7), and the training set
now mixes v1 labels (a shared block inside each box, or inside one) with v2 labels (inside none).

## Round 2 selection (active learning), 2026-10-02

`select.py --round 2` picks, from the 16 training books only, the unlabelled pages student-4 is least sure of.
It reads the student's boxes down to confidence 0.2 (`infer.py --conf 0.2 --out data/vision/pred-doubt`, 105 s
for the 16 books; the bake's `data/vision/pred/` is untouched). A page where the publisher lists more activities
than the student found at 0.5 goes first; the rest are ranked by their most doubtful box (a box at 0.5 is the
most doubtful). 1,829 unlabelled pages qualify; `data/vision/select/round2.json` holds 540 (38 a book; the two
ELT workbooks have only 1 and 7 doubtful pages), 78 of them with a missing publisher activity.
**Not sent to the teacher yet** (ask first: about 600 requests).

## Teacher round 2 and student-6, 2026-10-02

The 540 pages were labelled with prompt v2 and `--hint`: 480 through the Antigravity CLI
(`labels/agy/v2-round2-g38`, 576 requests, 1,492 s) and 60 through the free-tier API keys
(`labels/api/v2-round2`, 159 requests, 337 s, no key spent). The gate trusts 467 (415 + 52); 73 are `re-ask`
and are not trained on (`runs/phase6-qa-round2-{agy,api}.json`). Dataset: 1,362 train pages / 3,446 boxes;
validation and held-out unchanged. Student-6: 20 epochs, 1,028 s, peak VRAM 4.3 GB, best epoch 18.

On the 79 validation pages (`runs/eval-student-6-valid79.json`):

| Student | Confidence | mAP50 | mAP50-95 | Precision | Recall | Boxes on empty pages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| student-4 | 0.5 | 0.853 | 0.768 | 0.905 | 0.745 | 0 / 28 |
| student-6 | 0.5 | 0.921 | 0.854 | 0.867 | 0.830 | 1 / 28 |
| student-6 | 0.6 | 0.921 | 0.854 | 0.908 | 0.773 | 0 / 28 |
| student-6 | 0.7 | 0.921 | 0.854 | 0.936 | 0.731 | 0 / 28 |

**Student-6 is the best checkpoint**: at confidence 0.6 it matches student-4's precision with more recall and
no box on an empty page, so 0.6 is the confidence to bake it at. Geography (`7763e45b`) is still the weak
book: mAP50 0.852, precision 0.827 and recall 0.682 at 0.6. Not yet baked, not yet scored on held-out.

## Student-6 on all books and on held-out, 2026-10-02

Infer (0.6) 164 s, bake of 27 books with 6 workers 133 s (peak 0.9 GB USS), compare 3 s: 316 s in all.
Held-out, scored once (`runs/eval-student-6-heldout.json`, 384 pages, 841 teacher boxes, confidence 0.6):
mAP50 0.786, mAP50-95 0.696, precision 0.838, recall 0.647, 8 of 83 empty pages got a box (student-4, on
340 pages at 0.5: 0.776 / 0.673 / 0.827 / 0.677, 9 of 83). The gain on validation (+0.07 mAP50) did not carry
over to the held-out books.

Engines (`runs/phase6-compare-all-student-6.json`, sheets in `data/vision/sheets/engines-all-student-6/`):

| Split | Engine | Regions | Cuts | Cuts/region | Overlaps | Slivers | Tall | Match | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| held-out (9) | rules | 4275 | 320 | 0.075 | 0 | 0 | 0 | 0.950 | 0.419 |
| held-out (9) | vision, student-6 | 3060 | 142 | 0.046 | 0 | 0 | 0 | 0.345 | 0.520 |
| train (18) | rules | 12685 | 782 | 0.062 | 0 | 0 | 0 | 0.926 | 0.506 |
| train (18) | vision, student-6 | 7627 | 263 | 0.035 | 0 | 0 | 0 | 0.557 | 0.576 |

**The vision engine still does not ship.** Held-out match fell from 87 to 76 of 220 publisher entries (the
higher confidence costs boxes); on the training books cuts fell from 348 to 263 with match unchanged.
More labels of the same kind are not closing the match gap.

## Why publisher entries go unmatched (student-6), 2026-10-02

`unmatched.py` (`runs/phase6-unmatched-student-6.json`) files every publisher entry by the scorer's own bucket
for both bakes and looks at the page for each one the vision bake misses.

| Split | Entries | Rules ok | Vision ok | No box on page | Box elsewhere on page | Icon beside a box | Icon in a box |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| held-out | 220 | 209 | 76 | 69 | 40 | 30 | 5 |
| train | 732 | 678 | 408 | 128 | 103 | 89 | 4 |

Where the rules bake matched the entry, a vision region covers half or more of the rules region for only 11 of
111 (held-out) and 28 of 156 (train): the student is not drawing these things at all, the join is not at fault.
The losses sit in a few books: chemistry `6e4f65dc` 4 of 78 matched (58 on pages with no box), mathematics
`753fcdb0` 6 of 96, `51cdbbce` 10 of 38, `a0e5ed1a` 5 of 47, biology `09f62a7e` 2 of 21. The ELT books match
(`0e966773` 151 of 165, `c9f63718` 24 of 27).

The titles and the pages say why: in the subject books the publisher's icons stand beside worked examples
("örnek"), information and reminder boxes ("bilgi kutusu", "Hatırlatma"), opening photographs, proofs,
simulations and concept maps. Those are not exercises, and the teacher prompt says body text that is not an
exercise gets no box, so the student draws nothing there, as told. The rules engine grows a region from
every icon whatever it stands beside. **Match, as scored, measures a thing the vision engine was told not
to do.** Whether an icon beside a worked example should get a hotspot is the user's decision.

## Next, in order

1. The user decides what an icon beside non-exercise content gets (see above). The plan follows from that.
2. Either way, about 30 held-out and 89 training entries have the icon level with a vision box that the join
   refuses (mostly `drift`): worth reading on the sheets before changing the join.

## Reproduce

```bash
PY=.venv-vision/bin/python; V=tools/hotspot_extraction/vision
$PY $V/qa.py --labels ~/Projects/interaktiv-local-teacher/data/labels/agy/v1-g38 --out data/vision/labels/verdicts-cli-g38.jsonl
$PY $V/qa.py --labels data/vision/labels/api/v1 --out data/vision/labels/verdicts-api-v1.jsonl
$PY $V/dataset.py --verdicts data/vision/labels/verdicts-cli-g38.jsonl data/vision/labels/verdicts-api-v1.jsonl
$PY $V/train.py --smoke && $PY $V/train.py --run student-1          # ask first: ~10 min of GPU
$PY $V/evaluate.py --run student-1 --split valid
$PY $V/infer.py --run student-1 --only 550e601a,7763e45b
$PY tools/hotspot_extraction/scan.py --engine vision --only <ids> --out data/vision/bake/activities/books
$PY $V/compare.py --sheets 8
```
