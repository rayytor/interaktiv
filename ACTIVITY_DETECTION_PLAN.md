# Implementation Plan — Activity / Question Detection + Click-to-Zoom

Goal: in the existing Interaktiv reader (`js/viewer.js` + PDF.js), automatically detect every
individual activity (`a`, `b`, `c`, …) and every numbered question inside them, make those regions
clickable, and on click zoom the page so only that activity fills the viewport.

Everything below runs **client-side in the current app** — no new dependency, no preprocessing step,
no server change. This is important: the same code path works for `full_pdf.pdf` and for any other
file opened through the file picker.

---

## 0. What the PDF actually gives us (verified, not assumed)

Probed live against the running app with `pdfDoc.getPage(n).getTextContent()` on all 165 pages:

| Fact | Value |
|---|---|
| Structure tree (`getStructTree`) | `null` — the PDF is **untagged** (InDesign export). No semantic help. |
| Annotations | 0 |
| Activity letter markers | single-char items `a`–`z`, font `g_d0_f8`, **font size 11** |
| Body / instruction text | same family but **size 10**, plus prose fonts `g_d0_f14`, `g_d0_f15` |
| Marker count across document | **482** markers on **97** of 165 pages (+4 on p.5 in font `g_d0_f26`) |
| Marker x positions | cluster tightly: `~51` / `~65` (left column), `~286` / `~300` (right column) |
| Instruction hanging indent | body starts at marker x + ~17pt (e.g. marker 51 → text 68) |
| Numbered sub-questions | standalone `"1"`…`"13"` items, same font `g_d0_f8`, **size 10**, at column x + ~17 |
| Page box | 569.76 × 796.54 pt, `rotate 0`, consistent |
| Images | not in text content — must come from `getOperatorList()` (`paintImageXObject` + CTM) |

So detection is a **typographic + geometric** problem, and the signals are unusually clean: the
label font/size pair (`g_d0_f8` @ 11) is used for *nothing else* in the book.

Example — page 33 yields exactly `a b c d` at x=51 and `e f g` at x=286, with zero false positives.

---

## 1. Data model

```js
// One page's analysis, cached in app.activityCache: Map<pageNum, PageActivities>
PageActivities = {
  pageNum, pageWidth, pageHeight,          // PDF user-space pt
  columns: [{ x0, x1 }, ...],              // detected column bands
  activities: [Activity]
}

Activity = {
  id: "p33-a",
  label: "a",
  pageNum: 33,
  column: 0,
  rect: { x0, y0, x1, y1 },                // PDF user space, y-up, already padded
  headline: "Work in groups. Look at the photos below and talk about them.",
  items: [ { id:"p33-a-1", label:"1", rect, text } ]   // numbered sub-questions
}
```

Rects are stored in **PDF user space** (origin bottom-left) and converted to CSS pixels at paint
time with `viewport.convertToViewportRectangle(...)`, so they survive zoom, rotation and re-render
for free.

---

## 2. Detection algorithm (`js/activities.js`, new module)

### 2.1 Collect primitives

```js
const tc  = await page.getTextContent();          // items: str, transform, width, height
const ops = await page.getOperatorList();          // for images + vector boxes
```

From `tc.items` build `{ text, x, y, size: transform[3], font: fontName, w: width, h: height }`.
From `ops` walk `save`/`restore`/`transform` maintaining the CTM and record a rect for every
`paintImageXObject*` and for `constructPath` fills (the coloured activity boxes / rules).
This walk is ~40 ops on a typical page — negligible.

### 2.2 Find label markers

```js
isMarker(it) =>
     /^[a-z]$/.test(it.text.trim())
  && it.font === MARKER_FONT            // learned per document, see 2.3
  && Math.abs(it.size - MARKER_SIZE) < 0.6
```

### 2.3 Learn the marker font instead of hardcoding it

`g_d0_f8` is a per-document generated id, so on first load run `calibrateDocument()`:
sample pages, histogram `(font, roundedSize)` for all standalone `[a-z]` items, and take
the pair whose members form the longest **consecutive alphabetical runs** (`a,b,c,d…`) down a
column. That's the marker style; store `MARKER_FONT`, `MARKER_SIZE`, and
`SUBITEM_SIZE = MARKER_SIZE - 1`. Falls back gracefully on other PDFs (no run found → feature
silently disabled for that document).

