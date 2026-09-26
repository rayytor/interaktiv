# Hotspot detector accuracy programme — briefs for AI agents

One phase per fresh session. An agent starting a phase has no memory of any
other phase, so every brief below is self-contained: read **§0** and **your
phase only**, plus the handoff file the previous phase left in
`tools/hotspot_extraction/train/runs/`. Do not start a later phase in the same
session; write your handoff and stop.

Written 2026-09-26 from measurements taken that day on the 27 books whose PDFs
are on this machine. Line numbers cited are from that date and may drift;
search by function name.

---

## §0 Read this first, every phase

### 0.1 What is being trained

The activity-hotspot detector is ~5,600 lines of rules in
`tools/hotspot_extraction/scanner/`. It reads exact glyph and vector geometry
out of PDF textbooks with PyMuPDF, grows one hotspot rectangle per activity,
and bakes them to `activities/books/<book-id>/regions.json`, which the GTK
reader loads. There is no hand-labelled ground truth and there never will be.
Two things supervise it for free:

- the **publisher manifest** in `activities_meta/<book-id>.json`: which
  activities are interactive, on which printed page, and where their icon
  hangs (`posx`/`posy`, % of the sheet). It validates presence and top edge,
  never extent.
- the **violation rules** in `scanner/score.py`: a hotspot may not cut a
  drawn block (panel, answer space, figure), may not overlap another, may not
  be a sliver, may not be most of the sheet. They validate extent and need no
  labels, so they run over the whole catalogue.

`tools/hotspot_extraction/train/` holds the fitting machinery:

| File | Role |
| --- | --- |
| `train/cache.py` | Parses each PDF once into `.cache/primitives/<id>.pkl` (27 shards, 113 MB). Replaying a shard runs the detector without the PDF. |
| `train/splits.py`, `train/splits.json` | 18 training books, 9 held-out. **Held-out books are never scored during fitting**; `assert_trainable` raises. |
| `train/objective.py` | The loss: per-book rates, `cut·100 + overlap·200 + tall·40 + sliver·40 + miss·20 − coverage·1`, averaged over books. `--verify` proves replay == bake; `--baseline` scores a profile on both splits. |
| `train/fit.py` | Screens the ~66 knobs in `scanner/profile.py`, then coordinate descent, then separable CMA-ES. `--final` is the only place held-out is scored. |
| `train/learn.py`, `train/features.py` | A 28-feature logistic ranker over regions, labelled by the manifest join. Not wired into the detector. |
| `train/triage.py` | Reads `activities/books/*/trace.json.gz` and ranks the rules that emptied sheets. |
| `scanner/profile.py` | Every tunable constant as a `Knob` with a range; `apply_profile` writes a profile into the detector modules; `RULER` pins the three thresholds the violation rules use. `scanner/profile.json` is the shipped profile (absent = source defaults). |
| `scanner/pipeline.py` | `detect_page`: the one place the stage order is written. |
| `scanner/regions.py` | `grow_activity_regions`, `snap_edges`, `clean_page_activities`, `detect_panels`, `detect_solution_spaces`, `legal_planes`. |
| `scanner/anchors.py` | Binds publisher icons to regions and synthesises regions for icons nothing found (`reconcile_anchors`, `_band_owner`). |
| `interaktiv_core/linking.py` | `link_oges`: the one implementation of region-to-manifest matching; used by the reader, the baker and the scorer. |
| `tools/hotspot_extraction/scan.py` | Bakes books. `--all --force --workers 2 --trace` rebakes the 27 local books in ~95 s at ~350 MB peak. |
| `tools/hotspot_extraction/compare_scorecard.py` | `--skip-scan` writes `BENCHMARK_REPORT.md` from the bakes with acceptance gates. |

### 0.2 Standing rules (the user's, not negotiable)

