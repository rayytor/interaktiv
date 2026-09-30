# Vision hotspot detector — teacher/student plan

Written 2026-09-28. This replaces the knob-fitting programme in
`tools/hotspot_extraction/train/PLAN.md` (its Phases 3–5 are **paused**, not
deleted). One phase per fresh agent session, in Claude Code or in the Antigravity IDE
installed on this machine; paste §0 plus the phase as the agent's task. Every
phase is self-contained:
read §0, then your phase, then the handoff the previous phase left in
`tools/hotspot_extraction/vision/runs/`.

The whole programme is designed so that **no single step takes longer than
about an hour, every step can be stopped and resumed, and a crash costs
minutes, not a day.**

---

## §0 Read this first, every phase

### 0.1 Why we are starting over (plain words)

The old "training" was not a neural network. It was a search that nudged 66
numeric constants inside a 5,600-line rule detector and re-ran the whole
detector over 18 books after every nudge. Each re-run took 20–47 s, a 4-hour
budget managed ~200 nudges, two runs disagreed, and the shipped result changed
**one** constant. It could not fix the real errors either: boxes that slice
through a row of text, miss the answer blanks, or cut an example sentence in
half. That is a "what belongs together" problem, which rules written by hand
have not solved after 5,600 lines.

The new approach:

1. **Teacher.** A large vision model (Gemini, through the user's existing
   Google AI Pro / Antigravity subscription, **no API key and no extra
   payment**) looks at a rendered page and returns the activity boxes as
   data. Nobody draws a box by hand. Because the subscription is metered in
   requests per day, the plan labels a **small, well-chosen subset** of
   pages, not all 6,767, and lets the student fill in the rest.
2. **Student.** A small object-detection network trains on those boxes on the
   local RTX 5060 Ti (16 GB). Training one run takes well under an hour.
3. **Snap.** The student's boxes are approximate (a few pixels). The PDF's
   exact text and line geometry, which the old detector already extracts,
   snaps every edge to a legal seam. The old detector's *growth* logic is
   retired; its *geometry* and *scoring* logic is kept.

Why this split: pixels are the right input for deciding **what belongs to an
activity** (a picture, its caption, the answer lines, a speech bubble). The
PDF's vector geometry is the right input for deciding **exactly where the
edge goes**. Each does what it is good at.

### 0.2 Decisions the user made (2026-09-28), not open for re-discussion

| Decision | Chosen |
| --- | --- |
| Where the model runs | Only on this PC, at bake time. Smartboards read `regions.json`; they never run a model. So PyTorch and the GPU may be used freely. |
| Teacher | Google models through the user's Antigravity / Google AI Pro subscription only. **The user will not pay for an API key**, so no design may depend on billed API access. See §0.4 for the zero-cost routes and their daily quotas. *Superseded 2026-09-30: free-tier Gemini API keys only, see the note in §0.4.* |
| Model output | **Box only.** One rectangle per activity. Label letters (`a`, `b`, `3`) still come from the existing text rules in `scanner/markers.py`; missing labels stay `null`, which `regions.json` already allows. |
| Old rule detector | **Model leads, rules snap edges.** `snap_edges`, `legal_planes`, `detect_panels`, `detect_solution_spaces`, `detect_figures`, `clean_page_activities`, `score.py`, `census.py` and `interaktiv_core/linking.py` are kept. `grow_activity_regions`, `train/fit.py`, `train/objective.py` and the profile knobs are retired from the bake path. |

### 0.3 Standing rules (carried over; the user's, not negotiable)

1. **Memory.** Never a large heap cap, never a whole-catalogue sweep in one
   process. Rendering and PDF parsing run one short-lived child per book
   (`ProcessPoolExecutor(max_tasks_per_child=1)`), 2–4 workers, watch RSS.
   The machine has 20 cores, 14 GB RAM, of which ~9 GB is free at rest.
   GPU work is different: one process, one model, one book at a time.
2. **Correctness beats coverage.** A page with no box is better than a page
   with a wrong one. Judge every change by the violation counts (cut,
   overlap, tall, sliver) first; match rate and coverage second.
3. **Universal fixes only.** No per-page or per-book hacks. Fix the prompt,
   the data or the model, and show the fix on the whole corpus.
4. **Region semantics.** A box covers the whole picture, diagram or panel the
   question refers to, never half; it includes the space the student writes
   the answer in; it takes a referenced block whole or not at all; two
   activities that share a label stay two activities.
