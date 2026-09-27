# Phase 2: where it stopped, and how to resume

Stopped 2026-09-27 19:25 on branch `phase2-fit-profile`. No profile has been
shipped: `scanner/profile.json` does not exist, and the bakes are Phase 1's
(`3aae0c22cfb6`). Read `PLAN.md` §0 and Phase 2, `phase1-HANDOFF.md`, and the
message of commit `4aa4565` first.

## Done

- **The fit machinery works on Python 3.14.** `ChunkPool` used
  `max_tasks_per_child`, which deadlocks on CPython 3.14.4 (gh-115634):
  retired workers are never replaced, so `fit.py` hung on its first
  evaluation. The pool now replaces the executor between evaluations.
- **Screen, all 74 knobs:** `phase2-screen.json`. 65 knobs move the loss and
  9 are inert.
  - The block knobs (`MIN_CELL`, `RULE_*`, `PANEL_*`) lead only because
    moving them makes cuts far worse, with region counts flat. So the Phase 0
    ruler freeze holds, and the brief says to proceed.
  - The strongest single gain is `RETREAT_COST` 0.26: cuts 782 → 622,
    matches 678 → 694.
- **Two exits in the loss, found and closed.** Both runs are kept as
  evidence.
  1. `phase2-fit-a-ungated.json`: cuts fell 782 → 36, but 7 books lost
     21–37 % of their regions. `HOTSPOT_GAP` 16 pushes colliding hotspots
     apart until the smaller one is under the minimum size and dropped.
     - Fix: every candidate is now held to `Evaluation.gates` against the
       start profile (`fit.Run.fitness`, `GATE_PENALTY`).
  2. `phase2-fit-a-gated1.json`: cuts 782 → 51 and no lost-region
     failures, but `550e601a` went 174 → 418 regions and `0a3fbb41`
     717 → 951.
     - Cause: `COLUMN_CHANNEL` 5.75 reads the gap after a sub-item's number
       as a column gutter, so each numbered sub-item became an activity of
       its own. That breaks the region rules, and it also dilutes the
       per-region cut rate.
     - Fix: a new gate, `regions-gained` (more than 20 % and more than 10
       regions).
- **Fit B:** `phase2-fit-b-gated1-stopped.json` was stopped before the new
  gate existed. Do not use it.

## To do

Both gated fits must run again from scratch with the full gate set.

### Setup (for a cloud session)

The parse cache is **not** in this public repo and must never be committed to
it. It is in the private repo `rayytor/interaktiv-cache`.

```bash
gh repo clone rayytor/interaktiv-cache /tmp/interaktiv-cache
mkdir -p .cache && ln -s /tmp/interaktiv-cache/primitives .cache/primitives
pip install -r requirements.txt
python3 -m pytest tools/hotspot_extraction/train tools/hotspot_extraction/scanner -q -p no:cacheprovider   # 164 passed
python3 tools/hotspot_extraction/train/objective.py --verify --workers 2                                     # must be 27/27 equal
```

- If cloning is denied, give the Claude GitHub app access to the private
  repo.
- If verify is not 27/27, the Python or pymupdf differs from local
  (3.14.4). Do not fit on it.
- `cache.py --all` needs the PDFs, so skip it in the cloud.

### Fits

Size `--workers` from `nproc` and `free -m`. The two fits may run
concurrently if the machine has room. Locally, 6 workers took about 21 s per
evaluation and 0.8 GB.

```bash
python3 -u tools/hotspot_extraction/train/fit.py --fit --minutes 240 --workers N --seed 20260922 \
  --screen-from tools/hotspot_extraction/train/runs/phase2-screen.json \
  --out tools/hotspot_extraction/train/runs/phase2-profile-a.json --log tools/hotspot_extraction/train/runs/phase2-fit-a.json
python3 -u tools/hotspot_extraction/train/fit.py --fit --minutes 240 --workers N --seed 7 --shuffle \
  --screen-from tools/hotspot_extraction/train/runs/phase2-screen.json \
  --out tools/hotspot_extraction/train/runs/phase2-profile-b.json --log tools/hotspot_extraction/train/runs/phase2-fit-b.json
```

The baseline must print `+5.86897`; otherwise the screen reuse refuses.

`--shuffle` exists because coordinate descent is deterministic. Without it, two
seeds differ only in CMA-ES, which the 4-hour budget barely reaches.

### Then

1. **Combine.** Keep the knobs that moved the same way in both fits'
   `bestChanged`, and reset the rest to their defaults. Score the combined
   profile per book on the **training split only**, with the gates. If it
   fails a gate, stop and report.
2. **Sub-item check.** Replay `550e601a` and `0a3fbb41` (both training
   books) under the defaults and under the candidate. Confirm that numbered
   sub-items are not activities of their own. The user's rule: a lettered
   activity's region holds its instruction, its numbered sub-items and its
   answer space, and activities are never grouped.
3. **Final scoring, once:**
   `fit.py --final --profile <chosen> --log tools/hotspot_extraction/train/runs/phase2-final.json`.
   Never run it on a second candidate.
4. **Ship.** Copy the profile to `scanner/profile.json`, write
   `phase2-HANDOFF.md` (§0.5 format), commit and push.
5. **Local only.** The rebake (`scan.py` needs the 4 GB of PDFs), the
   scorecard, the census and the reader restart must run on the user's
   machine.

## Reference (training split, defaults)

loss 5.869 · cuts 782 (0.0616/region) · match 92.6 % · coverage 77.9 % ·
12,685 regions · tall, overlaps and slivers all 0.