1. **Memory.** Never pass a large heap cap to Node or Python, never sweep the
   catalogue in one process. Every batch step is one short-lived child per
   book or per page chunk at the default heap (`ProcessPoolExecutor` with
   `max_tasks_per_child`). The machine has 20 cores and 15 GB RAM of which
   ~4 GB was free during measurement; start pools at 2–6 workers and watch
   RSS. One badly sized job has crashed this machine before.
2. **Correctness beats coverage.** A page with no hotspot is better than a
   page with a wrong one. Judge every change by cut, overlap, tall and sliver
   counts first; match rate and coverage second. Say plainly when a
   correctness fix lowers coverage; that is the chosen trade.
3. **Universal fixes only.** No per-page overrides, no constant tuned so one
   page comes out right. Quantify a defect across the corpus before fixing
   it, and show the fix is general.
4. **Region semantics.** A hotspot covers the whole picture, diagram or panel
   the question refers to, never half; it includes the space the student
   writes the answer in; it takes a referenced block whole or not at all;
   two activities that share a label stay two activities.
5. **No hand labels.** Any learned component must derive its labels from the
   manifest, the rules, or cross-book consistency. A design that needs a
   person to draw boxes is out of scope.
6. **A detector change is not done** until the local books are re-baked, the
   scorecard is run, and the running reader is restarted so the user sees
   it. The reader keeps a book's bake in memory for the session:
   `pkill -TERM -f "python3 -m interaktiv_gtk"` then relaunch
   `python3 -m interaktiv_gtk &` from the repo root returns to the same page.
7. **Held-out stays unread** during any fitting or model training. Score it
   once, at the end, with `fit.py --final` or `learn.py`.

### 0.3 Commands every phase uses

Run from `/home/rayyan/Projects/interaktiv`.

```bash
# tests that guard the training loop and the scorer (~5 s)
python3 -m pytest tools/hotspot_extraction/train tools/hotspot_extraction/scanner -q -p no:cacheprovider

# is the cache current, does replay equal the bake on every book (~2 min)
python3 tools/hotspot_extraction/train/cache.py --all --workers 2
python3 tools/hotspot_extraction/train/objective.py --verify --workers 2

# score the shipped profile on both splits (~3 min, writes JSON)
python3 tools/hotspot_extraction/train/objective.py --baseline --workers 2 --json tools/hotspot_extraction/train/runs/<date>-baseline.json

# rebake, scorecard, reader restart (rule 6)
python3 tools/hotspot_extraction/scan.py --all --force --trace --workers 2
python3 tools/hotspot_extraction/compare_scorecard.py --skip-scan
pkill -TERM -f "python3 -m interaktiv_gtk"; (python3 -m interaktiv_gtk >/dev/null 2>&1 &)
```

### 0.4 Reference measurements (2026-09-26, source defaults, profile hash `27b80aacc68e`)

Kept in `train/runs/2026-09-26-baseline-defaults.json` (per book) and
`train/runs/2026-09-26-learn-verdict-leaky.json`.

| Split | Books | Loss | Cuts | Cuts/region | Tall | Overlaps | Slivers | Match | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 18 | 6.926 | 888 | 0.070 | 40 | 0 | 0 | 91.9 % | 77.9 % |
| held-out | 9 | 8.356 | 386 | 0.090 | 10 | 0 | 0 | 95.0 % | 62.8 % |

Cut census over the 27 bakes (script in Appendix A): 1,274 cuts; 1,009 on
answer-space blocks, 265 on panels. By where the region's edge lies relative
to the block: 506 "straddle" (region entirely inside the block's vertical
span), 309 side-only, 249 bottom edge inside the block, 210 top edge inside.

Empty sheets: 1,666 of 6,185 (26.9 %). Attributed by `triage.py`:
`prompt.not-a-question` 1,130, `growth.no-marker` 466 (three ELT books
`a7a7886a`, `ad3f3275`, `6e4f65dc` are 85–97 % empty), `marker.no-hanging-indent`
67. 83 sheets yielded only icon-synthesised regions (a miss with a witness):
`anchor.unbound` 45, `prompt.not-a-question` 35.

