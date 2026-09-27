# Phase 0 handoff: make the training loop trustworthy

Done 2026-09-27. All five tasks are complete and every acceptance item is met.

## Commits

- **As-is package (task 1):** `be2e480` ("feat: stage-2 hotspot scanner pipeline,
  Python scorecard, and detector training harness"). By the time this phase
  started, the train package and the scanner modules had already been committed
  unmodified there, and `git status` was clean. The brief's precondition
  ("untracked") was stale. No separate "chore: commit detector profile and
  training package" commit was needed, because that commit already existed under
  another name.
- **Fixes:** the commit that contains this file, `feat(train): phase 0 …`.
  `git log -1 --format=%H -- tools/hotspot_extraction/train/runs/phase0-HANDOFF.md`
  prints its hash. A file cannot contain the hash of its own commit.

## What changed

### Task 2: the ruler no longer moves with the candidate

- `scanner/pipeline.py`: `detect_page` now returns `PageResult.blocks` (a new
  `Blocks`: content box, markers, panels, solutions). It builds that with
  `ruler_blocks`, which runs `detect_blocks` **under `DEFAULTS`**. When the
  active profile is the defaults, it reuses the layout and markers that
  detection already produced. Otherwise it re-derives layout, markers, panels
  and solutions inside `profile_applied(DEFAULTS)`. `PageResult.panels` and
  `.solutions` are now properties over `blocks`, and `diagnostics()` writes
  `blocks`.
- `scanner/profile.py`: new `profile_applied(p)` context manager, the only
  place a profile is restored.
- `scanner/score.py`: `Tally.panels_total` (merged like the other counters).
  Row `stats` now carries `panels` and `solutions`, the size of the ruler.

**Deviation from the brief, and why.** The brief suggested freezing only the
block knobs (`RULE_*`, `PANEL_*`, `MIN_CELL`) and only inside
`objective.tally_chunk` and `learn.examples_for_book`. I made two changes to
that:

1. **Layout and markers are frozen too.** `detect_panels` takes the markers as
   input, and body spans are clipped to the layout's content box. The
   scorer's panel filter ("encloses more than one marker") also reads the
   markers. If the ruler froze the block knobs but ran on the candidate's
   markers, a profile could still move the panel count through marker or
   layout knobs.
2. **The freeze is in `detect_page`, so the bake uses it as well.** If only the
   replay froze the ruler, then once Phase 2 ships a `profile.json`, the bake's
   diagnostics sidecar would be measured under the fitted profile and the
   replay's under the defaults. Verify would break, and `score_baked` (the
   scorecard) would be gameable again. The regions path is unchanged.
   Diagnostics are read only by the scorer (the reader never opens them), and
   under the default profile they are byte-for-byte what they were. The
   rebake below confirms this.

`figures` are not in the diagnostics. They shape growth only, so they are not
part of the ruler.

**Evidence.** On `09f62a7e` sheets 1–60, under
`DEFAULTS.replace(RULE_MIN=72, PANEL_MIN_H=100)`:

| | answer spaces | panels |
| --- | ---: | ---: |
| defaults | 170 | 87 |
| bent profile, old code (ruler measured under the candidate) | 58 | 47 |
| bent profile, new code | 170 | 87 |

Under the old code, that profile would have erased two-thirds of the answer
spaces its cuts are counted against.

### Task 3: the coverage exit has a gate

- `train/objective.py`: `Evaluation.gates(baseline)` returns per-book
  failures: `coverage` (lost more than `COVERAGE_GATE` = 5 pp), `regions` (lost
  more than `REGIONS_GATE` = 20 %), or `not-scored`. The `cover` weight is
  unchanged.
- `train/fit.py`: `--final` runs the gates for both splits against the
  defaults. It prints them next to the comparison and records them in the
  run log (`Run.gates`). `_compare` takes optional per-book lists and prints
  coverage and regions per book.
- Correction to the brief: `3a4479c7` is a **held-out** book, not a training
  one (`splits.json`). The training book with no manifest is `4442fde4`. The
  gates cover both splits, so this changes nothing.

### Task 4: the label leak is closed

- `train/learn.py`:
  - The dataset is built from `replay_page(sp, reconcile=False)`, which is
    the detector with no publisher anchors. No region grown from or bound to
    an entry exists there. As a guard, any anchored region that still gets in
    (`anchored`, `ogeId`, or a `p<n>-oge-` id) is dropped and counted as
    `anchoredDropped`. That count is 0 on both splits. I chose this over
    replaying with anchors and then filtering, because
    (a) `reconcile_anchors` also *binds* genuinely detected regions
    (`ogeId`), and dropping those would discard real detections, and
    (b) the pre-reconcile regions are exactly the candidates a ranker would
    choose among in Phase 4a.
  - Labels come from the reader's join: `link_oges`, then
    `apply_anchored_ids`.
  - `Dataset.cost` holds each candidate's `pair_cost` to the cheapest icon on
    the sheet. This carries the entry's `posx`/`posy` into the dataset in the
    form the baseline needs.
  - `baseline_top1` is now the cheapest `pair_cost`, not the topmost region.
    `top1_accuracy` and `baseline_top1` can also be restricted to *contested*
    sheets, where the two cheapest candidates are within `CONTESTED` = 2.5 %
    (half of `UNLABELLED_DRIFT`).
  - `MODEL_VERSION` = 2.