The sample is not a fixed count: it widens in interleaving passes until the leading style is
decisive or a budget (a quarter of the book, at most 64 pages) is spent, because a textbook of
running prose carries far less evidence per page than a workbook does — two numbered questions at
the foot of a section every six pages, against a lettered list on nearly every page. Where a book
uses two label schemes at once, both are kept; only a *smaller* size **in the same face** is read as
a level nested inside another, since a different face is a different scheme rather than a level.

### 2.4 Columns

Cluster marker x values with a 12pt tolerance → column left edges (typically 2: ~51 and ~286).
Column band = `[leftEdge - 6, nextLeftEdge - 10]`, last band ends at `pageWidth - margin`
(margin taken from the max text x extent on the page, not hardcoded).

### 2.5 Order and validate

Within each column sort markers by descending `y` (top→bottom). Validate the sequence is
alphabetically ascending **across columns in reading order** (left column top→bottom, then right).
Drop any marker that breaks monotonicity and isn't at a legal restart point — this kills the rare
false positive (e.g. a stray lowercase letter inside a matching exercise). Observed duplicates
like page 36 `e f h c d e b a d g` resolve correctly once split per column and sorted.

### 2.6 Grow the region (the part that matters)

A region must cover the question — the label, the instruction that runs on from it, the worked
example under that and its numbered items — plus the block the question reads, and nothing else:
not the photograph it merely points at, nor the section banner beside it. Naive "everything between
marker N and marker N+1" is therefore **wrong** for this book, and so is stopping at the question's
last line: on p.33 activity `a` is two lines of instruction above a dialogue set in a speech bubble
that spans x=29→528 and half the page's height, and the region is the instruction *and* the whole
bubble.

The band from a label down to the next one (§2.5) says only where a question *may* reach. What of
what is printed there is the question is decided by `flowContent()`, reading down from the label:

1. **Measure**: the opening line shows how wide the text this question is set in is, and the flow
   keeps to the wider of that and the label's own column. A line breaking out of that measure is a
   full-width object the question merely sits above. Reading the measure off the page rather than off
   the column grid keeps a genuinely full-width section whole.
2. **Continuity**: a question's lines follow one another at the leading. Whatever is *drawn* between
   two lines — a photo, a table, a figure — is discounted from the distance first, so a question set
   around the figure it refers to stays whole while blank paper still ends it.
3. **List succession**: a line opening the item the question's list is next owed continues the flow
   however wide the blank above it (a workbook leaves half a page for the answer), and each alphabet
   counts separately so `a) b) c)` nested under `1.` is not confused with the digits around it.
   Before a list has opened the flow tolerates any blank short of a break in the page; once it has,
   the leading rules again, which is what stops the last item running on into the passage below.
4. **Images are never absorbed**: a picture is something a question points at, never part of its
   wording. Images still shape the page in `segmentStrips()`, which is a question about layout.
4b. **Headings end the flow**: a question is set in the type it opens in — the size most of what it
   has taken is set in, since what comes first in a band is as often a caption or a credit. Display
   type opens a section wherever it falls, inside the measure or across the page; a change of face
   is the weaker reading and ends the flow only at a line out at the label's own margin, short
   enough to be naming what follows rather than saying it. Neither reading applies inside a drawn
   panel or over a placed picture, whose titles belong to the block. Small print — a caption, a
   credit, a rotated tab — is skipped rather than read as the end, since a flow that steps down the
   page on furniture never crosses a gap wide enough to stop it.
5. **Panels are all or nothing** (`textPanels()`, `panelFor()`): a shape the page draws around a
   block of text — a speech bubble, a tinted exercise box, a reading card — says where that block
   begins and ends. A dialogue is set to the page rather than to a column, so its short turns pass
   the measure test above while its long ones fail it, and read line by line the region ends up
   covering a ragged half of it. So once the flow has taken any line out of a drawn panel, and runs
   at least half its height, the region snaps out to the panel's frame; a panel the question merely
   stands above, or one that encloses another activity's label, is left alone. The frame is also
   where the padding of step 6 stops, since a drawn edge is already the edge the reader sees.
6. **Clip**: intersect with the page's content box (exclude the running header `THEME n` band and
   the footer page number — both identifiable: font `g_d0_f6`, and y in the top/bottom 45pt).
7. **Pad**: 8pt on all sides, then clamp to the page box.

### 2.7 Sub-question detection

Inside each activity rect, collect standalone `/^\d{1,2}$/` items in `MARKER_FONT` at `SUBITEM_SIZE`,
sorted by (column, -y). Each sub-item's rect runs from its own top down to the next sub-item's top
(or the activity bottom), across the sub-column width. Validate the numbers form `1,2,3,…`; discard
the set if they don't (guards against "Digital Story 2"-style labels).