Learned ranker, as it stands: held-out AUC 0.861, top-1 0.608 vs 0.529 for
"topmost region". **Not to be trusted** — see Phase 0 task 4.

### 0.5 Handoff protocol

Every phase ends by writing `tools/hotspot_extraction/train/runs/<phase>-HANDOFF.md`
containing: what changed (files, functions), the before/after table in the
format of §0.4, test results, anything left undone and why, and the exact
commit hash. The next phase's first action is to read it. Commit at the end
of each phase with a message naming the phase.

---

## Phase 0 — make the training loop trustworthy

**Goal.** Nothing fitted on the loop as it stands should be shipped: the
objective can be gamed two ways and the learned scorer has a label leak.
Close all three, prove replay equals bake, commit.

**Read first.** §0; `train/objective.py` (all); `scanner/profile.py`
(`SPEC`, `RULER`, `apply_profile`); `scanner/pipeline.py`; `train/learn.py`
(`examples_for_book`, `_matched_region_ids`, `baseline_top1`);
`scanner/score.py` (`tally_page`, `takeable`).

**Preconditions.** `git status` shows `tools/hotspot_extraction/train/` and
seven `scanner/*.py` files untracked. Tests in §0.3 pass (98 did on
2026-09-26). `.cache/primitives` holds 27 shards.

### Tasks

1. **Commit the package as-is** before touching anything, so the fitted
   results can be tied to the code that produced them:
   `git add tools/hotspot_extraction/train tools/hotspot_extraction/scanner`
   and commit as "chore: commit detector profile and training package".

2. **Freeze the blocks the cuts are measured against.** `pipeline.detect_page`
   builds `panels`/`solutions` (and `figures` inside growth) with
   `detect_panels`, `detect_solution_spaces`, `detect_figures`, which read
   module globals that `apply_profile` overwrites: `RULE_MIN`, `RULE_ROW`,
   `RULE_CLUSTER`, `RULE_THICK`, `MIN_CELL`, `PANEL_MIN_W`, `PANEL_MIN_H`,
   `PANEL_LINES`, `FIGURE_*`. The scorer's diagnostic blocks come from the
   same call, so a candidate profile that detects fewer answer spaces erases
   answer-space cuts without moving a region. The fit will find this.
   Implement: in `objective.tally_chunk` and `learn.examples_for_book`,
   produce the diagnostic blocks under `DEFAULTS` for those knobs (apply the
   candidate, run growth, then temporarily re-apply the block knobs from
   `DEFAULTS` and recompute `panels`/`solutions` for `diagnostics`; or add a
   `block_profile` argument to `detect_page`). Keep the bake path unchanged.
   Add `train/test_fit.py::test_block_knobs_cannot_move_the_ruler`: score one
   cached book under `DEFAULTS.replace(RULE_MIN=72.0, PANEL_MIN_H=100.0)` and
   assert `solutions_total` and the panel count in the tally equal the
   defaults' counts.

3. **Close the coverage exit.** Two training books have no manifest
   (`4442fde4`, `3a4479c7`), so their loss is `100·cut_rate − coverage` and
   baking nothing is optimal for them. Add to `objective.Evaluation` a
   `gates(baseline: Evaluation)` method returning per-book failures when a
   book loses more than 5 pp of coverage or more than 20 % of its regions
   against the baseline. Call it in `fit.py --final` and print the failures
   beside the comparison; extend `_compare` to list coverage per book. Add a
   test with two synthetic `BookScore`s. Do not change the `cover` weight.