- `train/features.py`: `is_anchored` is removed from `FEATURE_NAMES`, leaving
  27 features.
- `interaktiv_core/linking.py`: the cost arithmetic is extracted into
  `icon_reach` and `pair_cost`, and `link_oges` now calls them. Behaviour is
  unchanged: `tests/test_linking.py` and all 238 repo tests pass, and the
  rebake is identical.

**Why the baseline has no label gate.** The labels are `link_oges` output.
A baseline that applied `link_oges`'s full rule (label gate included) would
be the labeller itself and would score 100 % by definition. Geometry alone is
what the detector has for an entry with no label, and it is the decision
Phase 4a would give a ranker.

## Honest learner verdict (`runs/phase0-learn-verdict.json`)

| Split | Examples | Marked | AUC | Model top-1 | Drift top-1 | Sheets | Contested: model / drift / sheets |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| train | 1,523 | 486 | 0.743 | 0.449 | 0.991 | 107 | 0.125 / 0.938 / 16 |
| held-out | 317 | 76 | 0.765 | 0.480 | 0.960 | 25 | 0.000 / 1.000 / 1 |

**The model loses to drift by 48 pp on held-out.** The leaky verdict's
"+7.8 pp over topmost" came from the leak and the straw-man baseline. Without
`is_anchored`, the strongest weights are `items` +1.76, `regions_on_page`
−0.79 and `solutions_inside` +0.71. So the model learns "a region with
sub-questions is the kind the publisher marks", which is a property of the
activity, not of which candidate an icon points at.

A caveat for Phase 4a, stated plainly: drift is near-perfect partly because
the labels are themselves settled by drift, so the comparison favours the
baseline by construction. But the model sees no icon position at all, so no
fairer truth would let it pick "which region does this icon mean" better than
the icon's position does. Held-out has only 25 usable sheets and a single
contested one, so the contested row says nothing either way. By the brief's
rule (a margin under 5 pp), Phase 4a should record that the ranker is not
worth carrying.

## Before / after (§0.4 format, `runs/phase0-baseline.json`, profile `27b80aacc68e`)

| Split | Books | Loss | Cuts | Cuts/region | Tall | Overlaps | Slivers | Match | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train (before) | 18 | 6.926 | 888 | 0.070 | 40 | 0 | 0 | 91.9 % | 77.9 % |
| train (after) | 18 | 6.926 | 888 | 0.070 | 40 | 0 | 0 | 91.9 % | 77.9 % |
| held-out (before) | 9 | 8.356 | 386 | 0.090 | 10 | 0 | 0 | 95.0 % | 62.8 % |
| held-out (after) | 9 | 8.356 | 386 | 0.090 | 10 | 0 | 0 | 95.0 % | 62.8 % |

The numbers are identical, as they should be. Under the default profile the
ruler and the candidate's blocks come from the same computation. Task 2
changes what a *non-default* profile is scored against, not what the defaults
score.

## Verification

- Tests: `pytest tools/hotspot_extraction/train tools/hotspot_extraction/scanner tests/test_linking.py`
  gives **163 passed**. The repo's `tests/` gives **238 passed**. New tests:
  - `test_fit.py::TestTheRulerDoesNotMove::test_block_knobs_cannot_move_the_ruler`
    (the brief's test)
  - `test_fit.py::TestTheRulerDoesNotMove::test_the_knobs_do_move_the_blocks_growth_sees`
    (proves the first test is not vacuous; it also asserts that recomputing the
    ruler equals reusing it)
  - `test_fit.py::TestGates` (4 tests, synthetic `BookScore`s)
  - `test_learn.py` (9 tests: no anchored feature, what counts as anchored, a
    real book yields 0 anchored examples, the baseline picks the cheapest, and
    the contested subset)
- `cache.py --all`: 27 shards current.
- `objective.py --verify --workers 2`: **27/27 equal**.
- Rebake `scan.py --all --force --trace --workers 2`: 27 baked, peak 511 MB
  total USS. On all 27, `regions.json` pages and `diagnostics.json.gz` pages are
  **identical to HEAD**. The only file changes are `builtAt` and the new
  `"profile": "27b80aacc68e"` stamp (the previous bakes predate the stamp).
- `compare_scorecard.py --skip-scan`: the report is byte-identical to the
  committed one. It FAILS two gates, both **pre-existing and not caused by
  this phase**:
  - 7 overlaps, all on books with no local PDF (`59158d87` ×3, `7a92f6d0` ×2,
    `641b8d6a`, `e2a410e4`). Their bakes come from older code and cannot be
    rebaked on this machine.
  - `d6dd5587` 11 → 12 violations, measured against the legacy-JS baseline
    that Phase 5 is to re-record.
- Reader: it was not running, so there was nothing to restart. No hotspot
  changed, so there is nothing new to see.

## Left for later phases

- Phase 2's screen check ("if `solutions_total` falls, task 2 is not
  effective") can now read `stats.solutions` and `stats.panels` in each score
  row. The ruler cannot move by construction, so any change there would point
  to a bug.
- `Evaluation.gates` is only called by `fit.py --final`. It is not a
  constraint inside the search, as the brief specified. If Phase 2's fit walks
  a no-manifest book towards empty, `--final` will show it, but the fit will
  already have spent its budget there. A penalty inside the loss is the
  obvious next step if that happens.