### 2.8 Headline text

`headline` = concatenation of text items on the marker's own baseline (±2pt) plus following lines
until the first blank-line gap — used for tooltips, the activity list panel, and the zoom-mode caption.

### 2.9 Caching + overrides

- `Map<pageNum, PageActivities>`, computed lazily when a page is rendered, and pre-warmed for the
  next spread during idle (`requestIdleCallback`). Measured cost: text content + op list ≈ 20–40 ms
  per page, and both calls are already cheap because the page object is in PDF.js's cache.
- Optional `GET /api/activity-overrides` (static JSON file, served by the existing static handler):
  `{ "33": { "a": {"rect":[x0,y0,x1,y1]}, "drop": ["c"] } }`. Merged over detected results so any
  page the heuristic gets wrong can be corrected by hand without touching code. Ships empty.

---

## 3. Interaction layer

### 3.1 Hit layer (`.activityLayer`)

A new absolutely-positioned div added in `createPageElementSync()` alongside `.textLayer`, sized to
the viewport, `z-index` above the canvas and **below** the text layer is not possible (text layer
must stay selectable), so instead: activity layer sits *above* the canvas and *below* `.textLayer`,
and receives clicks via `pointer-events: none` on itself + `pointer-events: auto` on the text layer
only while a modifier/selection is active. Simpler and preferred: keep `.activityLayer` on top with
`pointer-events: auto` and re-enable selection by forwarding `mousedown` with >4px drag to the text
layer. Decide with a 10-line spike; default to "activity layer on top, click = focus, drag = select".

Each activity renders one `<button class="activity-hotspot" data-id="p33-a">` positioned from
`convertToViewportRectangle`. Hover → soft outline + label chip (`a`) + headline tooltip.
Sub-question hotspots are nested and only become active in focus mode (§3.3).

### 3.2 Click → zoom (`focusActivity(id)`)

```
1. Remember { viewMode, currentPage, zoomMode, scrollTop } for Esc restore.
2. Switch to a new viewMode: "focus" (book/single/scroll untouched underneath).
3. scale = clamp(min(containerW / rectW, containerH / rectH), 0.4, 6)
4. Render ONLY the region, not the whole page:
      const vp = page.getViewport({ scale, rotation, offsetX: -rect.x0*scale, offsetY: -(pageH-rect.y1)*scale });
      canvas.width  = rectW * scale * dpr;   canvas.height = rectH * scale * dpr;
   This is the crop trick: canvas stays ≈ viewport-sized regardless of zoom level, so a 6× zoom on
   an activity costs ~4 MB, not the ~113 MB a full-page 6× canvas would cost. Keeps the project's
   bounded-memory promise.
5. Render text layer with the same offset viewport → selection and Ctrl+F still work in focus mode.
6. Animate in: 180 ms transform-based cross-fade from the hotspot's on-screen rect to the final
   canvas rect (FLIP), so the zoom reads as a continuous motion rather than a jump.
```

Everything outside is simply not drawn (we render a crop), with the activity centred on a dimmed
backdrop, plus a caption bar showing `Page 33 · Activity a` and the headline.

### 3.3 Focus-mode controls

| Key | Action |
|---|---|
| `Esc` | exit focus, restore previous view/page/scroll |
| `→` / `←` (or `n` / `p`) | next / previous activity, crossing page boundaries |
| `↓` / `↑` | next / previous **numbered question** inside the current activity (zooms one step deeper) |
| `+` / `-` | nudge focus zoom |
| `A` | toggle activity hotspots on/off globally |
| double-click on page | focus the activity under the cursor |

### 3.4 Sidebar "Activities" tab

Third tab next to Thumbnails/Outline: a flat, lazily-filled list `Page 33 — a · Work in groups…`.
Click jumps straight into focus mode. Doubles as the QA surface for detection quality.

---

## 4. Files touched

| File | Change |
|---|---|
| `js/activities.js` **(new, ~350 lines)** | calibration, detection, region growth, cache, override merge |
| `js/viewer.js` | import module; build `.activityLayer` in `createPageElementSync`; call detection after `renderPageCanvas`; new `focus` view mode + `focusActivity/exitFocus/nextActivity`; keybindings; sidebar tab |
| `css/viewer.css` | `.activityLayer`, `.activity-hotspot` (idle/hover/active), label chip, tooltip, focus backdrop + caption bar, FLIP transition |
| `index.html` | Activities sidebar tab + panel, focus-mode caption/overlay markup, help-modal rows |
| `activity-overrides.json` **(new, `{}`)** | manual corrections, served statically |
| `README.md` | document the feature and the new keys |