4. **Remove the label leak from `learn.py`.** `_matched_region_ids` marks
   every region with an `ogeId` or a `p<n>-oge-` id as positive: those were
   built from the manifest entry, so the label is a tautology, and the
   `is_anchored` feature carries weight +2.20 in the recorded verdict.
   Implement: exclude anchored regions from the dataset; delete
   `is_anchored` from `FEATURE_NAMES` (bump `MODEL_VERSION`); replace
   `baseline_top1` (topmost region) with the detector's real choice, the
   `link_oges` drift+reach cost against the entry's icon position (needs the
   entry's `posx`/`posy` carried through the dataset). Re-run
   `learn.py --workers 2 --json train/runs/phase0-learn-verdict.json` and
   record the honest verdict in the handoff. Whatever it says is fine; the
   point is that Phase 4 decides on a true number.

5. **Verify.** Run the §0.3 tests, then `objective.py --verify --workers 2`
   (must be 27/27 equal), then `objective.py --baseline --workers 2 --json
   train/runs/phase0-baseline.json`. Loss values will differ from §0.4 only
   if task 2 changed which blocks are counted; explain any difference in the
   handoff.

**Acceptance.** All tests pass including the two new ones; verify is 27/27;
the honest `learn.py` verdict is recorded; two commits exist (as-is, then the
fixes).

**Do not** touch `scanner/regions.py`, `anchors.py` or `serializer.py` in
this phase; that is Phase 1.

---

## Phase 1 — expose the detector to the fitter

**Goal.** The constants nearest the cut count are hard-coded and two cleanup
passes run blind. Fix both so the fit in Phase 2 can reach them, and commit
the cut-census script so every later phase measures the same way.

**Read first.** §0; `runs/phase0-HANDOFF.md`; `scanner/regions.py`
(`snap_edges`, `clean_page_activities`, `grow_activity_regions` tail);
`scanner/anchors.py` (`reconcile_anchors` end, line ~583);
`scanner/serializer.py` (the `clean_page_activities` call, line ~329);
`scanner/profile.py` (`SPEC`) and `scanner/test_profile.py` (how defaults are
checked against source literals).

### Tasks

1. **Commit the census as a tool.** Create `train/census.py` from Appendix A,
   with `--json` output and a per-book table. Run it on the current bakes and
   record the numbers (should reproduce §0.4: 1,274 / 506 / 309 / 249 / 210).

2. **Put the snap thresholds in the profile.** In `snap_edges`, the literals
   `share >= 0.35` (twice), `rect_area(b) <= 2.5 * rect_area(r)` (twice) and
   `passes: int = 3` become module globals `SNAP_EXPAND_SHARE = 0.35`,
   `SNAP_PANEL_RATIO = 2.5`, `SNAP_PASSES = 3`, added to `SPEC` in
   `profile.py` under the `regions` module with ranges `0.1–0.9`, `1.0–8.0`,
   `1–6`. `test_profile.py` will fail until defaults and literals agree.

3. **Thread geometry into the blind cleanups.** `anchors.py` ends
   `reconcile_anchors` with `clean_page_activities(reconciled, geom=None, …)`,
   and `serializer.py` calls `clean_page_activities(raw_acts)` with no
   geometry; in that mode the de-overlap pass cuts at midpoints, inside lines
   and blocks, undoing what `snap_edges` did. Pass the page geometry
   (`trace.geometry` from growth, or rebuild it with `_page_geometry` in
   anchors) to both. Measure first: bake one high-cut book (`79cbecfa`) before
   and after with `scan.py --only 79cbecfa --force --workers 1`, run the
   census on it, and put both numbers in the handoff. Also pass geometry in
   `objective._replay_pages` and `learn.examples_for_book` so replay still
   equals bake.

4. **Tall regions.** 50 tall violations survive although `snap_edges` clamps
   parts to `TALL_REGION`. Find where they come from (`score_baked` per book
   → `tally_page` → which regions; likely the anchored path or a region
   whose union of parts exceeds the clamp). Fix generally. Target: 0.