5. **No hand labels.** A person may *look at* labelled pages to judge whether
   the teacher is trustworthy. A person never *draws or corrects* a box.
6. **A detector change is not done** until the 27 local books are re-baked,
   the scorecard is run, and the reader is restarted:
   `pkill -TERM -f "python3 -m interaktiv_gtk"; (python3 -m interaktiv_gtk >/dev/null 2>&1 &)`
7. **Held-out stays unread.** `train/splits.json` names 9 held-out books.
   They are never trained on and never used to pick a checkpoint. They are
   scored once per phase, at the end.
8. **Ask before anything significant.** Before an agent does anything that
   could cost the user money, or that will use a lot of the machine or a
   lot of time, it **stops and asks the user clearly: "Should I proceed?"**
   and waits for a yes. That includes, without exception: anything that
   could create a bill (an API key, enabling billing, a paid model, a paid
   service, credits); any run expected to take more than about 15 minutes
   (a full render, a full teacher round, a full training run, a rebake of
   all books); anything that will spend a large share of a daily quota;
   any install larger than a few hundred MB; and deleting or overwriting
   data that took time to produce (labels, checkpoints, bakes). The ask
   names what will run, roughly how long it will take, what it will use
   (GPU, RAM, disk, quota, money) and what it will produce. A "yes" covers
   that one run only. Small, fast, reversible steps (tests, a smoke run,
   rendering a few pages, writing code) need no ask.
9. **Every long step is resumable and idempotent.** Output goes to one file
   per page or per book; a re-run skips what exists; a crash is restarted
   with the same command. No step may need more than ~1 hour of wall clock
   before it has saved something reusable.

### 0.4 Zero cost: how the teacher runs on the existing subscription

> **2026-09-30, the user's decision: the teacher in this repository is the Gemini API with the
> user's free-tier keys only** (`teacher.py`, keys in the gitignored `api.md`, no billing). The
> Antigravity CLI / IDE routes and the local Ollama (Qwen3-VL) teacher were moved out of the repo
> to `~/Projects/interaktiv-local-teacher/`, with their code, labels and results.
>
> The routes below are the original 2026-09-28 reasoning, kept for the record.

Checked 2026-09-28 against Google's own docs. The user pays for Google AI
Pro (which includes Antigravity) and will not pay for an API key. Three
routes cost nothing extra; the plan uses them in this order.

**Route A (primary): a scriptable CLI logged in with the Google account.**
Gemini CLI (`npm i -g @google/gemini-cli`, Node 22 is already installed) and
its successor Antigravity CLI (`agy`) both run headless:
`gemini -p "..." --output-format json` or `agy -p "..." --output-format json`.
They log in with the Google account, not an API key, and Gemini CLI's
documented quota on Google AI Pro is **1,500 requests per day** (1,000 on a
free account). Files, including images, are attached with `@path`. One
request labels one page, so **about 1,400 pages a day** after retries. This
is a normal script that a Python loop can call with `subprocess`; it is
resumable and unattended. Phase 0 verifies in ten minutes that the CLI
really sees the image and returns boxes; if it does not, use route B.

**Route B (fallback): the Antigravity IDE agent, in batches.** The agent is
multimodal, can open image files and run terminal commands, and its quota
refreshes every five hours on Pro. Give it one task per batch of ~30 pages:
"for each JPEG in this folder, look at it and write `<page>.json` in the
format of `teacher_prompt.md`; skip pages whose JSON exists". Chain batches
with Antigravity's Scheduled Tasks. Slower and needs more babysitting than
route A, but it is the same model looking at the same pixels. Never ask the
agent to label more than one batch in one task; long tasks drift.

**Route C (optional): a free-tier Gemini API key.** Google issues these with
no card on file, at a lower daily cap than route A and with the free-tier
data terms. The user has said no to paid keys and has not asked for this;
mention it only if routes A and B both fail.

**Why the subset design matters.** Labelling all 6,767 pages would take five
quota-days and is unnecessary. The teacher labels ~1,200 pages first, the
student learns, and then the teacher is asked only about the pages the
student is unsure of (Phase 6). Total teacher requests over the whole
programme: about 2,500, or **two to three quota-days, $0**.

### 0.5 Glossary (for the user; agents may skip)

