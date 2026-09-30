# The hotspot pipeline

The reader draws activity regions it did not compute. This is how they are
made, in `tools/`.

## What a bake is

For every book, `activities/books/<id>/regions.json` lists, per page, the
activities found on it: a label (the letter or number printed in the book), the
rectangle of each part (a region that flows across columns is kept as separate
parts, never as one box), the numbered questions inside it, and the page's own
dimensions. `interaktiv_core/regions.py` reads it; `interaktiv_core/linking.py`
joins it to the publisher's interactive index in `activities_meta/` so that a
region with an interactive version opens that version.

## Producing one

```bash
python3 tools/hotspot_extraction/scan.py --only <book id>          # one installed book
python3 tools/hotspot_extraction/scan.py --all --workers 2         # every installed book
python3 tools/hotspot_extraction/fetch_and_bake.py                 # download and bake the whole catalogue
python3 tools/hotspot_extraction/compare_scorecard.py --skip-scan  # grade the bakes against the scorecard
```

`scan.py` runs the scanner in `tools/hotspot_extraction/scanner/` over the
PDF's text and drawing primitives (`primitives.py`), finds activity markers
and question numbers (`markers.py`, `prompts.py`), lays out columns
(`layout.py`), grows regions and snaps their edges to the page's own structure
(`regions.py`, `anchors.py`, `figures.py`), and writes the result
(`serializer.py`). `score.py` measures a bake against the rules the reader
depends on: a region covers the whole figure it refers to, includes the space
for the answer, never cuts through a block, and two activities are never merged.

Batch runs go book by book, each in its own short-lived process at the default
memory limit; a whole-catalogue sweep in one process has taken the machine
down before.

## Packaging books

`package_book.py` and `build_library.py` produce a self-contained library (a
folder with `manifest.json`) for a board that must never download anything;
the reader opens it with `--library`.

## What is in progress

The rule-based scanner is being replaced by a learned detector: a large vision
model labels a chosen subset of rendered pages, a small detector is trained on
those labels, and the scanner's geometry then snaps the detector's boxes to
the page. Its plans and run records (`tools/hotspot_extraction/train/PLAN.md`,
`vision/PLAN.md`, the `runs/` folders) are working notes, not documentation of
the reader; the scanner above remains what produced the bakes the reader
ships with.