5. **Verify and rebake.** §0.3 tests; `objective.py --verify` 27/27;
   rebake all 27 with `--trace`; `compare_scorecard.py --skip-scan`; census;
   `objective.py --baseline` to `runs/phase1-baseline.json`; restart the
   reader.

**Acceptance.** Tests green; verify 27/27; census shows cuts not higher than
Phase 0's on any book and lower in total; tall = 0 on both splits; overlaps
and slivers still 0; no book loses more than 2 pp match or 5 pp coverage.
Handoff includes the before/after census table per book.

---

## Phase 2 — fit the profile

**Goal.** Produce and ship the first fitted `scanner/profile.json`.

**Read first.** §0; `runs/phase1-HANDOFF.md`; `train/fit.py` (module
docstring and `main`); `train/objective.py` (`ChunkPool`, `Weights`).

**Preconditions.** Phase 0 tasks 2–3 and Phase 1 task 2 are in the tree
(check `SNAP_EXPAND_SHARE` is in `SPEC` and `Evaluation.gates` exists).
`objective.py --verify` is 27/27.

### Tasks

1. **Screen.**
   `python3 tools/hotspot_extraction/train/fit.py --screen --minutes 30 --workers 6 --log tools/hotspot_extraction/train/runs/phase2-screen.json`
   Watch peak RSS across children for the first evaluation
   (`psutil`, or `watch -n2 free -m`); if free memory drops under 2 GB,
   restart with `--workers 4`. Read the ranking before fitting:
   - if `RULE_*` / `PANEL_*` / `FIGURE_*` knobs lead **and** the trial totals
     show `solutions_total` or region counts falling, Phase 0 task 2 is not
     effective — stop and write that in the handoff;
   - if `SNAP_*`, `RETREAT_*`, `WHOLE`-adjacent growth knobs lead, proceed.

2. **Fit, two seeds.**
   ```bash
   python3 tools/hotspot_extraction/train/fit.py --fit --minutes 240 --workers 6 --seed 20260922 \
       --out tools/hotspot_extraction/train/runs/phase2-profile-a.json --log tools/hotspot_extraction/train/runs/phase2-fit-a.json
   python3 tools/hotspot_extraction/train/fit.py --fit --minutes 240 --workers 6 --seed 7 \
       --out tools/hotspot_extraction/train/runs/phase2-profile-b.json --log tools/hotspot_extraction/train/runs/phase2-fit-b.json
   ```
   Run them one after the other, not at once. Each run saves its best profile
   after every improvement, so an interrupted run still leaves a result.
   Compare `bestChanged` of the two logs: knobs that moved the same way in
   both are findings; a knob that moved only in one is noise and is reset
   to its default in the shipped profile.

3. **Final scoring, once.**
   `python3 tools/hotspot_extraction/train/fit.py --final --profile <chosen> --log tools/hotspot_extraction/train/runs/phase2-final.json`
   This is the only time the held-out books are scored in this phase.

4. **Ship.** Copy the chosen profile to `tools/hotspot_extraction/scanner/profile.json`,
   rebake all 27 with `--trace`, run the scorecard, census, and restart the
   reader. The bake stamps the profile hash; confirm `regions.json` carries
   it.

**Acceptance (held-out, from `--final`).** Loss below 6.0 (from 8.36);
cuts/region below 0.06 (from 0.090); tall 0, overlaps 0, slivers 0; no gate
failure from `Evaluation.gates`; no held-out book loses more than 2 pp match
rate. If the fit does not reach these, ship it anyway if every component is
no worse and total cuts are lower, and say in the handoff which target was
missed and by how much. Do not re-propose the split, and do not re-run
`--final` on a second candidate after seeing the first's held-out numbers.

---

## Phase 3 — structural fixes the fit cannot make

Four independent briefs, one session each, in this order. Each is a
universal rule change measured on the whole corpus with `train/census.py`
and `triage.py`, followed by rebake, scorecard, reader restart, and — because
a rule change moves the fitted optimum — a re-fit (Phase 2 tasks 2–4, one
seed is enough, 120 minutes).