- **Bounding box / box**: a rectangle given as four numbers. Gemini returns
  `[ymin, xmin, ymax, xmax]` scaled to 0–1000 of the image. `regions.json`
  stores `[x0, y0, x1, y1]` in PDF points with **y going up**.
- **Teacher / student**: the expensive model that produces labels, and the
  small model that learns from them.
- **Epoch**: one full pass of training over every image.
- **Checkpoint**: the model's weights saved to disk mid-training so a run can
  resume from there.
- **IoU** (intersection over union): how well two boxes overlap, 0 to 1.
  0.5 means "roughly the same thing", 0.9 means "nearly identical".
- **mAP50**: the standard detection score; the fraction of boxes the model
  got right at IoU ≥ 0.5, averaged over confidence thresholds. Above 0.85 is
  good for large rectangular regions like these.
- **Held-out**: books the model never saw. The only honest score.

### 0.6 Files this programme creates

```
tools/hotspot_extraction/vision/
  PLAN.md            this file
  render.py          Phase 1: PDF pages -> JPEG + index
  teacher.py         Phase 2: Gemini labelling, resumable
  teacher_prompt.md  Phase 2: the prompt, versioned
  qa.py              Phase 2: label quality gates
  dataset.py         Phase 3: builds train/val/held-out sets
  contact_sheet.py   Phase 3: pages with boxes drawn, for looking at
  train.py           Phase 4: trains the student, resumable
  evaluate.py        Phase 4: mAP + violation gates on held-out
  infer.py           Phase 5: student boxes for one book
  snap.py            Phase 5: boxes -> PDF points -> snapped regions
  run_all.sh         Phase 6: the whole pipeline, every step skippable
  runs/              handoffs, metrics, logs (small files only)
data/vision/         (gitignored) rendered pages, labels, checkpoints
.venv-vision/        (gitignored) the training environment
```

The GTK reader and `interaktiv_core/` never import anything from `vision/`
or from PyTorch.

### 0.7 Handoff protocol

Every phase ends by writing `vision/runs/<phase>-HANDOFF.md`: what was
built (files, functions), the numbers in the format of the phase's "done
when" table, exact commands that reproduce them, what is left undone and
why, and the commit hash. Commit at the end of each phase with a message
naming the phase.

---

## Phase 0 — environment and a ten-minute proof

**Goal.** Prove on this machine that (a) PyTorch sees the GPU, (b) Gemini
returns boxes for one page, (c) those boxes can be drawn on the page and
they look like activities. Nothing else. Budget: one evening, under $0.10.

**Why a separate Python.** The app runs on the system Python 3.14. CUDA
wheels for 3.14 have been unreliable; `uv` already has CPython 3.12 installed
at `~/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu`. Training gets
its own venv so nothing about the app changes.

### Tasks

1. Create the environment and pin it:
   ```bash
   uv venv --python 3.12 .venv-vision
   uv pip install --python .venv-vision/bin/python \
       torch torchvision --index-url https://download.pytorch.org/whl/cu128
   uv pip install --python .venv-vision/bin/python \
       pymupdf pillow google-genai psutil pytest
   uv pip freeze --python .venv-vision/bin/python > tools/hotspot_extraction/vision/requirements.lock
   ```
   Add `.venv-vision/` and `data/vision/` to `.gitignore`.
2. Smoke test, and record the output in the handoff:
   ```bash
   .venv-vision/bin/python -c "import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
   ```
   Must print `True` and `NVIDIA GeForce RTX 5060 Ti`. If it prints `False`,
   stop and write the handoff; do not try three other wheel indexes.
3. Install the CLI and log in with the Google account (the user does the
   browser login step; no key is created):
   ```bash
   npm i -g @google/gemini-cli && gemini --version
   ```
   If `agy` (Antigravity CLI) is available for Linux at
   https://antigravity.google/docs/getting-started?tab=cli, install it too
   and prefer it; the commands are the same shape.
