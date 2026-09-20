# Interaktiv - Lightweight & Efficient PDF Reader

A blazing-fast, lightweight PDF reader application specifically engineered to handle massive, complex PDF documents (such as the 140MB, 165-page InDesign textbook `full_pdf.pdf`) with near-instant initial render times and a strictly bounded memory footprint.

## Highlights & Features

- 📖 **Two-Page Book Spread (Default)**: Authentic open-book viewing experience with facing pages (even on left, odd on right), center spine shading, and standalone cover display.
- 🔄 **3 Switchable View Modes**:
  - **Two-Page Book Spread** (`B`): Dual facing pages.
  - **Single Page View** (`S`): Clean single page centered display.
  - **Continuous Vertical Scroll** (`C`): Virtualized infinite scroll container with automatic offscreen canvas eviction.
- ⚡ **RFC 7233 Byte-Range Streaming**: Uses HTTP 206 Partial Content range requests. Instead of downloading all 140MB up front, the reader fetches only the necessary byte chunks for visible pages on demand. Initial page render completes in **< 900ms**!
- 🛡️ **Zero External Dependencies**: Server runs purely on Python 3 standard library (`http.server`, `socketserver`, `urllib`). No `pip install` or `npm install` needed.
- 📴 **100% Offline & Standalone**: Mozilla PDF.js v4 core and worker are vendored locally.
- 🖥️ **Desktop App Experience**: `launch.sh` launches directly in frameless Google Chrome / Chromium application mode (`--app=http://127.0.0.1:8080`) with an isolated profile, feeling just like a native Linux desktop application.
- 🎯 **Activity & Question Detection + Click-to-Zoom**: Every activity and every question inside it is detected automatically and made clickable. Click one and the reader zooms straight to it — only that activity fills the screen. Detection is typographic (no tagging in the PDF, no preprocessing step), and both the label style and the label *alphabet* are learned per document: a book that runs `a b c` and one that runs `1. 2. 3.` with `a) b) c)` nested inside both work, the steps of a practical are read from the compound markers a Turkish book numbers them in (`1. adım:`, `2. adım:` …), and unrelated PDFs simply show no hotspots. A region covers the question and the block it reads: the label, the instruction that runs on from it, the worked example under that, its numbered items, and — whole, never in part — the dialogue or passage the page draws a frame around; but not the photograph it merely points at or the section banner beside it. The question's own text is recognised by the measure it is set to and by the succession of its list, so it survives being set around a figure and does not run on into the passage that follows it. **516 activities and 884 questions** are found across the 165-page English textbook, and **1088 activities and 485 questions** across the 417-page `matematik.pdf`.
- 🔍 **Full-Text Search (`Ctrl+F`)**: Instant in-document search with match counts, keyword highlighting, and navigation.
- 📑 **Lazy-Loaded Thumbnails & Bookmarks**: Sidebar drawer with lazy-rendered page thumbnail cards and document outline bookmarks.
- 🌓 **4 Reading Themes**:
  - **Dark** (Default, deep slate palette for eye comfort)
  - **Light** (Crisp clean paper tone)
  - **Sepia** (Warm retro book tone)
  - **Inverted** (Night reading contrast mode)
- 🖵 **Fullscreen Presentation Mode (`F`)**: Distraction-free full-screen reading.
- ⌨️ **Extensive Keybindings**: Keyboard-first design with standard navigation shortcuts.

---

## Quick Start

### 1. Launch Reader (Standalone Window)

Run the launch script:

```bash
./launch.sh
```

This starts the background streaming server (if not already running) and opens the reader in standalone app mode defaulting to `pdf_parts/full_pdf.pdf`.

### 2. Launch via Python Directly

```bash
python3 main.py
```

Options:
- `python3 main.py [path/to/any.pdf]` - Open an arbitrary PDF file
- `python3 main.py --port 8085` - Specify custom HTTP port
- `python3 main.py --no-browser` - Start server without launching a browser window

---

## Keyboard Shortcuts

