# Phase 8 — the student on unseen books (started 2026-10-02, late)

Follows `phase7-snap-icons-HANDOFF.md`. Branch `vision-student`, not committed. Sections 1 and 2 used no
GPU; section 3 did, with the user's yes. **`activities/books/` now holds the vision bake `bake-4` for the
27 local books** (section 4). The teacher and the network were not used.

## 1. Both engines against the teacher's boxes (the comparison the gate never made)

`accuracy.py` now also counts the other direction: regions that answer no teacher box (best IoU under 0.5)
and pages the teacher left empty that carry a region. `--bake-root .` scores the shipped rules bake.
Files: `runs/phase8-accuracy-{valid,heldout,train}-{rules,vision}.json`.

| Split | Engine | Teacher boxes found (IoU ≥ 0.5) | Same content | Regions answering no teacher box | Empty pages with a region |
| --- | --- | ---: | ---: | ---: | ---: |
| held-out, 9 books | rules (shipped) | 0.379 | 0.289 | 674 of 985 (68.4 %) | 18 of 83 |
| held-out, 9 books | vision (`bake-2`) | 0.634 | 0.616 | 100 of 617 (16.2 %) | 7 of 83 |
| validation, 2 books | rules (shipped) | 0.669 | 0.526 | 126 of 217 (58.1 %) | 5 of 28 |
| validation, 2 books | vision (`bake-2`) | 0.789 | 0.677 | 10 of 115 (8.7 %) | 0 of 28 |
| train, 16 books | rules (shipped) | 0.536 | 0.450 | 2,835 of 4,639 (61.1 %) | 96 of 265 |
| train, 16 books | vision (`bake-2`) | 0.891 | 0.800 | 101 of 3,013 (3.4 %) | 3 of 265 |

Two cautions before reading this as "vision wins":

- **The referee is the student's own teacher.** The student was trained to imitate these boxes; the rules
  engine was written before prompt v2 existed and draws by another definition (a region per icon, a region
  per numbered question). Part of its "regions answering no teacher box" are things the teacher was told
  not to box (worked examples, information boxes), which the user has since decided are pins, not hotspots.
- **Nobody has hand-checked the teacher.** The labels passed `qa.py`; the two teacher sources agree with
  each other at 0.67 (IoU 0.7).

With that said: the teacher prompt *is* the user's region rules, and by it the shipped bake finds 38 % of
the activities on unseen books and two thirds of its hotspots are on something else; the vision bake finds
63 % with one hotspot in six on something else. Held-out was read here for the rules bake only; nothing was
tuned on it.

## 2. What the student gets wrong (validation and training pages, student-6 at 0.6)

| Teacher box is… | validation (141) | training (3,446) |
| --- | ---: | ---: |
| found (IoU ≥ 0.5) | 77.3 % | 89.4 % |
| not boxed at all | 16.3 % | 8.6 % |
| merged with a neighbour in one student box | 2.8 % | 0.9 % |
| partly boxed, split, loose | 3.5 % | 1.1 % |

Misses dominate, not merges. Thin boxes (under 5 % of the page height, a one-line question) are found
less often: 2 of 7 on validation, 76 % on training pages against 89 % overall.

**The student has only ever been trained at RF-DETR Medium's default input, 576 × 576 px**
(`train-runs.jsonl`: `"resolution": null` in all six runs). A 1024 × 1445 page is squeezed to that; a
20 px line of type becomes 8 px. `train.py --resolution` exists and has never been used.

## 3. What was run on 2026-10-02 with the user's yes (17.5 min of GPU in all, limit given: 30)

The user asked for the vision bake in the reader, and for "whichever GPU run gives the best results" in
under 30 minutes without exhausting the machine.