4. Render page 31 of book `0a3fbb41` at 1024 px wide (`pymupdf`, JPEG
   quality 90) and ask the CLI headlessly, attaching the image with `@`,
   with the first draft of the prompt (Phase 2 owns the final one):
   ```bash
   gemini -p "@vision/runs/phase0-page31.jpg $(cat tools/hotspot_extraction/vision/teacher_prompt.md)" --output-format json
   ```
   Confirm from the reply that the model actually saw the picture (it names
   things on the page) and returned parseable JSON. If the CLI cannot attach
   images, record that and do the same single page through the Antigravity
   IDE agent (route B) instead. The prompt:

   > This is a page from a school textbook. Find every student activity:
   > an exercise, question or task a student is meant to answer. Return one
   > box per activity. A box must include the activity's instruction text,
   > everything the instruction refers to (pictures, diagrams, tables,
   > dialogues) and the space where the student writes the answer (blank
   > lines, boxes, grids). Two activities never share a box. Body text that
   > is not an exercise gets no box. Return JSON: a list of objects with
   > `box_2d` as `[ymin, xmin, ymax, xmax]` normalised to 0–1000 and `label`
   > as the printed label of the activity (like "a", "b", "3") or null.

5. Draw the returned boxes on the page and save the image to
   `vision/runs/phase0-page31.png`. Compare with the old bake's boxes for
   the same page (they are in `activities/books/0a3fbb41-…/regions.json`).
   The user looks at both. That is the proof.

**Done when.** The handoff contains the torch line, the raw JSON Gemini
returned, the drawn image, the token counts the API reported for that one
call (so Phase 2 can do the cost arithmetic with real numbers), and the
commit hash.

**Do not** in this phase: render more than a handful of pages, train
anything, touch `scan.py`.

---

## Phase 1 — render the corpus once

**Goal.** Every page of the 29 local PDFs as one JPEG on disk, with an index
that maps image pixels back to PDF points. Rendered once, never again.
Budget: about one hour of machine time, no cost.

### Tasks

1. Write `vision/render.py`:
   - Input: `books/*.pdf` (or `--only <id>`). Output:
     `data/vision/pages/<book-id>/<page:04d>.jpg` at a **fixed** width of
     1024 px (height follows the page aspect), JPEG quality 90.
   - `data/vision/pages/<book-id>/index.jsonl`, one line per page:
     `{"page": 31, "width_pt": 552.756, "height_pt": 779.528, "width_px": 1024, "height_px": 1444}`.
   - One child process per book, `max_tasks_per_child=1`, `--workers 2`
     default. Skip a page whose JPEG exists (`--force` to redo). Log peak
     RSS per book the way `scan.py` does.
   - A book's folder also gets `fingerprint.txt` from
     `serializer.compute_fingerprint`, so a re-exported PDF re-renders.
2. Run it over all 29 books. Expect ~6,800 files, roughly 1.5–2.5 GB.
3. Write `vision/test_render.py`: rendering one page twice gives byte-equal
   files; the index line's pixel size matches the JPEG; the point-to-pixel
   scale equals `width_px / width_pt` on both axes within 0.1 %.

**Why 1024 px.** It is the size the student trains at, it is 4 Gemini tiles
(so predictable cost), and a page at that size is 0.3 MB. Higher resolution
buys nothing: the exact edges come from the PDF geometry in Phase 5, not
from pixels.

**Done when.** Every page of every book has a JPEG and an index line; the
test passes; the handoff records file count, total size, peak RSS and time.

---

## Phase 2 — the teacher labels a chosen subset

> **2026-09-30, the user's decision: the teacher in this repository is the Gemini API with the
> user's free-tier keys only** (`teacher.py`, keys in the gitignored `api.md`, no billing). The
> Antigravity CLI / IDE routes and the local Ollama (Qwen3-VL) teacher were moved out of the repo
> to `~/Projects/interaktiv-local-teacher/`, with their code, labels and results.
> `teacher.py` has no `--route` any more; the steps below that name the CLI or `--route ide` describe
> the original design.

**Goal.** One JSON label file per page for a **stratified subset of ~1,200
pages**, produced through the subscription at no cost, then an automatic
quality gate that marks each page **trusted / re-ask / rejected**. Budget:
one to two quota-days of unattended running, $0.

### Which pages (`vision/select.py`)

Chosen by a fixed seed so the set is reproducible:

- About 45 pages per book across all 29 books (~1,200 total).
- Within a book: 70 % pages that carry a publisher manifest entry or that
  the old rules bake found a region on (activity-bearing pages); 15 % pages
  where the manifest says there is an activity but the rules bake found
  nothing (the hard cases, e.g. the three ELT books that bake almost
  nothing); 15 % pages with neither (front matter, prose, so the student
  learns "nothing here").
- Held-out books get the same treatment; their labels are used only for
  scoring, never for training.
- Writes `data/vision/select/round1.json` listing the pages and why each
  was picked.

