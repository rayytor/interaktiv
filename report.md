# Vision hotspot detector: where it stands and what to solve

> **Update, 2026-10-02 evening. Read `tools/hotspot_extraction/vision/runs/phase7-snap-icons-HANDOFF.md` first.**
> The inaccurate boxes were mostly not the student's and not the icons': the bake was moving the
> student's boxes after the fact. The rules cleanup (`snap_edges`) still ran inside the serializer, and
> `snap.py`'s own edge and block steps traded lines of the activity for a tidy edge. That is fixed and
> tested on 8 books (6 training, 2 validation): regions holding exactly what the teacher's box holds went
> from 0.750 to 0.837 on the training books (the student's raw boxes: 0.849) and from 0.654 to 0.677 on
> the validation books (raw: 0.692). `vision/accuracy.py` measures it.
> What is below still stands for *match*: §3's diagnosis is right, §4 is still the user's to answer, and
> the match gate cannot pass without growing a region from every icon. `scan.py --icons bind|blocks|grow`
> now implements the three options of §4. The user answered §4 the same evening: **pin only** (`bind`), and
> an activity taller than 70 % of the sheet gets no hotspot. All 27 books are re-baked into
> `data/vision/bake-2/` and scored (held-out once); `data/vision/bake/` still holds the old bake. On held-out
> the fix moves little (same content 0.616 before and after; the student's raw boxes 0.632): there the
> limit is the student, not the bake.
>
> **Later the same night, Phase 8 (`runs/phase8-HANDOFF.md`).** Scored against the teacher, the shipped rules
> bake finds 38 % of the activities on held-out books and 68 % of its hotspots sit on none; the vision bake
> finds 62–63 % with 15–16 % on none. With the user's yes the vision bake (`data/vision/bake-4/`: student-7 at
> 864 px and confidence 0.7, plus doubtful boxes a publisher icon heads) was copied into `activities/books/`
> for the 27 books so it can be seen in the reader; `git checkout activities/books` undoes it. Higher
> resolution did not help on unseen books. Next: the user's verdict in the reader, then more books.

> **2026-10-03 (`runs/phase8-HANDOFF.md` §6 to §8).** The user's verdict: the vision bake stays, and
> `scan.py` bakes with it by default. Two fixes (stray glyphs as labels; grids across the gutter) are in
> `bake-5`, which the reader shows. The no-hint re-ask (54 of 123 pages) and the labelling of eight new
> books (0 of 320) stopped at the free tier's daily quota; the retrain waits for them.

> **2026-10-03, Phase 9 (`runs/phase9-HANDOFF.md`).** An unboxed label between two boxed ones now gets a
> region (`snap.between_siblings`), eight more books were labelled (five English, three science) and
> student-9 trained on 32 books. `bake-8` (student-9 at 0.7) is installed for 43 books: on held-out it
> finds 64.0 % of the teacher's boxes with 12.3 % of regions on none (`bake-7`: 59.2 %, 13.8 %). Fen Lisesi
> Fizik did not move (29 %).

Written 2026-10-02. Branch `vision-student`, nothing committed since `225a0ba`.
Point a fresh agent at this file. It is self-contained; the full history is in
`tools/hotspot_extraction/vision/PLAN.md` (read §0) and
`tools/hotspot_extraction/vision/runs/phase3-5-HANDOFF.md`.

## 1. The situation in five sentences

Interaktiv's reader shows a tappable hotspot over each student activity on a textbook page.
Today those hotspots come from a hand-written rules engine (`activities/books/<book>/regions.json`).
A vision engine was built to replace it: a teacher model (Gemini) labels pages, a small detector
(RF-DETR Medium, "the student") learns from them, and its boxes are snapped to the PDF's geometry.
The vision engine draws cleaner regions (fewer cuts per region, more answer-space coverage) but joins far
fewer of the publisher's interactive entries, so by the plan's own gate it **does not ship**.
Today's finding is that most of that gap is not a detection failure: the publisher hangs its icons beside
things that are not exercises, and the student was told not to box those.

## 2. The numbers

Student-6 is the best checkpoint (`data/vision/checkpoints/student-6/checkpoint_best_total.pth`,
SHA-256 `b0696468d532a923…`), baked at confidence 0.6 into `data/vision/bake/`.

All 27 books, both engines (`runs/phase6-compare-all-student-6.json`):

| Split | Engine | Regions | Cuts | Cuts/region | Overlaps | Slivers | Tall | Match | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| held-out (9) | rules | 4275 | 320 | 0.075 | 0 | 0 | 0 | 0.950 | 0.419 |
| held-out (9) | vision | 3060 | 142 | 0.046 | 0 | 0 | 0 | 0.345 | 0.520 |
| train (18) | rules | 12685 | 782 | 0.062 | 0 | 0 | 0 | 0.926 | 0.506 |
| train (18) | vision | 7627 | 263 | 0.035 | 0 | 0 | 0 | 0.557 | 0.576 |

"Match" is the share of the publisher's interactive entries that the scorecard's join links to a region.
The shipping gate (PLAN.md Phase 5) is: overlaps, slivers and tall are 0; cuts/region lower than rules on
held-out; match not more than 2 points lower on any book. The first two hold. The third fails badly.

Student against the teacher's boxes:

| Set | Confidence | mAP50 | Precision | Recall | Boxes on empty pages |
| --- | ---: | ---: | ---: | ---: | ---: |
| validation, 79 pages | 0.6 | 0.921 | 0.908 | 0.773 | 0 / 28 |
| held-out, 384 pages (scored once) | 0.6 | 0.786 | 0.838 | 0.647 | 8 / 83 |

Held-out per book ranges from 0.617 to 0.945 mAP50; ELT books are high, subject books low.
Six students so far; adding labels raised validation (0.853 → 0.921) but not held-out (0.776 → 0.786).

## 3. Why publisher entries go unmatched

`tools/hotspot_extraction/vision/unmatched.py` files every publisher entry for both bakes and looks at the
page for each one the vision bake misses (`runs/phase6-unmatched-student-6.json`, one row per entry).

| Split | Entries | Rules ok | Vision ok | No box on page | Box elsewhere on page | Icon beside a box | Icon in a box |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| held-out | 220 | 209 | 76 | 69 | 40 | 30 | 5 |
| train | 732 | 678 | 408 | 128 | 103 | 89 | 4 |

- **The student is not drawing these regions at all.** Where the rules bake matched an entry and vision
  did not, a vision region covers half or more of the rules region for only 11 of 111 entries on held-out
  and 28 of 156 on training books. The join is not the main fault.
- **The losses sit in a few subject books.** Chemistry `6e4f65dc` (held-out) matches 4 of 78, 58 of them on
  pages with no box. Mathematics `753fcdb0` 6 of 96, `51cdbbce` (held-out) 10 of 38, `a0e5ed1a` 5 of 47,
  biology `09f62a7e` 2 of 21. ELT books match: `0e966773` 151 of 165, `c9f63718` 24 of 27.
- **What those icons mark.** The unmatched entries are titled "örnek" (worked example), "bilgi kutusu"
  (information box), "özellikler", "Fotokromik Camlar" (an opening photograph), "Kosinüs Teoremi İspatı"
  (a proof), "Kavram Haritası", simulations. Checked on two pages by drawing icon, rules regions and vision
  regions (`6e4f65dc` PDF page 16, `753fcdb0` PDF page 14): the icons stand beside a photograph, a worked
  example, a "Hatırlatma" box and a property list; the student drew nothing, as the teacher prompt
  instructs ("body text that is not an exercise gets no box").
- **The rules engine matches by construction.** It grows a region from every icon, whatever the icon stands
  beside. On the mathematics page that produced about a dozen fragmentary regions over one worked example.
- **A smaller real problem remains.** 30 held-out and 89 training entries have the icon level with a vision
  box that the join still refuses, mostly as `drift` (the box top is further from the icon than
  `UNLABELLED_DRIFT` 5 % / `LABELLED_DRIFT` 10 % in `interaktiv_core/linking.py`), some as
  `label-mismatch`. Not yet investigated.
- **Duplicate icons.** 25 pairs of entries sit within 3 % of each other (two entries for one spot, different
  content ids). The join is one region to one entry, so vision can match at most one of each pair.

Where the icons come from: `activities_meta/<book-id>.json`, the publisher's manifest downloaded from the
OGM Materyal API by `tools/content_extraction/extract_activities.py`. Each `kitapogeList` entry has `baslik`,
`sayfano` (printed page), `posx`/`posy` (percent of the sheet, from the left and from the top) and no size.
`scanner/score.py:interactive_oges` filters them; the bake's `folio.byPage` maps printed page to PDF page.

## 4. The open decision (ask the user before building)

When the publisher puts an icon beside something that is not an exercise, what should the reader show?
The user was asked on 2026-10-02 and **has not answered**. Do not pick for them.

1. **A hotspot on that block.** Give the vision engine the icon positions: for an entry no vision box
   claims, take the block the icon stands beside (panel, figure, example box) from the PDF geometry, whole.
   The only option that can approach the rules engine's match. Changes what a hotspot means.
2. **No hotspot, only the icon.** Keep the definition; score match only against entries that stand beside
   an exercise, so the vision engine is judged on the smaller gap.
3. **Hybrid.** Vision boxes for exercises, the rules engine's icon-grown region elsewhere. Quickest, but
   keeps the rules engine's fragmentary regions on exactly those pages.

The previous agent leaned to option 1.

## 5. What to solve, in order

1. Get the user's answer to §4.
2. Read the 30 + 89 "icon beside a box" entries on sheets (rows with `"where": "icon beside a box"` in
   `runs/phase6-unmatched-student-6.json`) and decide whether the join or the box is wrong. Any fix is
   universal, never per page or per book.
3. Implement the chosen option, re-bake the 27 books into `data/vision/bake/`, run `compare.py` and
   `unmatched.py`, and report the gate table per split and per book.
4. Only if the gate passes: make vision the default engine, bake into `activities/books/`, run the
   scorecard, restart the reader (rule 6 below). Ask first; it overwrites the shipped bake.
5. Not yet done and cheap: re-ask the 73 round-2 `re-ask` pages. Held-out generalisation on subject books
   (recall 0.45–0.62) is the other weak point; the training set still mixes prompt v1 labels (a shared
   block inside a box) with v2 labels (inside none).

## 6. Rules that are not negotiable

These are the user's standing decisions. PLAN.md §0.2 and §0.3 have the full text.

- **Ask first** before anything that could cost money, takes more than about 15 minutes, uses a large share
  of a daily quota, the GPU, RAM or disk, or deletes or overwrites labels, checkpoints or bakes. Say what
  runs, how long, what it uses, what it produces; wait for a yes. A yes covers one run.
- **No paid API keys, ever.** Teachers are the Antigravity CLI (subscription) and the free-tier Gemini
  keys in the gitignored `api.md`.
- **Memory.** No large `--max-old-space-size`, no whole-catalogue sweep in one process. One short-lived
  child per book. An unasked batch job once crashed this machine (14 GB RAM).
- **Correctness beats coverage.** No hotspot is better than a wrong one. Judge by cuts, overlaps, slivers,
  tall first; match and coverage second.
- **Universal fixes only.** No per-page or per-book overrides.
- **Region semantics.** A region takes a referenced picture, table, passage or dialogue whole or not at
  all; includes the answer space; two activities sharing a label stay two. A block that several questions
  share belongs to **none** of their hotspots (decided 2026-10-01; prompt v2 says so).
- **No hand labels.** A person may look at labelled pages; nobody draws or corrects a box.
- **Held-out stays unread.** The 9 held-out books in `train/splits.json` are never trained on and never
  used to pick a checkpoint. Student-6's held-out score has been taken; do not re-score to tune.
- **A detector change is not done** until the books are re-baked, the scorecard is run and the reader is
  restarted: `pkill -TERM -f "python3 -m interaktiv_gtk"; (python3 -m interaktiv_gtk >/dev/null 2>&1 &)`
- **Textbook cache** goes to the private `rayytor/interaktiv-cache` repo, never the public one.
- Commit only when the user asks.

## 7. Map of the code and data

All under `tools/hotspot_extraction/vision/` unless a path says otherwise. Python is `.venv-vision/bin/python`.

| File | Role |
| --- | --- |
| `render.py` | PDF pages → `data/vision/pages/<book>/<page>.jpg` + `index.jsonl` (1024 px wide). Done once. |
| `select.py` | Round 1: stratified sample. `--round 2`: pages the student is least sure of, training books only. |
| `teacher.py`, `teacher_prompt.md` (v2) | Gemini API teacher with the free keys; resumable, one JSON per page. |
| `qa.py` | Label gate: `trusted` / `re-ask` / `rejected` per page; writes `verdicts-*.jsonl`. |
| `dataset.py` | Trusted pages → `data/vision/dataset/{train,valid,heldout}` (COCO), split by book; `--check`. |
| `train.py` | RF-DETR Medium; `--smoke` before every full run; one line per run in `runs/train-runs.jsonl`. |
| `evaluate.py` | mAP, precision, recall per book against the teacher's boxes. |
| `infer.py` | Student boxes per book → `data/vision/pred/<book>/boxes.json`; `--out` for a second folder. |
| `self_label.py` | Pages where every student box scores ≥ 0.9 become labels (training books only). |
| `snap.py` | Boxes → PDF points → settled on seams → labels and headlines. |
| `../scan.py --engine vision` | Bakes `regions.json` from `boxes.json`; never loads a model. |
| `compare.py` | Both bakes through the scorecard; `--sheets N` draws pages side by side. |
| `unmatched.py` | Per-entry reasons for unmatched publisher entries (new today). |
| `icons.py` | What the publisher's icons do once the boxes are settled: admit, bind, blocks, grow (new, evening). |
| `accuracy.py` | Baked regions against the teacher's boxes: IoU and same-content share (new, evening). |
| `../scanner/score.py` | The scorecard: violations, the join's buckets, `interactive_oges`. |
| `interaktiv_core/linking.py` | `link_oges`: the icon-to-region join and its drift and reach limits. |

Data (all gitignored under `data/vision/`): `labels/agy/` is a symlink to
`~/Projects/interaktiv-local-teacher/data/labels/agy`; `labels/api/`; `labels/self/`; `select/round{1,2}.json`;
`pred/` (student-6 at 0.6, what the bake reads); `pred-doubt/` (student-4 at 0.2, used for round-2 selection);
`bake/` (vision bake); `sheets/engines-all-student-6/` (side-by-side pages).

Labels so far: 1,215 round-1 pages (1,137 trusted after the v2 re-ask), 540 round-2 pages (467 trusted),
221 self-labelled. Dataset: 1,362 train / 79 valid / 384 held-out pages. Validation books are pinned:
`550e601a` (ELT workbook) and `7763e45b` (geography).

Things that will trip you up:

- The Antigravity CLI teacher script is **not in the repo** (user's decision, 2026-09-30). It is
  `~/Projects/interaktiv-local-teacher/code/teacher_all_routes.py`; it resolves the project root from its own
  path, so copy it into `tools/hotspot_extraction/vision/` under a temporary name, run it with `--route agy`,
  and delete the copy. Its output is silent until pages finish (about 20 s each, 8 workers).
- Quotas measured today: CLI 576 requests for 480 pages in 25 minutes (1,500 a day); API keys 159 requests
  for 60 pages in 6 minutes, ten keys, none exhausted.
- Bake student-6 at confidence **0.6**, not the 0.5 default: at 0.5 it puts a box on an empty validation page.
- Timings: infer all 27 books 164 s; bake with `--workers 6` 133 s at 0.9 GB; compare 3 s; a 20-epoch
  training run on 1,362 pages 17 minutes at 4.3 GB VRAM.
- `activities/books/` is the shipped rules bake and has not been touched. Keep it that way until the gate passes.

## 8. Reproduce today's results

```bash
PY=.venv-vision/bin/python; V=tools/hotspot_extraction/vision; L=data/vision/labels
$PY -m pytest $V -q                                   # 53 tests pass
$PY $V/dataset.py --verdicts $L/verdicts-cli-g38.jsonl $L/verdicts-api-v1.jsonl \
    $L/verdicts-round2-agy.jsonl $L/verdicts-round2-api.jsonl $L/verdicts-self-student-3.jsonl
$PY $V/dataset.py --check
$PY $V/train.py --smoke && $PY $V/train.py --run student-6 --epochs 20      # ask first: 17 min of GPU
$PY $V/evaluate.py --run student-6 --split valid --conf 0.6
$PY $V/infer.py --run student-6 --all --conf 0.6
$PY tools/hotspot_extraction/scan.py --engine vision --all --force --workers 6 --quiet \
    --out data/vision/bake/activities/books                                 # ask first: replaces the vision bake
$PY $V/compare.py --workers 6 --json $V/runs/phase6-compare-all-student-6.json
$PY $V/unmatched.py --json $V/runs/phase6-unmatched-student-6.json
```

## 9. Uncommitted work

39 changed or new paths on `vision-student`: the Phase 3–6 tooling listed in §7, the run summaries in
`runs/`, edits to `scan.py`, `PLAN.md`, `teacher_prompt.md` (v2) and `requirements.lock`, this report, and an
unrelated untracked `image.svg` at the repo root. The user has not asked for a commit.
