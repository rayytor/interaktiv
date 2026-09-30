# Phase 2 handoff — the teacher labels a chosen subset

Date: 2026-09-28, revised 2026-09-30. Plan: `tools/hotspot_extraction/vision/PLAN.md`, Phase 2.
Previous handoff: `phase1-HANDOFF.md`.

**The teacher in this repository is the Gemini API with the user's free-tier
keys, and nothing else (the user's decision of 2026-09-30).** The Antigravity
CLI and IDE routes, the local Ollama / Qwen3-VL-8B teacher, the Qwen Space
runner, their labels (795 CLI pages, 62 Qwen pages), their comparison results
and the original, longer version of this handoff were moved to
`~/Projects/interaktiv-local-teacher/` (see its README; the full branch
history is in `phase2-teacher.bundle` there).

**Status: code built and tested; 66 of 1,215 selected pages labelled
(`data/vision/labels/api/v1/`). Round 1 over the selection has not been run
on the API.** At the free tier's 20 requests per project per day, ten keys
give about 200 pages a day, so round 1 is about six days of runs. Each run
needs the user's yes (§0.3 rule 8).

## What was built

| Item | State |
| --- | --- |
| `vision/select.py` | Stratified, seeded selection from the rendered corpus → `data/vision/select/round1.json`: 1,215 pages over 27 books, 45 per book, seed 20260928. Strata per book: 15 % `manifest` (a publisher icon on the page), 70 % `baked` (old rules found a region, no icon), 15 % `empty`; two-page spreads excluded; shortfalls topped up from `baked` then `empty`. Held-out books are sampled the same way and tagged `heldout` (405 pages). |
| `vision/teacher_prompt.md` | `version: v1` on the first line, then the prompt: Phase 0's draft plus the four §0.3 region rules in plain words, "all the photos", never cut through a picture, same-label-twice = two boxes, "JSON only, no fence". SHA of the body is written into every label. |
| `vision/teacher.py` | The Gemini API through `google-genai`: the page JPEG attached, `response_json_schema`, thinking level LOW on `gemini-3.6-flash`, temperature 0. Keys from `api.md` (gitignored, one per line), round-robin with per-key pacing (`--rpm`, default 10). A 429 whose details name the per-day quota retires that key for the run; other 429s and 5xx cool the key down. When every key is spent the run stops cleanly (exit 3) and says what remains. A rejected schema is asked again once without it; other failures retry twice, then leave a `.failed` marker. Label → `data/vision/labels/api/<version>/<book>/<page:04d>.json` (raw reply, boxes in 0–1000, pixels and PDF points y-up, model, prompt version+sha, key index, usage, timestamp). Skips existing labels; `--limit`, `--only/--pages`, `--sample 0.1` (seeded), `--out-version`, `--hint`, `--dry-run`. Every request → `data/vision/labels/requests.jsonl`; "used today" per key is counted from it. |
| `vision/qa.py` | Per label: cut, sliver, tall, overlap major/minor, publisher-icon miss, label-run sanity, using `scanner.regions.cuts`/`rect_overlap`, the scorecard's `MIN_HOTSPOT`/`TALL_REGION`/`WHOLE_TOL`/`takeable`, the bake's `diagnostics.json.gz` and folio map, and `activities_meta`. Verdict trusted / re-ask / rejected with an optional `--reask` folder; writes `verdicts.jsonl`, a per-book table, `--summary`, and `--write-reask` (a selection with `hint_count` for `teacher.py --hint`). `--agree A B`: greedy IoU 0.7 matching, per-page agreement 2m/(nA+nB), mean. |
| `vision/contact_sheet.py` | 8 pages per PNG at 0.45 scale, red boxes with labels, caption with verdict and counts; from `verdicts.jsonl` (filtered by verdict) or a label folder. |
| `vision/test_phase2.py` | Strata partition and spread exclusion, seeded sampling and top-up, parsing (fences, prose, bad boxes), pixel/point conversion against Phase 0's numbers, prompt version + hint, seeded agreement sample, per-day and per-key request counts, key pool pacing / cooldown / retire, API error classes, gate defects (sliver/overlap/miss/cut), minor overlap, icon band, label runs, greedy IoU agreement, verdicts with a re-ask folder. |

## Plan assumptions corrected

1. **The "manifest but the bake found nothing" stratum is empty in every
   book.** The old bake grows a region from every publisher icon, so a page
   with an icon always has a region. The stratum was redefined as "carries a
   publisher icon" (163 of the 1,215 pages); those are the pages the gate can
   join against, and the "done when" 85 % figure is measured on them.
2. **The icon hangs in the margin, not inside the activity.** On page 20 of
   `0a3fbb41` the three icons sit ~30 pt left of the box edge, level with the
   label letter. The miss test widens each box by 10 % of the page width
   sideways and 16 pt vertically before asking whether the icon is inside.
3. **The overlap rule.** Two activities referring to the same picture
   legitimately overlap. The gate calls an overlap `minor` (not a defect)
   when the intersection is thinner than 6 pt in one direction, and `major`
   otherwise; major overlaps send the page to re-ask.
4. **The free-tier quota is 20 requests per project per day for this model**
   (quota id `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, value 20,
   seen in the 429 details; failed requests count too), not the plan's
   1,500. The per-day id is only in the error's structured details, never in
   its message, so `classify_api_error` reads both. Third-party pages report
   ~500/day for `gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` and
   ~250/day for `gemini-2.5-flash`; not verified here.

## The 66 API pages (`runs/phase2-qa-gemini-api.json`)

About 6 s per page. Gate over 68 pages (66 labels, 2 `.failed` markers):
56 trusted, 12 re-ask, 0 rejected; 194 boxes, 38 cuts, 10 major overlaps,
7 icon misses of 22; manifest pages trusted 6 of 13. The API boxes a "how this
book is organised" page's thumbnails (`09f62a7e` p14), which have no
activity: prompt v2 should say that miniature pictures of other pages are
not activities. Where several questions share one passage or picture, prompt
v1 has no rule for the shared block and the gate sends such pages to re-ask;
a v2 rule for it is the user's decision still to make.

## Reproduce

```bash
cd ~/Projects/interaktiv
.venv-vision/bin/python -m pytest tools/hotspot_extraction/vision/test_phase2.py -q
.venv-vision/bin/python tools/hotspot_extraction/vision/select.py                           # 1,215 pages, 2 s
.venv-vision/bin/python tools/hotspot_extraction/vision/teacher.py --dry-run                # what would be requested, no request sent
.venv-vision/bin/python tools/hotspot_extraction/vision/teacher.py --limit 3                # smoke, 3 requests
.venv-vision/bin/python tools/hotspot_extraction/vision/qa.py --labels data/vision/labels/api/v1 --summary tools/hotspot_extraction/vision/runs/phase2-qa-gemini-api.json
.venv-vision/bin/python tools/hotspot_extraction/vision/contact_sheet.py --verdicts data/vision/labels/verdicts.jsonl --verdict trusted --n 48
```

Round 1, agreement and re-ask, in order (each resumable, each stops cleanly
when every key is spent for the day):

```bash
.venv-vision/bin/python tools/hotspot_extraction/vision/teacher.py                                               # ~1,150 pages, ~200 a day
.venv-vision/bin/python tools/hotspot_extraction/vision/teacher.py --sample 0.1 --out-version v1-agree           # ~120 pages
.venv-vision/bin/python tools/hotspot_extraction/vision/qa.py --agree data/vision/labels/api/v1 data/vision/labels/api/v1-agree --summary tools/hotspot_extraction/vision/runs/phase2-agreement.json
.venv-vision/bin/python tools/hotspot_extraction/vision/qa.py --labels data/vision/labels/api/v1 --write-reask
.venv-vision/bin/python tools/hotspot_extraction/vision/teacher.py --selection data/vision/select/reask.json --hint --out-version v1-reask
.venv-vision/bin/python tools/hotspot_extraction/vision/qa.py --labels data/vision/labels/api/v1 --reask data/vision/labels/api/v1-reask --summary tools/hotspot_extraction/vision/runs/phase2-qa.json
```

## Left undone

Round 1 on the API, the agreement check, the re-ask round, the contact
sheets for the user: all wait for the user's yes. Not done on purpose:
nothing trained, `scan.py` untouched, no hand-drawn or hand-corrected box
anywhere.