### Tasks

1. **Prompt** in `vision/teacher_prompt.md`, versioned (`v1`, `v2`, …; the
   version is written into every label file). Start from Phase 0's draft
   and add the four region rules from §0.3 in plain words. Ask for
   `label` even though the student is box-only: it is free and it is what
   the quality gate joins against the publisher manifest. Ask for the
   JSON only, no prose, so the CLI's `response` field parses directly.
2. **`vision/teacher.py`**, resumable, route A:
   - For each selected page: build the prompt, call the CLI with
     `subprocess` (`gemini -p "@<jpeg> <prompt>" --output-format json`,
     timeout 120 s), parse `response`, write
     `data/vision/labels/<route>/<prompt-version>/<book-id>/<page:04d>.json`
     holding the raw reply, the boxes converted to PDF points (y-up, the
     `regions.json` convention), the model name the CLI reports, and a
     timestamp. Skip pages whose file exists.
   - On a quota error (the CLI says the daily limit is reached) **stop
     cleanly** and print how many pages remain; the same command continues
     tomorrow. On any other error retry twice, then write a `.failed`
     marker and move on. Never crash the loop for one page.
   - `--route ide` writes the batch folders and a `TASK.md` per batch for
     the Antigravity IDE agent (route B) instead of calling anything; a
     separate `--collect` merges what the agent wrote into the same layout.
   - Prints requests used today against the 1,500 quota.
3. **Run round 1** over the selection. Expect one quota-day.
4. **Agreement check.** Re-ask a random 10 % of round 1 (seeded) with the
   same prompt and compute per-page box agreement (greedy IoU matching at
   0.7). Record the mean in the handoff. Under 0.85 the prompt is
   ambiguous: fix the prompt (universal), bump the version, re-run the
   10 % before spending quota on the full selection again.
5. **`vision/qa.py`**, the label quality gate. Per page, using only the
   existing machinery:
   - Convert boxes to points, build `PageGeometry` from the cached
     primitives (`train/cache.py` shards, or parse the page), and run
     `score.tally_page`-style checks: **cut** (edge inside a drawn block),
     **overlap**, **sliver**, **tall**. A teacher box that cuts a block is
     not wrong by itself (the teacher cannot see vectors), so cuts are
     recorded and passed to Phase 5's snapper; overlaps and slivers are
     defects.
   - **Manifest join**: every publisher icon (`posx`/`posy` in
     `activities_meta/<id>.json`) on the page should fall inside or within
     a small band of some box. An icon with no box = a **miss**. A page
     whose miss count is > 0 goes to **re-ask**.
   - **Label sanity**: labels on a page should be a plausible sequence
     (`a, b, c` or `1, 2, 3`); a duplicate label with non-touching boxes is
     allowed (two columns) but flagged.
   - Verdict per page: `trusted` (no overlaps, no slivers, no misses),
     `re-ask` (misses or overlaps), `rejected` (still failing after
     re-ask). Write `data/vision/labels/verdicts.jsonl` and a per-book
     summary table.
