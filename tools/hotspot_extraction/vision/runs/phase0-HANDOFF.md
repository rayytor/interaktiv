# Phase 0 handoff — environment and the ten-minute proof

Date: 2026-09-28. Plan: `tools/hotspot_extraction/vision/PLAN.md`, Phase 0.

## What was built

| Item | State |
| --- | --- |
| `.venv-vision/` (CPython 3.12.14 via uv) with pymupdf, pillow, google-genai, psutil, pytest | done, 130 MB |
| torch 2.11.0+cu128, torchvision 0.26.0+cu128 in the same venv | done; venv is now 6.7 GB |
| `vision/requirements.lock` | frozen after the torch install (17 torch/nvidia lines among 50) |
| `.gitignore`: `.venv-vision/`, `data/vision/` | done |
| Gemini CLI `gemini` 0.61.0 (`npm i -g @google/gemini-cli`) | installed, **not logged in** (error code 41: no auth method). Not needed: see below. |
| Antigravity CLI `agy` 1.2.12 (`~/.local/bin/agy`, from `https://antigravity.google/cli/install.sh`) | installed, **already logged in** through the Antigravity IDE account; no browser step, no key |
| `vision/teacher_prompt.md` | Phase 0 draft prompt, verbatim from the plan |
| `vision/runs/phase0-page31.jpg` + `.index.json` | page 31 of book `0a3fbb41`, 1024×1432 px, JPEG q90, page 569.764×796.535 pt |
| `vision/runs/phase0-agy-*.json` / `.stderr` | raw CLI envelopes for every call made |
| `vision/runs/phase0-page31.teacher.json` | the parsed boxes from the accepted call |
| `vision/runs/phase0_draw.py` → `vision/runs/phase0-page31.png` | teacher boxes (red, `T:`) and old bake boxes (blue, `O:`) drawn on the page |

## Torch (task 2)

Installed after the user's yes (the wheels are a multi-GB download, §0.3
rule 8). First try of the cu128 index worked; no other index was needed.

```
nvidia-smi: NVIDIA GeForce RTX 5060 Ti, driver 610.57.04, 16311 MiB

.venv-vision/bin/python -c "import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
2.11.0+cu128 True NVIDIA GeForce RTX 5060 Ti
```

## Route A works, with one correction to the plan

`agy` runs headless (`-p ... --output-format json`) and returns a JSON
envelope with `response`, `status`, `conversation_id`, `duration_seconds`
and `usage` (input/output/thinking/cache_read/total tokens). Two things the
plan assumed are different in practice:

1. **`@path` does not attach an image.** The agent receives the path as
   text. It looks at pictures with its own `view_file` tool, so the prompt
   must say so: "First use your view_file tool to look at the image file
   `<path>` ... do not run shell commands, only view the file." With that
   sentence the file tool is auto-allowed in headless mode and the model
   sees the pixels. Without it (first attempt) the agent reached for a
   shell command, headless mode denied it, and `response` came back empty
   with `denied_actions: [RunCommand]`.