| Step | Cost | Result |
| --- | --- | --- |
| `infer.py --run student-6 --all --conf 0.3 --out data/vision/pred-s6-low` | 176 s GPU, 4 GB VRAM | boxes down to 0.3 for `icons.admit` |
| `train.py --smoke --resolution 864` | 13 s, 3.6 GB | fine |
| `train.py --run student-7 --init student-6 --resolution 864 --epochs 6` (`--init` is new) | 554 s, 6.4 GB VRAM, RAM 9 of 14 GB at peak | best epoch 3, val mAP50-95 0.869 |
| `infer.py --run student-7 --resolution 864 --all --conf 0.3 --out data/vision/pred-s7-low` | 260 s | |
| bakes `bake-3` (student-6, 0.6) and `bake-4` (student-7, 0.7), both `--icons bind` with admit | 127 s CPU each at 6 workers | |

**`admit` works.** A box between 0.3 and the bake's confidence stands only when a publisher icon heads it
(hung at its upper half; tightened after one page where an icon at the foot of a passage admitted the
passage) and no surer box is there. With student-6 it adds 26 regions on held-out and 69 on the training
books, and match goes from 0.359 to 0.473 (held-out) and 0.544 to 0.635 (train) with cuts unchanged.
Against the teacher on validation: found 0.789 → 0.805, regions answering no teacher box 10 → 11.

**Student-7 (864 px) against student-6 (576 px), validation, raw boxes (`runs/eval-student-7-valid.json`):**

| Student | Confidence | mAP50 | mAP50-95 | Precision | Recall | Boxes on empty pages |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| student-6 | 0.6 | 0.921 | 0.854 | 0.908 | 0.773 | 0 / 28 |
| student-7 | 0.6 | 0.930 | 0.862 | 0.881 | 0.837 | 0 / 28 |
| student-7 | 0.7 | 0.930 | 0.862 | 0.910 | 0.787 | 0 / 28 |

At equal precision (0.91) student-7 has 1.4 points more recall: within the noise of 141 boxes. Its boxes
are tighter, which shows once baked (validation, with admit, 133 teacher boxes):

| Bake | Found (IoU ≥ 0.5) | IoU ≥ 0.85 | Same content | Regions answering no teacher box |
| --- | ---: | ---: | ---: | ---: |
| student-6 at 0.6 | 0.805 | 0.714 | 0.692 | 11 of 118 |
| student-7 at 0.7 | 0.812 | 0.759 | 0.737 | 13 of 121 |
| student-7 at 0.6 | 0.865 | 0.797 | 0.774 | 17 of 132 |

0.7 was taken (fewest hotspots on nothing; correctness before coverage). **`data/vision/bake-4/` = student-7
at 0.7, `--icons bind`, admit.** Scored once on held-out, after the choice was made
(`runs/phase8-accuracy-*-vision-student7.json`, `runs/phase8-compare-bake2-as-rules-vs-bake4-student7.json`;
in that file the row called "rules" is `bake-2`, because `activities/books/` no longer holds the rules bake):

| Held-out, 9 books | Found | Same content | Regions answering no teacher box | Cuts/region | Match |
| --- | ---: | ---: | ---: | ---: | ---: |
| rules (shipped until today) | 0.379 | 0.289 | 674 of 985 (68.4 %) | 0.075 | 0.950 |
| `bake-2`: student-6 at 0.6 | 0.634 | 0.616 | 100 of 617 (16.2 %) | 0.069 | 0.359 |
| `bake-4`: student-7 at 0.7 + admit | 0.623 | 0.605 | 93 of 603 (15.4 %) | 0.053 | 0.473 |

**The higher resolution did not help on unseen books.** `bake-4` is `bake-2`'s equal there within noise
(9 fewer activities found, 7 fewer hotspots on nothing); what it gains is fewer cuts and the icon-vouched
regions. Six epochs carried on from student-6 is a small test of resolution, not the last word, but it
says the limit on unseen books is what the student has seen (16 books), not how sharply it sees.

## 4. State of the working tree

- **`activities/books/` now holds `bake-4` for the 27 local books** (the user's yes, to look at it in the
  reader). 54 tracked files changed; `git checkout activities/books` restores the rules bake. The GTK
  tests (`python3 -m pytest tests`) pass on it; the reader was restarted.