| Keybinding | Action |
|---|---|
| <kbd>→</kbd> / <kbd>PageDown</kbd> / <kbd>Space</kbd> / <kbd>J</kbd> | Next page / spread |
| <kbd>←</kbd> / <kbd>PageUp</kbd> / <kbd>K</kbd> | Previous page / spread |
| <kbd>Home</kbd> / <kbd>End</kbd> | Jump to First / Last page |
| <kbd>B</kbd> | Two-Page Book Spread Mode |
| <kbd>S</kbd> | Single Page Mode |
| <kbd>C</kbd> | Continuous Vertical Scroll Mode |
| <kbd>+</kbd> / <kbd>-</kbd> / <kbd>0</kbd> | Zoom In / Zoom Out / Reset Zoom (Fit Page) |
| <kbd>Ctrl</kbd> + <kbd>Wheel</kbd> | Smooth Zoom with Mouse Wheel |
| <kbd>Ctrl</kbd> + <kbd>F</kbd> | Open Search Bar |
| <kbd>T</kbd> | Toggle Thumbnails Sidebar |
| <kbd>M</kbd> | Cycle Theme (Dark → Light → Sepia → Inverted) |
| <kbd>R</kbd> | Rotate Page Clockwise (90°) |
| <kbd>F</kbd> | Toggle Fullscreen Presentation |
| <kbd>?</kbd> | View Keyboard Shortcuts Cheat Sheet |
| <kbd>Click</kbd> on an activity | Zoom into that activity (focus mode) |
| <kbd>A</kbd> | Toggle Activity Hotspots on / off |
| <kbd>→</kbd> / <kbd>←</kbd> *(in focus)* | Next / Previous activity (crosses pages) |
| <kbd>↓</kbd> / <kbd>↑</kbd> *(in focus)* | Next / Previous numbered question |
| <kbd>+</kbd> / <kbd>-</kbd> / <kbd>0</kbd> *(in focus)* | Nudge / reset the focus zoom |
| <kbd>Esc</kbd> | Exit focus / Close Modals / Close Search |

---

## Architecture

```
interaktiv/
├── server.py              # Multi-threaded RFC 7233 Range HTTP streaming server
├── main.py                # CLI launcher, port finder, and browser orchestrator
├── launch.sh              # Standalone app launcher script
├── index.html             # Single-page application markup & UI structure
├── css/
│   ├── viewer.css         # Modern themes, layouts (book spread, single, scroll)
│   └── pdf_viewer.min.css # PDF.js text layer & selection styling
├── js/
│   ├── pdf.min.mjs        # Vendored Mozilla PDF.js v4 engine
│   ├── pdf.worker.mjs     # Web worker script for off-thread decode
│   ├── activities.js      # Activity / question detection (calibration, regions, cache)
│   └── viewer.js          # Core viewer controller, virtualization, search & focus mode
├── tests/
│   ├── test_activity_overrides.mjs # Override resolution unit tests
│   ├── test_detection.mjs          # Detection unit tests over synthetic pages
│   └── detect.mjs                  # Headless per-page dump, for before/after diffs
├── activity-overrides.json # Optional manual corrections for detected regions
└── pdf_parts/
    └── full_pdf.pdf       # Default 140MB textbook PDF
```

---

## Activity Detection

The textbooks are untagged InDesign exports — no structure tree, no annotations — so
activities are recovered from typography and geometry, entirely in the browser. Nothing
about a particular book is hardcoded; every step below is learned from the document in
front of it:

1. **Calibration.** On load, a sample of pages is scanned for items that could open a
   list: a lone `a`–`z` or `1`–`99`, bare or decorated (`a)`, `a.`, `(a)`, `1.`), or the compound step markers a Turkish book numbers a practical in (`1. adım:`, any casing), standing
   clear of the gutter to its left with instruction text at a hanging indent. The
   (font, size) pair whose labels form the longest consecutive runs down a column is the
   label style. A book numbers its lists at more than one level and the inner level always
   has more entries, so the *largest* style with real evidence wins rather than the most
   numerous one — but only among styles set in the same face, since two levels of one list
   are one typeface at two sizes while a different face is a different scheme altogether,
   not something nested. Sizes within ¾pt of each other in one face are pooled as one
   style: PDF.js reports the size off the text matrix, so one nominal size comes back as
   two whenever a run has been optically scaled to fit its box.
   The sample widens rather than being fixed. A workbook settles the question in fourteen
   pages; a textbook of running prose that puts two questions at the foot of a section
   every six pages does not, and a fixed sample used to leave such a book scoring under the
   bar and switching the feature off for itself entirely. So pages are read in passes that
   interleave with the ones already read, stopping as soon as the leading style is decisive
   or a budget (a quarter of the book, at most 64 pages) is spent. If no style has real
   evidence, the feature disables itself silently.
   Fonts are identified by their metrics rather than by PDF.js's per-object `g_d0_fN` ids:
   a book assembled from per-chapter exports embeds one typeface dozens of times over, and
   keyed by id its evidence would never accumulate anywhere.
2. **Columns, nesting & reading order.** Label x-positions are clustered. A cluster
   further right is a second *column* only where a whitespace channel actually separates
   it from the column it would split — measured against that column's own content, not the
   whole page. Where the text runs straight through instead, the cluster is a deeper
   *level* of the same list (`a) b) c)` indented under `1.`) and its labels become
   sub-items rather than activities. Labels are then read left column top-to-bottom, then
   the next column, and the sequence is validated so strays are dropped; two alphabets in
   one column are validated as two independent runs, since `1.` and a flush `a) b) c)` are
   levels of one list and neither interrupts the other.