### 3a — straddle cuts (506)

A region lying entirely inside a block's vertical span means the block spans
several questions: an answer grid welded across question boundaries by the
union-find `near()` merge and `RULE_ROW` in `detect_solution_spaces`, or a
tinted background. Read first: `regions.py` `detect_solution_spaces`,
`spans_other_activity` ("shared furniture belongs to nobody"),
`score.takeable`. Diagnose on the census's per-book table: pick the three
books with most straddles, dump ten (region, block, markers level with the
block) triples, and classify. Fix candidates: split a solution block at rows
level with another marker; or let a region take its own rows of a shared
grid whole. Target: straddle count below 150 with no rise in the other three
census classes.

### 3b — side and bottom cuts (309 + 249)

Side: the column or measure edge slices a block that reaches into the
gutter; `legal_planes_x` exists but `snap_edges` only retreats laterally.
Make it expand laterally when the block is mostly the region's (per-axis
`share`). Bottom: the answer space is not taken whole (rule 4 of §0.2 says it
must be); check `solution_for` reach and `RETREAT_COST` after the Phase 2
fit before changing code, since the fit may already have moved this. Target:
side below 100, bottom below 100.

### 3c — three ELT books that bake almost nothing

`a7a7886a` (3 % of sheets have a region), `ad3f3275` (6 %), `6e4f65dc`
(15 %). Their labels are refused before growth (`growth.no-marker`), so no
knob reaches them. Read first: `markers.py` (`detect_activity_markers`, the
`no-hanging-indent`, `typography`, `smaller-in-font` drops), `trace.py`.
Dump the `marker.*` drops for 20 sheets of `a7a7886a` from its
`trace.json.gz`; the refusing rule names a label typography the detector
does not know (a letter set in a coloured box, flush with no indent, or in
a display face). Fix in `markers.py` generally, e.g. accept a label when a
run of `a, b, c…` in the same style confirms it even without the indent, and
prove it on all three books with no regression on the other 24 (census and
scorecard per book). Target: coverage above 40 % on each of the three, match
rate unchanged, cuts/region on them below the corpus mean.

### 3d — the misses with a witness

83 sheets yield only icon-synthesised regions: `anchor.unbound` 45,
`prompt.not-a-question` 35. Read first: `anchors.py` `_band_owner`,
`find_unplaced_anchors`; `prompts.py` the `not-a-question` gate;
`triage.py` output for those sheets. For `anchor.unbound`, check the
`BAND_REACH_*` values the Phase 2 fit chose and whether the icon lies in a
band that exists but was not offered. For `not-a-question`, relax the gate
only when the band already contains a publisher icon (the manifest is
evidence a question is there). Target: anchor-only sheets below 30;
catalogue `anchor-nogrow` bucket below 100.

---

## Phase 4 — learned components with free labels (gated)

Only start after Phase 3a–3b. The test for each is "where does the label come
from, and could the bake compute it itself". A label the bake can compute at
bake time is a lookahead rule, not a model — implement it as a rule.

### 4a — the candidate ranker

Read `runs/phase0-HANDOFF.md` for the honest verdict. If held-out top-1 beats
the `link_oges` cost by more than 5 pp, ship the weights as
`scanner/ranker.json` beside the profile and use them in exactly one
decision: which region an unlabelled manifest entry binds to when drift
alone cannot separate candidates (`contested` bucket, and the
synthesise-versus-bind choice in `reconcile_anchors`). Never use it to drop a
region: the negatives mean "not marked interactive", not "not an activity".
If the margin is under 5 pp, delete `learn.py`'s claim from the docs and
record that the ranker is not worth carrying.

### 4b — a "is this paragraph a question" classifier for the prompt gate