- While that is so, `compare.py`'s "rules" rows are not the rules engine. The rules numbers are in
  `runs/phase7-compare-all.json` and `runs/phase8-accuracy-*-rules.json`.
- `data/vision/`: `bake/` old, `bake-2/` phase 7, `bake-3/` student-6 + admit (made before admit was
  tightened), `bake-4/` current; `pred/` student-6 ≥ 0.6, `pred-s6-low/`, `pred-s7-low/`;
  `checkpoints/student-7/`.

## 5. Next, as it stood on 2026-10-02 (see §6 to §8 for what 2026-10-03 did with it)

1. **The user looks at the reader** and says whether the vision bake stays. If yes: restate the gate
   (overlaps, slivers, tall are 0; more teacher boxes found and a lower share of regions answering none
   than the rules bake on held-out), make `--engine vision` the default, commit. If no: `git checkout
   activities/books`.
2. **More books, not more pixels (ask: downloads, teacher quota, GPU).** Sixteen training books is the
   limit. `activities_meta/` lists books that are not in `books/`; each new subject book rendered,
   labelled (about 40 pages a book, prompt v2, **no `--hint`**) and trained on is a new layout seen.
3. **Stop using `--hint` with the teacher**: "make sure each has a box" makes it box whatever an icon
   marks, against prompt v2 and the pin decision. 123 of the 540 round-2 pages carried a hint; re-asking
   those without it costs about 150 free requests (ask).
4. `marker_for` reads stray glyphs as labels (`phase7` handoff); a wrong label blocks the join.
5. The ruler's block detector reads grids across the column gutter (`phase7` handoff); until that is
   fixed the cut count overstates both engines.

## 6. 2026-10-03: the verdict, two fixes, `bake-5`

**The user's verdict: the vision bake stays.** `scan.py --engine vision` is the default (`VISION_PRED`,
`VISION_CONF` at the top of `scan.py` name the shipped student: `pred-s7-low`, 0.7); `--engine rules` is
the old path, and `compare_scorecard.py` passes it. The gate is restated in `PLAN.md` (Phase 5 note):
violations 0, and on held-out more teacher boxes found with a lower share of regions answering none than
the rules bake. Callers that bake a book the student has not seen (`package_book.py`, `fetch_and_bake.py`,
`build_library.py`) are now refused until `infer.py` has run for that book.

Old step 4, **stray glyphs read as labels**: `snap.stands_apart`. A marker labels a box only when it is set
as a label: it carries its bracket or full stop, or is bold, or is in another colour than the text it
opens. On `a0e5ed1a` p16 the detector's markers were `b x y 2 4 15 1 64 81`, every one the first glyph of
a line of mathematics. The rules engine's marker detector is untouched.

Old step 5, **grids read across the column gutter**: `detect_solution_spaces` linked any two vertical
rules side by side within 350 pt. Two that each meet a horizontal rule are already tied into a lattice of
their own and are no longer linked directly (`0e966773` p78: the table and the notepad are two blocks
now; `a0e5ed1a` p241: two coordinate grids). This is the shared ruler, so the rules engine's regions and
both engines' cut counts change; the rules bake has not been re-made with it.

159 tests pass (`vision/` + `scanner/`); the GTK tests pass on the new bake.

**`data/vision/bake-5/`** = `bake-4`'s boxes (student-7 at 0.7, `--icons bind`, admit) with both fixes,
27 books, 132 s CPU at 6 workers, 1.1 GB. It is what `activities/books/` now holds; the reader was
restarted on it. Files: `runs/phase8-compare-bake5.json` (both rows are `bake-5`).

