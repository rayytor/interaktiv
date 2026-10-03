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

A region is an activity unless it says `"kind": "content"`. Content regions
are everything else a page holds -- a passage, a poem, an explanation, a
figure, a diagram, a table, an info panel -- so that a teacher can open any
part of a page, and step through a whole page in focus mode. They carry no
label and no questions, are never joined to the publisher's index, and are
listed in the page's reading order together with the activities. Front matter,
chapter openers, running heads and the back matter (the bibliography and the
map plates after it) get none.

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

## Content regions

`scanner/content.py` makes them, after the activities of a sheet are settled
and without moving them. The sheet's atoms -- lines of type, pictures, drawn
panels, ruled tables, the bodies and strokes of vector diagrams, and the
activity regions as obstacles -- are parted by a recursive XY-cut: down a clear
channel between two columns, across at the white between two blocks, above a
heading, round anything the page draws as one object. Every cut is a straight
clear line, so no two regions overlap and none lies on an activity. A diagram
keeps its labels and caption; a paragraph is never cut through a sentence; a
block of type taller than half the sheet is cut at a paragraph seam.

Which sheets are not content is decided for the whole book: the front matter
ends at the first chapter opener (a sheet carrying its chapter number in
poster type) or where the contents page says chapter one starts; the back
matter starts at the last bibliography, when that ends near the end of the
book, or at the run of plates the book ends in; and a region that stands in
the same place on sheet after sheet is a running head.

A sheet that is one form -- its title at the top names it a performance task,
an assessment form or a rubric -- is a single activity region for the whole
sheet (`content.page_form`). A region may be as tall as the sheet.

`vision/content_coverage.py` measures the result: the share of a book's lines
of type that lie in some region, and that no two regions overlap.

## Packaging books

`package_book.py` and `build_library.py` produce a self-contained library (a
folder with `manifest.json`) for a board that must never download anything;
the reader opens it with `--library`.

## What is in progress

Since October 2026 the activities come from a learned detector by default
(`scan.py --engine vision`): a large vision model labelled a chosen subset of
rendered pages, a small detector was trained on those labels, and the
scanner's geometry snaps the detector's boxes to the page
(`vision/snap.py`). `--engine rules` is the rule-based scanner described
above, kept for comparison. The plans and run records
(`tools/hotspot_extraction/train/PLAN.md`, `vision/PLAN.md`, the `runs/`
folders) are working notes, not documentation of the reader.
