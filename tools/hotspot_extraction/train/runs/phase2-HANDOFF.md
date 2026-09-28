# Phase 2 handoff: fit the profile (cloud run)

Done 2026-09-27/28 in a Claude Code cloud sandbox on branch `phase2-fit-profile`,
starting from `8145d81`. It followed `phase2-RESUME.md`. Two gated fits ran to
their 4-hour budgets. They were combined by the same-sign rule and passed the
training gates and the sub-item check. `--final` was then run **once**. A
profile ships in `scanner/profile.json`, but it moves **one knob**. Read
"The combine discarded almost everything" before relying on it.

## Commits

- WIP checkpoints during the fits: `f3cfe8a`, `f0b076a`, `3b9587f`, `8dfccfe`,
  `003c01c`, `877c134`, `ccc8b8a`.
- Both fits finished: `c9766f0` ("wip(train): phase 2 - gated fits A and B (cloud run)").
- Profile shipped: the commit that adds this file ("feat(train): phase 2 - fitted
  profile"). A file cannot hold its own commit's hash; take it from `git log`.

## Environment and verification

- CPython **3.14.0rc2**, installed with `uv python install 3.14` into a venv.
  The system Python was 3.11.15, which cannot even collect the tests
  (`NameError: name 'Any'` in `scanner/test_trace.py`). uv had no 3.14.4 build
  (`error: No download found for request: cpython-3.14.4-linux-x86_64-gnu`).
- pymupdf **1.28.2**, psutil 7.2.2.
- The parse cache came from the private `rayytor/interaktiv-cache`, cloned to
  `/tmp/interaktiv-cache` and symlinked as `.cache/primitives`, which
  `git check-ignore` confirms is ignored. It holds 27 shards and was not
  committed.
- `pytest tools/hotspot_extraction/train tools/hotspot_extraction/scanner`:
  **154 passed, 10 skipped**, before the run and again with the profile
  shipped. The skips need PDFs.
- `objective.py --verify --workers 2`: **replay == bake on 27/27 books**.
- Hardware: nproc 4 and 16 GB RAM. Following the brief, the fits ran **one
  after the other with 3 workers**. One evaluation took about 47 s, against
  14–21 s locally with 6 workers, and RAM in use stayed near 1 GB.

## The two fits

Both started from the defaults (`3aae0c22cfb6`) and printed baseline
`+5.86897`. Both reused `phase2-screen.json` (65 movers, 9 inert) and ran to
the 240-minute budget. Neither reached CMA-ES: coordinate descent pass 1
alone used the whole budget.

| Fit | Seed | Order | Start → end (UTC) | Evals | Best loss | Cuts | Match | Coverage | Regions | Knobs moved |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 20260922 | screen rank | 09-27 16:57 → 20:57 | 193 | −0.0913 | 782 → 61 | 92.6 → 98.5 % | 77.9 → 77.7 % | 12,685 → 11,858 | 13 |
| B | 7 | `--shuffle` | 09-27 20:57 → 09-28 00:58 | 200 | 3.8650 | 782 → 530 | 92.6 → 94.5 % | 77.9 → 78.2 % | 12,685 → 12,524 | 18 |

Both have 0 overlaps, 0 tall and 0 slivers, and every kept candidate passed
the gates (the fitter rejects any that fails).

- A `bestChanged`: `RETREAT_COST` 0.05, `PANEL_LINES` 1, `PANEL_MIN_H` 25,
  `COLUMN_CLEAR` 0.575, `PANEL_MIN_W` 46.67, `FOOTER_BAND` 52, `PAD` 32,
  `GRAPHIC_REACH` 0.2625, `BODY_LINE_WIDTH` 20, `SNAP_EXPAND_SHARE` 0.9,
  `HEADING_RATIO__regions` 1.455, `HEADER_BAND` 24, `FIGURE_SPAN` 66.
- B `bestChanged`: `FOOTER_BAND` 32, `CAPTION_SIDE` 4, `PAD` 0, `BRIDGE` 2,
  `FIGURE_AREA` 3350, `BODY_LINE_CHARS` 51, `FIGURE_SPAN` 51,
  `BAND_REACH_ABOVE` 2, `FIGURE_SHARE` 0.625, `RETREAT_SHARE` 0.8417,
  `HEADING_RATIO__regions` 1.1167, `GRAPHIC_HELD` 0.625, `HOTSPOT_GAP` 12,
  `FLOW_BREAK` 38.33, `HEADING_RATIO__prompts` 1.2133, `ANCHOR_ABOVE` 4,
  `GUTTER_RATIO` 0.1, `GRAPHIC_SHARE` 0.2.

Logs: `phase2-fit-{a,b}.json` and `.stdout`. Profiles: `phase2-profile-{a,b}.json`.

## Step 1: combine (`phase2-combine.py`, `phase2-combine.json`)

The rule keeps a knob that moved the same way from its default in both
`bestChanged`, with fit A's value, and resets every other knob. Only
**`FIGURE_SPAN` 30 → 66** qualified (A 66, B 51).
- 3 knobs moved in opposite directions: `PAD` (A 32, B 0), `FOOTER_BAND`
  (A 52, B 32) and `HEADING_RATIO__regions` (A 1.455, B 1.117).
- The other 23 moved in only one fit.

Combined profile `c7de24295a4b`, training split only, against the defaults:
loss 5.869 → 5.702, cuts 782 → 770, match 92.62 → 92.90 %, coverage and
regions unchanged. **Gates: no failure.**

## Step 2: sub-item check (`phase2-subitems.py`, `phase2-subitem-check.json`)

| Book | Profile | Regions | Sub-items | Digit-labelled regions |
| --- | --- | ---: | ---: | ---: |
| `550e601a` | defaults | 174 | 279 | 32 |
| `550e601a` | combined | 174 | 280 | 32 |
| `0a3fbb41` | defaults | 717 | 252 | 207 |
| `0a3fbb41` | combined | 717 | 252 | 207 |

- No region was gained. On `550e601a` only pages 41 and 42 differ, and only
  in which lettered activity holds a sub-item (p41 `a` 1→2 and `d` 3→4; p42
  `i` 2→1). No numbered sub-item became an activity. **Pass.**
- For reference, fit A's own profile gave 154 and 663 regions, and fewer
  digit-labelled ones (14 and 184). So fit A does not have the sub-item fault
  of the earlier `COLUMN_CHANNEL` run either.

## Step 3: final scoring (run once, `phase2-final.json`, `phase2-final.stdout`)

| Split | Books | Loss | Cuts | Cuts/region | Tall | Overlaps | Slivers | Match | Coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train (defaults `3aae0c22cfb6`) | 18 | 5.869 | 782 | 0.0616 | 0 | 0 | 0 | 92.6 % | 77.9 % |
| train (shipped `c7de24295a4b`) | 18 | 5.702 | 770 | 0.0607 | 0 | 0 | 0 | 92.9 % | 77.9 % |
| held-out (defaults) | 9 | 7.497 | 320 | 0.0749 | 0 | 0 | 0 | 95.0 % | 62.8 % |
| held-out (shipped) | 9 | 7.371 | 320 | 0.0749 | 0 | 0 | 0 | 95.45 % | 62.8 % |

**Gates: no failure on either split.** No book lost match rate.

Per book (loss, cuts = panel + answer-space, match, coverage = sheets with a
region, regions):

| Split | Book | Loss | Cuts | Match | Coverage | Regions |
| --- | --- | --- | --- | --- | --- | --- |
| train | `09f62a7e` | -0.203 → -0.203 | 2 → 2 | 100.0 % → 100.0 % | 65.8 % → 65.8 % | 440 → 440 |
| train | `0a3fbb41` | 7.913 → 8.471 | 47 → 51 | 89.5 % → 89.5 % | 74.7 % → 74.7 % | 717 → 717 |
| train | `0e966773` | 8.294 → 7.795 | 45 → 43 | 97.6 % → 98.2 % | 69.7 % → 69.7 % | 529 → 529 |
| train | `1903e365` | 8.619 → 8.619 | 52 → 52 | 100.0 % → 100.0 % | 61.7 % → 61.7 % | 563 → 563 |
| train | `1cc573f6` | 5.455 → 5.455 | 23 → 23 | 87.5 % → 87.5 % | 84.1 % → 84.1 % | 606 → 606 |
| train | `3d372038` | 1.198 → 1.198 | 4 → 4 | 95.5 % → 95.5 % | 79.2 % → 79.2 % | 370 → 370 |
| train | `4442fde4` | 6.841 → 5.446 | 61 → 50 | n/a → n/a | 89.1 % → 89.1 % | 789 → 789 |
| train | `550e601a` | 4.366 → 2.809 | 5 → 3 | 89.8 % → 91.8 % | 54.8 % → 54.8 % | 174 → 174 |
| train | `66098271` | 14.070 → 14.070 | 108 → 108 | 85.7 % → 85.7 % | 80.1 % → 80.1 % | 899 → 899 |
| train | `753fcdb0` | 8.106 → 8.106 | 97 → 97 | 80.2 % → 80.2 % | 93.9 % → 93.9 % | 1907 → 1907 |
| train | `7763e45b` | 4.770 → 4.770 | 23 → 23 | 100.0 % → 100.0 % | 56.7 % → 56.7 % | 431 → 431 |
| train | `93bf209f` | 0.616 → 0.616 | 6 → 6 | 100.0 % → 100.0 % | 77.0 % → 77.0 % | 433 → 433 |
| train | `a0e5ed1a` | 8.065 → 8.065 | 121 → 121 | 85.1 % → 85.1 % | 93.0 % → 93.0 % | 2011 → 2011 |
| train | `ad3f3275` | -0.056 → -0.056 | 0 → 0 | 100.0 % → 100.0 % | 5.6 % → 5.6 % | 5 → 5 |
| train | `afec0d89` | 11.907 → 11.907 | 72 → 72 | 92.9 % → 92.9 % | 84.2 % → 84.2 % | 636 → 636 |
| train | `d13a75d3` | 8.013 → 8.013 | 42 → 42 | 100.0 % → 100.0 % | 73.7 % → 73.7 % | 480 → 480 |
| train | `d6dd5587` | -0.676 → -0.676 | 1 → 1 | 100.0 % → 100.0 % | 79.6 % → 79.6 % | 838 → 838 |
| train | `df1e313c` | 8.343 → 8.226 | 73 → 72 | 96.6 % → 96.6 % | 86.5 % → 86.5 % | 857 → 857 |
| held-out | `11941059` | 2.505 → 2.505 | 10 → 10 | 97.0 % → 97.0 % | 82.6 % → 82.6 % | 367 → 367 |
| held-out | `3a4479c7` | 7.014 → 7.014 | 44 → 44 | n/a → n/a | 69.1 % → 69.1 % | 571 → 571 |
| held-out | `51cdbbce` | 4.976 → 4.976 | 42 → 42 | 86.8 % → 86.8 % | 91.1 % → 91.1 % | 1290 → 1290 |
| held-out | `6e4f65dc` | -0.154 → -0.154 | 0 → 0 | 100.0 % → 100.0 % | 15.4 % → 15.4 % | 78 → 78 |
| held-out | `79cbecfa` | 25.414 → 25.414 | 114 → 114 | 87.5 % → 87.5 % | 59.2 % → 59.2 % | 485 → 485 |
| held-out | `7fad03e9` | 5.231 → 5.394 | 36 → 37 | 100.0 % → 100.0 % | 63.2 % → 63.2 % | 614 → 614 |
| held-out | `a7a7886a` | -0.031 → -0.031 | 0 → 0 | 100.0 % → 100.0 % | 3.1 % → 3.1 % | 4 → 4 |
| held-out | `c9f63718` | 14.130 → 12.830 | 25 → 24 | 96.3 % → 100.0 % | 57.7 % → 57.7 % | 179 → 179 |
| held-out | `cb558332` | 8.388 → 8.388 | 49 → 49 | 89.7 % → 89.7 % | 81.3 % → 81.3 % | 687 → 687 |

The rule-by-book picture is mixed:
- `0a3fbb41` (train) got worse: cuts 47 → 51, loss 7.91 → 8.47.
- `7fad03e9` (held-out) got worse: cuts 36 → 37.
- `4442fde4` (61 → 50), `0e966773`, `550e601a`, `df1e313c` and `c9f63718`
  improved.

## Phase 2 acceptance (PLAN.md): missed

- Held-out loss is **7.371**; the target was below 6.0, missed by 1.37.
- Held-out cuts per region are **0.0749**; the target was below 0.06.
- Tall, overlaps and slivers are 0, there is no gate failure, and no held-out
  book lost match. Those targets are met.
- The fallback ("ship if every component is no worse and total cuts are
  lower") holds only in aggregate:
  - held-out total cuts are unchanged (320), not lower;
  - two books got slightly worse (above).
- The brief said to ship if steps 1–3 passed, and they did, so it is
  shipped. It is a small, near-neutral change: one knob.

## The combine discarded almost everything (for the user to decide)

Fit A alone reached training cuts 61 with no gate failure, and it passed the
sub-item preview. The same-sign rule kept one of its 13 knobs. The main
reason is that the two descents walked different paths:
- B visited `PAD` early, from the defaults, and took 0.
- A reached `PAD` after `RETREAT_COST` 0.05 and the panel knobs, and took 32.
- Neither fit visited most of the other's knobs within the budget: each did
  about 200 evaluations of pass 1, on 3 cores.

So "moved only in one fit" here mostly means "the other fit never reached
it". It does not mean the knob is noise. The rule was applied as written. A
larger budget (more cores, more minutes) or a combine rule that also
evaluates A's own profile would be a design decision for the user.

Held-out has now been scored for `c7de24295a4b`. Under PLAN.md, `--final`
must not be run on a second candidate, such as fit A's profile, in this
phase.

## Changed files

- `tools/hotspot_extraction/scanner/profile.json`: new, `FIGURE_SPAN: 66.0`,
  hash `c7de24295a4b`. The path is the one PLAN.md §0.1 names, and
  `load_profile()` loads it with that hash.
- `train/runs/`: `phase2-fit-{a,b}.json` and `.stdout`,
  `phase2-profile-{a,b,combined}.json`, `phase2-combine.json`,
  `phase2-subitem-check.json`, `phase2-final.json` and `.stdout`, and the two
  helper scripts `phase2-combine.py` and `phase2-subitems.py`.
- No detector or fitter code changed.

## Left to do (on the user's machine: needs the PDFs)

1. Rebake:
   `python3 tools/hotspot_extraction/scan.py --all --force --trace --workers 2`.
   Then confirm `regions.json` carries profile `c7de24295a4b`.
2. Scorecard: `python3 tools/hotspot_extraction/compare_scorecard.py --skip-scan`.
3. Census: `python3 tools/hotspot_extraction/train/census.py --json …`, with
   `--compare runs/phase1-census.json`.
4. Restart the reader:
   `pkill -TERM -f "python3 -m interaktiv_gtk"`, then relaunch.
5. Optional: re-run `objective.py --verify` on local CPython 3.14.4. The
   cloud verify ran on 3.14.0rc2 and was 27/27 equal.
