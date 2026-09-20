# Reading order: the limitation left in the activity model

This is a design note, not a bug report with a patch attached. The activity detector in
`js/activities.js` gets the *regions* right on the pages we know about, but the *order* it
reads them in is still page-global column-major, and that is wrong on pages whose columns
do not span the sheet. This note records what the problem is, why the current rule exists,
and what a fix would look like, so the work can be picked up later without re-deriving it.

## Symptom

`analyzePage()` sorts the cells of a page with

```js
cells.sort((a, b) => a.column - b.column || b.top - a.top);
```

— every cell of column 0 top to bottom, then every cell of column 1, and so on. So every
activity in the left column is read before any activity in the right column, wherever they
sit on the page.

On `matematik.pdf` page 16 the page is a boxed exercise in the top right (numbered `1.`
`2.`, printed beside a figure) above a full-width section that also runs `1.` `2.`. A
reader meets the boxed exercise first. The detector lists the lower section first, because
its labels are in column 0. `→` / `←` in focus mode and the Activities sidebar both follow
that order.

The same applies to any page where a column starts lower or ends higher than its
neighbour, which is exactly the shape that the column-closing rule (see `columnFlowEnd`)
made detectable in the first place.

## Why the current rule is there

It is right for the common case, and the obvious alternative is not. An InDesign spread
really does flow the left column top to bottom and then the right column top to bottom: a
right column whose first activity starts higher up the page than the left column's last
one is still read second. Ordering cells by their vertical position instead — top to
bottom, left to right within a band — would interleave the two columns of an ordinary
two-column page and produce `a c b d`. The comment above the sort says as much, and it
should stay true of whatever replaces it.

## Shape of a fix

Group strips into **regions** before ordering, in the spirit of a recursive XY-cut:

1. Walk the strips produced by `segmentStrips()` top to bottom and start a new region
   whenever the column structure changes (the set of cells a strip is divided into).
2. Order regions top to bottom.
3. Within a region, keep the existing column-major order over its cells.

An ordinary two-column page has exactly one region, so it is unaffected. Page 16 has a
region where only the right column is live, above a region that is one full-width cell, and
comes out in reading order. A page with a full-width photo strip across the middle becomes
three regions, which is also what a reader does with it.

The delicate part is a column that simply *continues* past a region boundary — a numbered
list running down the left column, interrupted by a full-width figure, resuming
underneath. Splitting there would read the figure between the two halves of the list, which
is right, but it also means the activity above the figure and the one below it end up in
different regions; check that `longestLabelRun()` still validates the sequence across the
seam before trusting the result.

## What it touches

Order is not cosmetic here — it feeds the algorithm:

- `ordered` is the input to `longestLabelRun()`, so changing it changes which labels
  survive validation (the restart-at-`1` rule is order-sensitive).
- The `running` pointer in the seed-regions loop attaches every markerless cell to the last
  activity seen *in cell order*, so re-ordering moves full-width figures between activities.
- `activities` is emitted in that order, so the Activities sidebar, `dumpActivities()` and
  `ActivityDetector.step()` all follow it.

## How to verify a change to it

```bash
node tools/hotspot_extraction/tests/detect.mjs pdf_parts/full_pdf.pdf   > /tmp/before-full.txt
node tools/hotspot_extraction/tests/detect.mjs pdf_parts/matematik.pdf  > /tmp/before-mat.txt
# ... make the change ...
diff /tmp/before-full.txt /tmp/after-full.txt
```

The totals at the foot of each dump (516 activities / 884 questions for `full_pdf.pdf`,
1088 / 485 for `matematik.pdf`) must not fall, and the per-page label sequence is the column
to watch: a reading-order change that drops or reorders labels has changed validation, not
just presentation.

Those two numbers are only the two books that happen to be at hand, though, and a
reading-order change moves every book. `npm run scorecard -- --baseline` is the check that
matters: it replays the detector and the manifest join over every installed book and fails
if any proxy-oracle count rises or any link rate falls against `tools/hotspot_extraction/tests/scorecard.json`.