3. **Region growth.** Each activity owns the band from its label down to the next label in
   the same column, plus — when the next activity is in another column — the top of that
   column, because text flows left-column-bottom to right-column-top. Column-local text is
   absorbed first; full-width blocks (dialogues, wide graphics) and images (recovered from
   the operator list with the CTM replayed) are then assigned to the band they overlap most.
   A column has a bottom as well as a top: it exists over the height its own content
   occupies, measured on whole lines rather than PDF.js's runs, and where its flow has
   ended and the page has stopped being in columns there, the space below belongs to the
   region beside it. Without that, a boxed exercise in the right column would keep
   absorbing the page to the folio and own the right half of the full-width section
   printed underneath it — while that section's own activities were clipped to the left
   column in exchange.
   A region also ends at a heading. A question is set in the type it opens in — the size
   most of what it has taken is set in, not whatever came first, since what comes first in
   a band is as often a caption or a credit — so a line in display type opens a section,
   wherever on the page it is set. A change of face is the weaker reading and is held to
   more: it ends the flow only at a line out at the margin the label stands on, short
   enough to be naming what follows rather than saying it. Both are needed and neither
   alone is enough: a book sets its headings at the question's own size as often as not,
   and it sets the question itself in two faces just as often — the instruction in roman,
   the question it asks in bold on the line under it — while a bank of words or a table of
   answers opens in another face and is plainly the question's own. Nothing set inside a
   panel the page draws, or over a picture it places, is read as a heading: a reading card
   and a photograph both carry a title that belongs to the block, and whether the question
   takes that block is a question about the block. Small print is skipped rather than read
   as an end — a caption, a credit, a rotated tab down the edge of a section — since a flow
   that steps down the page on furniture never crosses a gap wide enough to stop it.
   Without all this a region ran from the last question of a section straight down over the
   next section's heading, prose and tables.
4. **Questions.** The labels indented under an activity — each alphabet judged on its own,
   so the `1. adım …` steps and the `a) b) c)` that follow them do not cancel out — plus
   standalone digits set in the label font one step down. Either reading is accepted only
   when it forms a complete list
   — the first `n` entries of one alphabet, met in that order down each column or along
   each row — which rejects a caption numbered on its own ("Digital Story 2") as well as a
   grid of numbers that merely happens to run `1..n`, such as a seating plan.

A page often carries two independent lists — the left column running `d e f g` while the
right column starts a new section back at `a`, or a boxed exercise numbered `1. 2.` above a
full-width section that also starts at `1.`. Those are separate questions, so an activity is
identified by its region rather than by its label: the first `e` on page 36 is `p36-e` and
the second is `p36-e-2`. The chip still shows what the book prints; only the id
disambiguates, and hovering, zooming and next/previous stepping act on one of them at a
time.

Results are cached per page (~80 ms per page, computed lazily on render). Anything the
heuristic gets wrong on a given page can be corrected by hand in `activity-overrides.json`.
Overrides are scoped by document — keyed by PDF basename (e.g. `"full_pdf.pdf"`) or by the
PDF's content fingerprint from `pdfDoc.fingerprints[0]` (which survives file renaming) — with
the per-page corrections nested under it:

```json
{
  "full_pdf.pdf": {
    "33": { "b": { "rect": [43, 222, 261, 322] }, "drop": ["c"] }
  }
}
```

A correction is keyed by label (`"b"`, which moves every activity with that label on the
page) or by a single region's id (`"p36-e-2"`); `drop` accepts either. (For backwards
compatibility, a legacy flat object keyed directly by page numbers is also accepted.)

Rectangles are in PDF user space (y-up), so they survive zoom and rotation. Append
`?debug=activities` to the URL to draw every detected region at once.

Detection has no browser dependency, so it can be checked from the command line:

```bash
npm test                                   # unit tests, no PDF needed
node tools/hotspot_extraction/tests/detect.mjs pdf_parts/matematik.pdf > /tmp/after.txt
```

`tools/hotspot_extraction/tests/detect.mjs` prints every activity, region and question of every page, which is the
diff to take before and after touching the algorithm — the change should show up only on
the pages it is meant to change. `READING_ORDER.md` records the one structural limitation
left in the model.

Focus mode renders a **crop** rather than a scaled full page — `getViewport({scale, offsetX,
offsetY})` with a region-sized canvas — so a 6× zoom costs a few MB instead of the ~113 MB a
full-page canvas at that scale would need, keeping the reader's bounded-memory promise.

---

## Performance Benchmarks

- **Document Size**: 146.1 MB (165 pages, high-DPI CMYK assets)
- **Initial Page 1 Render**: **895 ms**
- **Subsequent Page Navigation**: **15 ms – 40 ms**
- **RAM Footprint**: Under **75 MB** browser heap (automatic offscreen canvas eviction)
- **Server Dependencies**: **0** external packages (Python standard library only)