2. **The response is prose plus a fenced ```json block**, not bare JSON.
   `phase0_draw.py` extracts the fenced block with a regex. Phase 2 should
   either keep that extractor or try `--output-format stream-json
   --json-schema <file>` (the CLI documents schema enforcement for the final
   result in stream-json mode only).

Also worth knowing for Phase 2: `agy models` lists `gemini-3.8-flash-{high,
medium,low}`, `gemini-3.7-flash-*`, `gemini-3.6-flash-*`,
`gemini-3.1-pro-{high,low}`, plus Claude and GPT-OSS entries. Which model
the CLI uses when `--model` is omitted is not printed anywhere; Phase 2
should always pass `--model` explicitly.

Gemini CLI (`gemini`) was installed as the plan said but never logged in,
because `agy` already had the account. If `agy` ever stops working, the
`gemini` route needs `GOOGLE_GENAI_USE_GCA=true` and a one-time browser
login by the user; its `@path` attachment is untested here.

## The calls (page 31 of `0a3fbb41`, 4 requests total)

| Call | Model | Result | in / out / thinking / cache_read tokens | seconds |
| --- | --- | --- | --- | --- |
| `phase0-agy-default` | default, `@path` | empty response, RunCommand denied | 26,423 / 1,662 / 1,519 / 0 | 12.3 |
| `phase0-agy-try2` | default, `@path`, "no tools" | model explained `@path` is text and asked to use `view_file` | 12,495 / 16,144 / 15,914 / 0 | 56.3 |
| `phase0-agy-try3` | default, `view_file` | **6 boxes, a–f; saw the image** | 82,716 / 35,176 / 34,585 / 146,948 | 148.7 |
| `phase0-agy-pro` | `gemini-3.1-pro-high`, `view_file` | 6 boxes, a–f, same layout; saw the image | 15,138 / 9,727 / 9,391 / 12,143 | 79.3 |

(A trivial "reply OK" call with no image costs about 12,300 input tokens:
that is the CLI's own system prompt, so it is the floor per request.)

Proof that the model saw the picture, quoted from the replies:

> try3: "In the photographs on the page, I can see a mountain lake
> surrounded by forested hills and the Flame Towers skyscrapers overlooking
> a city park."

> pro: "In the photographs on the page, I can see a scenic mountain lake
> and modern skyscrapers."

Raw JSON from the accepted call (`phase0-agy-try3`, `[ymin, xmin, ymax, xmax]` in 0–1000):

```json
[
  {"box_2d": [118, 88, 155, 846], "label": "a"},
  {"box_2d": [163, 88, 206, 882], "label": "b"},
  {"box_2d": [601, 88, 676, 886], "label": "c"},
  {"box_2d": [668, 88, 824, 925], "label": "d"},
  {"box_2d": [832, 88, 893, 724], "label": "e"},
  {"box_2d": [902, 88, 923, 790], "label": "f"}
]
```

And from the Pro call, for comparison (agrees to within ~2 % on every edge):

```json
[
  {"box_2d": [115, 80, 162, 880], "label": "a"},
  {"box_2d": [162, 80, 208, 900], "label": "b"},
  {"box_2d": [602, 80, 682, 900], "label": "c"},
  {"box_2d": [685, 80, 825, 940], "label": "d"},
  {"box_2d": [830, 80, 895, 850], "label": "e"},
  {"box_2d": [895, 80, 935, 850], "label": "f"}
]
```

## What the drawn page shows (`phase0-page31.png`)

Old bake (blue) has 7 boxes on this page; the teacher (red) has 6.

- **c**: the teacher includes the two "e.g." dialogue lines under the
  instruction; the old bake stops at the instruction and cuts the example
  off. Teacher is right (§0.3 rule 4: a referenced block whole or not at all).
- **e**: same story with the two example sentences. Teacher is right.
- **d**: both take the whole speech-bubble block with the three questions
  and the three answer lines. Agree.
- **a, b, f**: both take the instruction line(s) plus the blanks. Agree.
- **Old bake's `p31-3`** is a tall box over the right column (the three
  photographs), labelled "3" from the "3 Tourist attractions" option text.
  It is a false positive. The teacher produced nothing there.
- Open question for the Phase 2 prompt: activity **a** says "Look at the
  photos", so by rule 4 its box arguably should include the photographs.
  Neither model did that on the draft prompt. The Phase 2 prompt should say
  in plain words that referenced pictures belong inside the box even when
  they are far from the instruction.

## Cost arithmetic for Phase 2 (real numbers)

Per page with the Pro model: ~15 k input, ~10 k output tokens, ~80 s wall
clock, one CLI request. The subscription is metered in requests, not tokens,
so 1,200 pages is 1,200 requests, about 27 hours of serial wall clock at
80 s each. Run 3–4 CLI processes in parallel to fit a quota-day, and check
that the parallel calls do not trip a rate limit. Money spent in Phase 0: $0.

## Reproduce

```bash
cd tools/hotspot_extraction/vision
agy --model gemini-3.1-pro-high -p "First use your view_file tool to look at the image file runs/phase0-page31.jpg (it is a JPEG; do not run shell commands, only view the file). Then, before the JSON, write one sentence naming two things you can see in the photographs on the page. $(cat teacher_prompt.md)" --output-format json > runs/phase0-agy-pro.json
../../../.venv-vision/bin/python runs/phase0_draw.py   # reads phase0-agy-try3.json
```

## Left undone

Nothing from the Phase 0 task list. Not done on purpose (out of scope):
no other pages rendered, nothing trained, `scan.py` untouched, Gemini CLI
login skipped because `agy` already had the account.

Commit: branch `phase0-vision-env`; the hash is recorded in the follow-up
commit that adds this line.