No change to `server.py` / `main.py` (the static handler already serves the JSON, with Range support).

---

## 5. Phases

1. **Phase 1 — detection core.** `activities.js` with calibration + markers + columns + ordering.
   Ship a debug command (`window.pdfApp.dumpActivities()`) that prints per-page label sequences;
   verify against the 97-page ground truth already captured (482 markers).
2. **Phase 2 — region growth.** Add full-width absorb, image absorb, header/footer clipping.
   Validate visually with a debug overlay that draws every rect in colour (`?debug=activities`).
3. **Phase 3 — hotspots + hover.** Layer, positioning, hover affordance, no zoom yet.
4. **Phase 4 — focus mode.** Cropped-viewport render, FLIP animation, Esc/next/prev, caption.
5. **Phase 5 — sub-questions.** Numbered detection + `↓`/`↑` deeper zoom.
6. **Phase 6 — sidebar list, overrides file, README, help modal.**

Phases 1–2 are the risk; 3–6 are mechanical.

---

## 6. Edge cases and how each is handled

- **Activity spans two pages** (continues on the facing page): if a column's last activity runs to
  the bottom margin and the next page starts with a letter that continues the sequence, mark
  `continuesOnNextPage` and let focus mode show both crops stacked.
- **Book/spread mode**: hotspots live per page wrapper, so a click resolves to its own page; focus
  mode always renders a single crop.
- **Rotation**: rects are user-space; `convertToViewportRectangle` handles `rotation` already.
- **Non-textbook PDFs**: calibration finds no alphabetical run → feature disabled, zero UI shown.
- **Pages 1–17 / back matter** (no markers): nothing rendered, no cost.
- **Page 5's `g_d0_f26` markers**: calibration keeps the dominant style but a secondary style whose
  members also form a run is accepted, so those 4 are picked up too.
- **Text selection regression**: guarded by the drag-vs-click rule in §3.1; must be explicitly tested.
- **Memory**: activity cache holds ~10 rects/page × 165 pages ≈ trivial; canvas cost bounded by §3.2.

---

## 7. Acceptance criteria

- ≥ 95% of the 482 known markers become clickable activities with a rect that visually contains the
  whole activity and nothing from its neighbour (spot-checked on 20 pages across all themes).
- Click → fully rendered zoomed activity in < 250 ms on an already-rendered page.
- Focus-mode canvas never exceeds ~8 MB regardless of zoom.
- Esc always returns to the exact previous view, page and scroll position.
- Text selection and Ctrl+F still work in normal and focus mode.

---

## 8. Modernized Python Scanner Architecture (`scan.py` & `tools/hotspot_extraction/scanner/`)

While `js/activities.js` remains intact in the browser as the client-side live fallback for ad-hoc PDFs, server-side pre-baking and library packaging have been modernized to a high-speed Python pipeline powered by `PyMuPDF` (`fitz`):

### Modular Components (`tools/hotspot_extraction/scanner/`)
1. **`primitives.py`**:
   - Zero-decode image bounding boxes read directly from PDF dictionary headers (`/Width`, `/Height`, CTM) without allocating uncompressed RGBA pixel buffers.
   - Vector path extraction and text span extraction with low memory overhead.
2. **`layout.py`**:
   - Page geometry, margins, running header/footer exclusion, and column detection.
3. **`markers.py`**:
   - Modal typographic hierarchy, alphabetic sequence validation, and Turkish compound step markers (`1. adım:`, `2. adım:`).
4. **`regions.py`**:
   - Monotonic column flow growth, drawn panel snapping (dialogue bubbles, tinted boxes), solution space absorption (answer lines, grid tables), and overlap prevention.
5. **`anchors.py`**:
   - Reconciliation of publisher interactive elements from `activities_meta/<book-id>.json`.
6. **`serializer.py`**:
   - Production of deterministic, compressed `regions.json` (`BAKE_VERSION = 2`) matching `js/viewer.js` specifications, alongside `diagnostics.json.gz`.
7. **`scan.py`**:
   - Multi-process worker pool with bounded memory (< 500 MB total RSS across 4 workers) and high throughput (> 60 pages/second), processing the entire 56-textbook catalog in under 2 minutes.