6. **Re-ask** the `re-ask` pages once, same prompt plus one universal hint
   line ("The publisher lists N interactive activities on this page; make
   sure each has a box"), asking the CLI for the Pro model with
   `--model` if the CLI offers it. Re-run `qa.py`. Pages still failing are
   `rejected` and **excluded from training** (rule 2: better no label than
   a wrong one).
7. **Contact sheets for the user.** `vision/contact_sheet.py` draws 48
   random `trusted` pages with their boxes into a few PNGs in
   `vision/runs/phase2-sheets/`. The user looks (rule 5) and says whether
   the teacher is trustworthy. If not, it is a prompt problem: fix, bump the
   version, re-run.

**Done when.** The handoff shows: pages selected and labelled, requests
used per day, mean agreement, verdict counts (trusted / re-ask / rejected)
per book, and the prompt version that produced the trusted set. At least
85 % of selected pages with a manifest should be `trusted`; if fewer, say
why.

**Where the Antigravity IDE agent fits.** Under route A it writes and runs
these scripts. Under route B it *is* the teacher, one batch folder per
task. Either way it never labels more than a batch in one task, and every
label lands in a file so nothing is lost when a session ends.

## Phase 3 — build the dataset

**Goal.** Turn trusted labels into a training set the student can read,
split by **book** so the held-out score is honest, and prove the set is
clean before any GPU time is spent. Budget: one hour.

### Tasks

1. **`vision/dataset.py`** writes `data/vision/dataset/{train,val,heldout}/`
   with, per page, the JPEG (symlink) and a label file in COCO JSON form
   (one `annotations.json` per split; class id 0 = `activity`, boxes in
   pixels as `[x, y, w, h]`). Only `trusted` pages. Pages with zero
   activities are **kept** (the model must learn to say "nothing here").
2. **Split by book**, reusing `train/splits.json`: its 9 held-out books
   become `heldout`; of the 18 training books, 2 (one ELT, one subject,
   chosen by seed) become `val`; the remaining 16 are `train`. Never split
   by page: pages of one book look alike, and the score would lie.
3. **Fail-fast validation** in `dataset.py --check`, run automatically
   before training: every image file exists and opens; every box has
   `w > 8 px` and `h > 8 px` and lies inside the image; no image appears in
   two splits; class ids are all 0; the split sizes are printed. Any failure
   exits non-zero with the offending page named.
4. Record in the handoff: images and boxes per split, boxes per page
   (mean, max), and the fraction of empty pages.

**Done when.** `--check` passes; the split table is in the handoff;
`contact_sheet.py` on 24 `train` pages looks right to the user.

---

## Phase 4 — train the student

**Goal.** A detector that, on held-out books, finds the teacher's boxes with
mAP50 ≥ 0.85, trained in under an hour per run, with a smoke test that
catches every crash-class problem in three minutes. Budget: one afternoon.

### The model

**Primary: torchvision Faster R-CNN, ResNet-50 FPN v2**, COCO-pretrained,
fine-tuned with 2 classes (background, activity). Reasons: it ships inside
`torchvision` (BSD licence, no extra dependency), the fine-tuning recipe is
the official torchvision tutorial and about 150 lines, it handles large
rectangular regions well, and it is boring, which is the point.

**Alternative, if the user accepts its licence:** Ultralytics YOLO (`s`
size). Easier still (`model.train(data=..., epochs=60, imgsz=1024)` with
built-in resume and plots), but AGPL-3.0. Because the model only runs at
bake time on the user's PC and the reader never imports it, the practical
risk is low, but the tool code importing it would sit in the public repo.
The user decides; the plan assumes torchvision unless the handoff says
otherwise.

### Tasks

1. **`vision/train.py`**, with these non-negotiable properties:
   - **Fixed seed** (`--seed 0`), `torch.backends.cudnn.deterministic=True`.
   - **Checkpoint every epoch** to `data/vision/checkpoints/<run>/epoch_NN.pt`
     plus `last.pt` and `best.pt` (best = highest **val** mAP50; held-out is
     never consulted).
   - **`--resume`** continues from `last.pt` with optimiser state; the same
     command line restarts an interrupted run.
   - **`--smoke`**: 64 train images, 16 val, 1 epoch, batch 2, then a full
     evaluate on the 16. Must finish in under 3 minutes. **Run the smoke
     test before every full run.** Anything that would crash a full run
     (a bad label, an OOM at this batch size, a broken import, an NaN loss)
     crashes here instead.
   - **Batch-size probe** (`--probe`): tries batch 2, 4, 8 at 1024 px for
     ten steps each and reports peak VRAM; pick the largest under 12 GB so
     the desktop keeps its share. Expect 4–8 on 16 GB.
   - Dataloader `num_workers=4`, `pin_memory=True`; images are read from
     the JPEGs, never all held in RAM.
   - Mixed precision (`torch.autocast`) on; gradient clipping at 10.
   - Logs one JSON line per epoch (`loss`, `val_mAP50`, `val_mAP50-95`,
     seconds, peak VRAM) to `vision/runs/<run>.jsonl`. Prints an ETA.
   - Augmentation: horizontal flip **off** (text pages), small scale
     jitter (0.9–1.1), slight brightness/contrast. Nothing else.
   - Schedule: SGD momentum 0.9, lr 0.01 with warm-up, cosine to 0 over
     **40 epochs**, weight decay 1e-4. On ~4,500 train images at 1024 px,
     one epoch on this GPU is roughly 60–90 s, so a run is ~45–60 min.
2. **`vision/evaluate.py`**: given a checkpoint and a split, reports
   mAP50, mAP50-95, precision and recall at confidence 0.5, per book. Also
   counts, per page, boxes predicted vs teacher boxes, so a systematic
   under- or over-detection shows up as a number.
3. Run: `--probe`, then `--smoke`, then the full run. Then
   `evaluate.py --split val` on `best.pt`. Only when val is satisfactory,
   `evaluate.py --split heldout` **once**.
4. If held-out mAP50 is under 0.85, the first suspects in order: label
   quality on the worst book (look at its contact sheet), too few empty
   pages, batch size too small. Fix one thing, re-run smoke, re-run full.
   Never change three things at once; the run is an hour, use it.

**Done when.** The handoff has the per-split table (mAP50, mAP50-95,
precision, recall), per-book held-out numbers, the run's wall clock, peak
VRAM, the seed, and the checkpoint's SHA-256. Held-out mAP50 ≥ 0.85, or an
explanation and the number.

### Why this will not take 8 hours or crash overnight

| Old programme | This programme |
| --- | --- |
| One evaluation = re-run the detector on 18 books, 20–47 s, CPU | One epoch = ~70 s on the GPU, ~40 epochs |
| No intermediate result until the run ends | A usable checkpoint every epoch |
| A crash at hour 3 loses hour 3 | A crash resumes from the last epoch |
| Results differ between seeds and paths | Fixed seed, same data, same result |
| Errors surface hours in | Smoke test surfaces them in 3 minutes |

---

## Phase 5 — snap the boxes and bake regions

**Goal.** The student's boxes become `regions.json` through the kept slice of
the old detector, and the bake passes the same scorecard gates as before.
Budget: one day.

### Tasks

1. **`vision/infer.py`**: loads `best.pt` once, walks one book's JPEGs,
   writes `data/vision/pred/<book-id>/<page>.json` (boxes in pixels with
   confidence). Keeps boxes with confidence ≥ 0.5; applies non-maximum
   suppression at IoU 0.5 so two overlapping predictions become one.
   One book at a time; VRAM ~2 GB.
2. **`vision/snap.py`**, the one new piece of geometry code:
   - Convert pixel boxes to PDF points, y-up, using the index from Phase 1.
   - Parse the page's primitives (`primitives.extract_page_primitives`) and
     build `PageGeometry` exactly as `anchors._page_geometry` does, with
     `detect_panels`, `detect_solution_spaces`, `detect_figures`.
   - Wrap each box in an `ActivityRegion` (`rect`, `parts=[rect]`,
     `label=None`) and run the existing `snap_edges` and
     `clean_page_activities` with geometry. This is what turns "a few
     pixels off" into "on the seam between two lines".
   - Vertical edges that still rest inside a text line move to the nearest
     `legal_planes` seam.
   - **Labels**: run `markers.detect_activity_markers` on the page; a
     marker whose glyph lies inside a box's top-left quarter gives that box
     its label; otherwise `null`. `build_headline` gives the headline text
     the reader shows.
   - Publisher ids: `interaktiv_core.linking.link_oges`, unchanged.
   - Output the same `regions.json` (`BAKE_VERSION` 2) and
     `diagnostics.json.gz` the serializer writes today, with a new
     `"engine": "vision", "checkpoint": "<sha256>"` stamp where the profile
     hash used to go.
3. **`scan.py --engine vision`**: the existing CLI gains an engine switch.
   `--engine rules` keeps the old path so the two can be compared on the
   same day. The vision engine renders (or reuses Phase 1's JPEG), infers
   and snaps; one book per invocation of the GPU worker, PDF parsing in a
   child per book as before.
4. **Bake all 27 books, both engines**, then
   `compare_scorecard.py --skip-scan` and `train/census.py` on each. The
   table that matters, per split and per book:

   | Engine | Cuts | Cuts/region | Overlaps | Slivers | Tall | Match | Coverage |
   | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

   Rule 2 decides: the vision engine ships if overlaps, slivers and tall are
   0 and cuts/region is lower than the rules engine on held-out, with match
   rate not more than 2 pp lower on any book. Coverage is reported, not
   gated.
5. Restart the reader (rule 6). The user opens the page from Phase 0 and a
   few others.

**Done when.** The comparison table is in the handoff for train and held-out
splits; `scan.py --engine vision` is the default only if it won; the reader
shows the new boxes.

---

## Phase 6 — close the loop and make it boring

**Goal.** One command reproduces everything, and the student improves from
its own disagreements without new hand work. Budget: one afternoon, then
repeat as wanted.

### Tasks

1. **Active learning, the second teacher round.** Run `infer.py` over
   every page the teacher has not labelled (~5,500 pages, about ten minutes
   on the GPU). `select.py --round 2` picks ~600 pages: lowest-confidence
   predictions, pages where the number of predicted boxes disagrees with the
   manifest's count, and pages whose snapped boxes still violate a rule.
   Those go to the teacher (Phase 2's `teacher.py`, one more quota-day),
   through `qa.py`, into the dataset; re-run smoke, train, evaluate, bake.
   One cycle is a quota-day of waiting plus two hours of machine time, $0.
   A third round is worth it only if held-out mAP50 moved by more than 0.02
   in the second.
   **Self-training** after that: student predictions with confidence
   ≥ 0.9 that pass `qa.py` on unlabelled pages may be added as labels for
   one more run, kept only if held-out improves.
2. **`vision/run_all.sh`**: `render → teacher (skips done) → qa → dataset
   --check → train --smoke → train → evaluate val → bake --engine vision →
   scorecard → census`. Every step skips when its outputs exist; `--from
   <step>` restarts anywhere. Exit non-zero on any gate failure, printing
   which.
3. **New books.** A book added to `books/` later needs: render, infer, snap,
   bake. No teacher call and no training. Document the four commands in
   `README.md` under a "Baking a new book" heading. Retrain only when a
   new *kind* of layout appears (a new publisher template), in which case
   that book's pages go through Phases 2–4 once.
4. **Retire.** Mark `train/fit.py`, `train/objective.py`, `train/learn.py`,
   `train/features.py` and the profile knobs as retired in
   `train/PLAN.md` (keep the files one more release for comparison, then
   delete). Keep `train/cache.py`, `census.py`, `splits.py`, `triage.py`.

**Done when.** `run_all.sh` completes from scratch on this machine in under
three hours of wall clock excluding the teacher's API time, and one
mining cycle has been run with its before/after table in the handoff.

---

## Time and money, all phases

| Phase | Machine time | Person time | Quota / cost |
| --- | ---: | ---: | ---: |
| 0 environment + proof | 30 min | 1 evening | a few requests, $0 |
| 1 render | ~1 h | 10 min | 0 |
| 2 teacher labels (~1,200 pages) + QA | 1–2 days unattended | 1 h looking at sheets | ~1,500 requests, $0 |
| 3 dataset | 15 min | 15 min | 0 |
| 4 train | ~1 h per run | 1 afternoon | 0 |
| 5 snap + bake + compare | ~1 h | 1 day | 0 |
| 6 active-learning round + run_all | 1 quota-day + 2 h | 1 afternoon | ~700 requests, $0 |

## Assumptions the plan makes (say so in a handoff if one breaks)

- Python 3.12 via `uv` for the training venv; the app stays on 3.14.
- torchvision Faster R-CNN is the student unless the user opts into
  Ultralytics YOLO knowing its AGPL licence.
- One class, `activity`. Sub-questions and activity types are out of scope.
- The 9 held-out books in `train/splits.json` stay held out.
- No API key is ever created or paid for. The teacher runs through the
  subscription (route A CLI, else route B IDE agent). If both turn out to
  be unable to see images, stop and report; do not fall back to billing.
- The Gemini CLI quota (1,500 requests/day on Google AI Pro) is the
  bottleneck; the plan is sized so no round needs more than one quota-day.

## Sources checked on 2026-09-28

- Antigravity plans and quotas: https://antigravity.google/docs/plans/
- Google AI plans vs API billing: https://ai.google.dev/gemini-api/docs/google-ai-plans
- Gemini CLI quota by plan (1,000 free / 1,500 Pro / 2,000 Ultra requests per day, Google login, no key): https://geminicli.com/docs/resources/quota-and-pricing/
- Gemini CLI headless mode (`-p`, `--output-format json`): https://geminicli.com/docs/cli/headless/
- Antigravity CLI headless mode (`agy -p`, `--output-format json`, `--model`): https://antigravity.google/docs/cli/headless/
- Gemini bounding-box output format and image token cost: https://ai.google.dev/gemini-api/docs/image-understanding
- Gemini rate limits (live numbers are shown only inside AI Studio): https://ai.google.dev/gemini-api/docs/rate-limits
- PyTorch Blackwell (RTX 50) support since 2.7 with cu128 wheels: https://pytorch.org/blog/pytorch-2-7/