| | Regions | Cuts/region | Overlaps, slivers, tall | Match | Found (IoU ≥ 0.5) | Same content | Answering no teacher box |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| held-out (9), rules, old ruler | 4275 | 0.075 | 0 | 0.950 | 0.379 | 0.289 | 674 of 985 (68.4 %) |
| held-out (9), `bake-4`, old ruler | 2818 | 0.053 | 0 | 0.473 | 0.623 | 0.605 | 93 of 603 (15.4 %) |
| held-out (9), `bake-5` | 2818 | 0.023 | 0 | 0.473 | 0.623 | 0.607 | 93 of 603 (15.4 %) |
| train (18), `bake-5` | 7224 | 0.041 | 0 | 0.637 | 0.900 | 0.830 | 78 of 3,019 (2.6 %) |
| validation (2), `bake-5` | | | | | 0.812 | 0.737 | 13 of 121 (10.7 %) |

The fall in cuts is the ruler, not the regions: on the 8 development books the same 3,490 regions went
from 382 cuts to 225.

## 7. 2026-10-03: the hint re-ask and eight new books (the user's yes to both; stopped by the daily quota)

- **Hint re-ask.** `data/vision/select/reask-nohint.json` lists the 123 round-2 pages whose label carried
  the hint (110 Antigravity, 13 API). `teacher.py --selection … --out-version v2-nohint` labelled **54 of
  123** and then every key reported its daily quota (71 requests today, on top of yesterday evening's 159
  in the same Pacific day). 69 pages remain.
- `dataset.py` no longer trains on a label that carried the hint, whatever its verdict
  (`dataset.hinted`). `qa.py --allow-miss` judges a label without holding an unboxed icon against it; use
  it for `v2-nohint` and round 3, since an icon beside non-exercise content is a pin.
- **Eight new books** (subjects the held-out books are weakest on, 0.66 GB): `641b8d6a` Kimya,
  `8a737a50` Matematik 1, `7a92f6d0` Türk Dili ve Edebiyatı, `5e3a8aab` Mantık, `1a87adf5` Sosyoloji 1,
  `e87588af` İnkılap Tarihi, `b0410882` Demokrasi ve İnsan Hakları, `6ae92909` Fizik. Downloaded to
  `books/`, rendered (1,963 pages, 51 s), given a **rules** bake in `activities/books/` (the selection and
  `qa.py` read folio and blocks from it; the student has not seen these books), and 40 pages each
  selected: `data/vision/select/round3.json`, 320 pages. **None is labelled yet.**
- The free tier gives about 200 requests a Pacific day over the ten keys; it resets at 10:00 Turkish time.
  389 pages are owed: two quota-days.

## 8. To finish, in order

```bash
PY=.venv-vision/bin/python; V=tools/hotspot_extraction/vision; L=data/vision/labels
# 1. after 10:00, and again the next day: both stop cleanly at the quota and resume where they stopped
$PY $V/teacher.py --selection data/vision/select/reask-nohint.json --out-version v2-nohint
$PY $V/teacher.py --selection data/vision/select/round3.json --out-version v2-round3
# 2. judge them (an unboxed icon is not a defect)
$PY $V/qa.py --labels $L/api/v2-nohint --selection data/vision/select/reask-nohint.json --allow-miss --out $L/verdicts-nohint.jsonl
$PY $V/qa.py --labels $L/api/v2-round3 --selection data/vision/select/round3.json --allow-miss --out $L/verdicts-round3.jsonl
# 3. dataset: no-hint first so it wins; --selection must name the new books (merge round1.json and round3.json)
$PY $V/dataset.py --selection <merged> --verdicts $L/verdicts-nohint.jsonl $L/verdicts-cli-g38.jsonl $L/verdicts-api-v1.jsonl \
    $L/verdicts-round2-agy.jsonl $L/verdicts-round2-api.jsonl $L/verdicts-round3.jsonl $L/verdicts-self-student-3.jsonl
# 4. GPU, the one retrain the user allowed on 2026-10-03; tell them before it starts (about 15 min, 6.4 GB VRAM)
$PY $V/train.py --run student-8 --init student-7 --resolution 864 --epochs 8
$PY $V/infer.py --run student-8 --resolution 864 --all --conf 0.3 --out data/vision/pred-s8-low
# 5. choose the confidence on validation, bake all 35 books, score held-out once, then VISION_PRED / VISION_CONF
```