Labels: positives are paragraphs a lettered marker accepted, plus paragraphs
the gate refused on the anchor-only sheets that lie within the icon's band;
negatives are paragraphs on sheets with no marker, no icon and no manifest
entry (front matter, prose). Features from text and typography already in
the primitives, no pixels: length, terminal punctuation, an imperative
Turkish or English verb at the start, size ratio to body, indent, leading,
position in band. Train a logistic model with the same zero-dependency loop
as `learn.py`, evaluate on held-out by bucket counts and empty-sheet share
with violations as a hard no-regression gate, and ship as JSON weights used
only on sheets where the rule refused everything.

### 4c — book-level self-calibration for marker typography

For books like the three in 3c, the manifest titles ("42/a") say which
letters exist on which printed page, so the spans matching those letters on
that sheet are that book's marker style. Fit the `markers.py` knobs per book
from that evidence, store them in the bake's existing `calibration` block,
fall back to the global profile when a book has no manifest. This is
calibration from the book's own evidence, not a per-page override.

### 4d — trees, only if 4a or 4b shows a linear margin

numpy and scikit-learn are not installed and the shipped detector keeps two
dependencies. Train a small tree ensemble in a throwaway venv, export as
nested JSON, evaluate with a pure-Python walker, keep it only if it beats
the linear model on held-out by more than seed-to-seed noise.

---

## Phase 5 — make it repeatable

Create `tools/hotspot_extraction/train/run_all.sh`: cache check → verify →
screen → fit → final → bake → scorecard → census → acceptance table, every
step one short-lived child per book or chunk, workers bounded by free RAM.
Move the acceptance gates (zero overlaps, slivers, tall; no per-book
violation regression; no per-book match loss over 2 pp; no per-book coverage
loss over 5 pp) into `compare_scorecard.py` so they fail the run.
`splits.py --check` runs first and refuses to fit if a book was added or
removed since `splits.json` was written. Re-record the scorecard baseline with
the Python scorer so future deltas are algorithm-only.

---

## Appendix A — cut census (worked on 2026-09-26)

Reads bakes and diagnostics only; no detector run, one book at a time.

```python
import sys, json, gzip, os
sys.path.insert(0, '.')
from tools.hotspot_extraction.scanner import score as S
from tools.hotspot_extraction.scanner.regions import cuts
from tools.hotspot_extraction.train import cache as C

where = {"straddle": 0, "side-only": 0, "bottom-in-block": 0, "top-in-block": 0}
by_kind = {"solutions": 0, "panels": 0}
for bid in C.cached_books():
    d = os.path.join("activities/books", bid)
    reg = json.load(open(os.path.join(d, "regions.json")))
    diag = json.load(gzip.open(os.path.join(d, "diagnostics.json.gz"), "rt"))
    dpages = diag.get("pages", diag)
    for pno, page in reg["pages"].items():
        dp = dpages.get(pno)
        if not dp:
            continue
        W, H = page["pageWidth"], page["pageHeight"]
        for act in page["activities"]:
            for r in act.get("parts") or [act["rect"]]:
                r = tuple(r)
                for kind in ("solutions", "panels"):
                    for b in S.takeable([tuple(x) for x in dp.get(kind, [])], r, W, H, kind == "panels"):
                        if not cuts(r, b, whole_tol=S.WHOLE_TOL):
                            continue
                        by_kind[kind] += 1
                        bot_in = b[1] < r[1] < b[3]     # y-up: r[1] is the region's bottom
                        top_in = b[1] < r[3] < b[3]
                        key = ("straddle" if bot_in and top_in else
                               "bottom-in-block" if bot_in else
                               "top-in-block" if top_in else "side-only")
                        where[key] += 1
print(by_kind, where)
```

Note: the census applies the scorer's `takeable` filter but not its
"panel encloses more than one marker" filter, so panel counts can differ
slightly from `score_baked`'s; use `score_baked` for the official count and
the census for the breakdown.