The eight new books show in the reader with the rules engine's hotspots until step 5.

## 9. 2026-10-03, later: the labels, student-8, `bake-6` (not installed)

The user asked for the Antigravity CLI when the API keys ran out. `teacher_all_routes.py` was copied in
under a temporary name, run with `--route agy`, and deleted: 69 no-hint pages (`labels/agy/v2-nohint-g38`)
and 319 of the 320 round-3 pages (`labels/agy/v2-round3-g38`), 448 of the CLI's 1,500 daily requests,
10 minutes. With `qa.py --allow-miss`: no-hint 119 of 125 trusted, round 3 316 of 320
(`runs/phase8-qa-round3-agy.json`). Section 8's steps 1 to 4 are done.

Dataset (`--selection data/vision/select/round1+3.json`, the no-hint verdicts first): train 1,739 pages,
4,345 boxes, 24 books (was 1,362 / 3,446 / 16); validation and held-out unchanged. The previous
`dataset.json` is kept as `data/vision/dataset-student7.json`.

`train.py --run student-8 --init student-7 --resolution 864 --epochs 7`: 706 s, 6.45 GB VRAM (9 GB
reserved), RAM 10 of 14 GB, best epoch 5, val mAP50-95 0.882 (student-7: 0.869). `infer.py --all --conf
0.3 --out data/vision/pred-s8-low`: 35 books. Confidence chosen on validation: 0.7 (at 0.6: found 0.857,
12.3 % answering none). **`data/vision/bake-6/`** = student-8 at 0.7, `--icons bind`, admit, 35 books.

| Against the teacher | Found (IoU ≥ 0.5) | IoU ≥ 0.85 | Same content | Regions answering no teacher box | Empty pages with a region |
| --- | ---: | ---: | ---: | ---: | ---: |
| validation, `bake-5` (student-7) | 0.812 | 0.759 | 0.737 | 13 of 121 (10.7 %) | 0 of 28 |
| validation, `bake-6` (student-8) | 0.850 | 0.789 | 0.767 | 11 of 124 (8.9 %) | 0 of 28 |
| held-out, `bake-5` | 0.623 | 0.559 | 0.607 | 93 of 603 (15.4 %) | 7 of 83 |
| held-out, `bake-6` (scored once) | 0.591 | 0.530 | 0.569 | 77 of 559 (13.8 %) | 4 of 83 |

Scorecard, held-out (`runs/phase8-compare-bake5-as-rules-vs-bake6-student8.json`; its "rules" rows are
`bake-5`, and its "?" split is the eight new books, whose "rules" row is the rules bake): `bake-6` 2,742
regions, cuts/region 0.021, violations 0, match 0.455 (`bake-5`: 2,818, 0.023, 0, 0.473).

**Eight more books did not make the student better on unseen books.** On held-out student-8 draws fewer
hotspots: 16 fewer on nothing, 3 fewer on empty pages, and 27 fewer teacher boxes found. It is better on
validation and on the books it trained on. Per book it gained on `51cdbbce` (maths), `cb558332` (physics),
`a7a7886a`; it lost on `7fad03e9` (Fen Lisesi physics), `11941059` and `c9f63718` (ELT).

**State.** `activities/books/` still holds `bake-5` for the 27 books and the rules bake for the eight new
ones; `scan.py`'s `VISION_PRED` still names student-7. Installing `bake-6` is the user's choice:
copy `data/vision/bake-6/activities/books/` over `activities/books/`, set `VISION_PRED` to
`pred-s8-low`, run `python3 -m pytest tests`, restart the reader.

Still open: old step 2 at scale (the limit on unseen books is not yet "more of the same eight"; the ELT and
Fen Lisesi losses say which layouts to add), and a rules re-bake with the corrected ruler if the rules
numbers are wanted again.
