# GTK4 / Libadwaita School Edition

## Context

Interaktiv's School Edition is today the same web app as the authoring edition: a
Python `http.server` on 127.0.0.1 plus Chromium in `--app` mode, differentiated
only by `body.edition-school` and the larger metrics in `css/school.css`. It
carries a browser, a 1.7 MB vendored pdf.js, a 162 KB typographic detector, and a
lock/heartbeat/watchdog dance in `launch.sh` whose only job is to notice that the
browser window closed.

School Edition does not need any of that. Its books are baked: every activity
region is precomputed in `activities/books/<id>/regions.json`, so `js/activities.js`
— the 3,529-line live detector — has nothing to do and is dropped outright. What
remains is a PDF renderer, a hotspot overlay, a focus zoom, a catalogue, and an
embedded browser for the publisher's HTML activities. All of it is expressible as
a native GTK4/Libadwaita application, which is what a classroom smartboard should
be running: one window, board-sized touch targets, no Chromium, no localhost.

The outcome is a new PyGObject front end for School Edition only. The web UI
(`index.html`, `js/viewer.js`, `js/dashboard.js`, `server.py`) stays untouched for
the "full" authoring edition; the two editions stop sharing a front end and start
sharing a *core* instead.

### Decisions taken

| | |
|---|---|
| Binding | PyGObject — GTK 4.22.4, Adw 1.9.1, gi 3.56.2, Python 3.14 (all installed) |
| PDF backend | PyMuPDF 1.28.2 (already in `requirements.txt`; same library the baker uses, so `regions.json` coordinates line up exactly) |
| Activities | Embedded WebKitGTK 6.0 in an `Adw.Dialog` (`apt install gir1.2-webkit-6.0` — available, not yet installed) |
| Library scope | Full catalogue + install / uninstall / preview, matching today's School Edition |
| Detection | Precomputed only. `js/activities.js` is not ported and not shipped. |

### Verified on this machine

`Adw.{ToolbarView, OverlaySplitView, ToggleGroup, InlineViewSwitcher, ShortcutsDialog, AlertDialog, ToastOverlay, Banner, Spinner}`, `Gtk.Snapshot.push_color_matrix`, `append_scaled_texture`, `Gsk.ScalingFilter.TRILINEAR`, `Gdk.MemoryTexture`/`MemoryFormat.R8G8B8` all present.
PyMuPDF on `books/0e966773-….pdf` (146 MB, 165 pp): full page @ scale 2 = **20 ms / 5.45 MB**; a 200×200 pt clip @ scale 6 = **50 ms**; `pix.x, pix.y` = `(600, 600)`, and `(-1800, 600)` under `prerotate(90)` — that origin *is* pdf.js's `offsetX/offsetY`, so the focus-mode crop trick ports directly. `page.search_for()` returns rects at ~1 ms/page. **`TOOLS.store_maxsize()` and `store_size()` both return `None` in 1.28** — the store cannot be capped, only shrunk.

---

## Architecture

Two new packages at repo root, so `from books_manager import BooksManager` keeps
working unchanged.

### `interaktiv_core/` — no GTK, shared by the GTK app and the baker

| Module | Responsibility |
|---|---|
| `regions.py` | Load/validate a `regions.json` bake. `BAKE_VERSION = 2`; `RegionsBook.load(path)`, `.matches(page_count)` (the `viewer.js:1175` edition guard), `.page(n) -> PageRegions`, `.printed_page(n)`, `.confidence`, `.folio_offset`, `.fingerprint`. Schema authority is `tools/hotspot_extraction/scanner/serializer.py:serialize_activity`. |
| `oges.py` | `OgeIndex.from_kitapoge_list(...)`, `.for_printed_page(p)` — applies the same filters as `viewer.js:1234` (`ogeturu == 1 and data`) and `viewer.js:1330` (`sayfaustuoge and sayfano`). |
| `linking.py` | **The single port of `js/interactive-links.js`** — see below. |
| `geometry.py` | `PageTransform`, the one place PDF rects become widget coordinates. |
| `jobs.py` | Logic lifted out of `server.py`: `trigger_bake` (from `_trigger_jit_bake`, `server.py:60`), `activity_index_url` (from `resolve_activity_asset`, `server.py:690`, path confinement intact), `fetch_activity`, `ensure_thumbnail`, `trim_memory` (from `scan.py:62`). |
| `appdirs.py` | XDG config/state/preview-cache paths. |

### `interaktiv_gtk/` — the front end

```
__main__.py        python3 -m interaktiv_gtk   (argparse mirroring main.py)
app.py             Adw.Application; owns the single BooksManager, Settings, ThemeController
window.py          Adw.ApplicationWindow + Adw.NavigationView (library <-> reader)
render/
  document.py      DocumentHandle — owns one pymupdf.Document; touched ONLY by the render thread
  requests.py      RenderRequest / RenderResult (incl. origin_x, origin_y from pix.x/pix.y)
  service.py       RenderService — thread, priority queue, generations, GLib.idle_add delivery
  cache.py         TextureCache — byte-budgeted LRU of Gdk.Texture
reader/
  session.py       DocumentSession — book + RegionsBook + OgeIndex + per-page link cache
  view.py          ReaderPage (Adw.ToolbarView shell)
  page_view.py     PageView(Gtk.Widget) — do_snapshot: texture + colour matrix + hotspots
  spread.py        book/single layout; port of calculateScale (viewer.js:680)
  scroll_mode.py   Gtk.ListView virtualization (replaces the IntersectionObserver)
  focus.py         FocusOverlay + crop-render stage
  sidebar.py       Adw.ViewStack: thumbnails | bookmarks | activities
  search.py        background text pass + page.search_for() rects
  activity_dialog.py  Adw.Dialog + WebKit.WebView
library/
  view.py model.py card.py covers.py downloads.py
state/
  settings.py      JSON at $XDG_CONFIG_HOME/interaktiv/state.json
  theme.py         Adw.StyleManager + CssProvider + per-theme colour matrix
style.css          board metrics (ported from css/school.css) + theme colour overrides
icons/             the SVGs currently inlined in index.html
```

### Threading

**One render thread per open document, owning the only `pymupdf.Document`.**
PyMuPDF is not thread-safe and has one global context; a pool gives neither single
ownership nor the coalescing that paging needs. The main loop calls no PyMuPDF API
at all — `page_count` and page rects are captured at open. Library covers stay on
`pdftoppm` subprocesses (already how `books_manager._generate_thumbnail_from_local`
works), so cover generation never enters the MuPDF context.

- **Generations.** `service.generation` bumps on page turn, zoom, rotate, mode
  switch, debounced resize, focus enter/exit. Checked at dequeue *and* before
  `idle_add`. MuPDF has no cooperative abort, so "finish and drop" is the
  cancellation model — affordable at 20–50 ms per render.
- **Coalescing.** `submit()` keys pending work by `(page, scale, rot, clip)`;
  resubmitting a key replaces it. Holding the next-page button never accumulates a
  queue — only the final spread is rendered.
- **Priority lanes** on a `PriorityQueue[(priority, seq)]`: `0` visible/focus,
  `1` neighbour prefetch, `2` thumbnails, `3` search and activity-list scans.
  Lane-3 work is chunked to ≤ 8 pages per queue item so a lane-0 render is picked
  up between chunks.
- **While a render is outstanding** the `PageView` keeps painting the previous
  texture through `append_scaled_texture(..., TRILINEAR, new_bounds)` — instant at
  the new zoom, slightly soft, sharpening on arrival. `Adw.Spinner` only after 150 ms.
- **Memory.** `TOOLS.set_low_memory(True)` at thread start; `jobs.trim_memory()`
  (`store_shrink(100)` + `gc.collect()` + `malloc_trim(0)`) every 16 renders and
  on queue-empty. `TextureCache` is a byte budget (192 MB default, `INTERAKTIV_TEXTURE_BUDGET`),
  cleared on mode change and on leaving the reader. In book mode the live set is
  2 visible + 2 prefetched ≈ 20 MB.
- **Downloads** keep `BooksManager`'s own threads; the UI polls
  `get_download_statuses()` on `GLib.timeout_add(800)` — the cadence `dashboard.js:579` uses.

### Coordinates

`interaktiv_core/geometry.py`, one frozen dataclass; nothing else does rect
arithmetic. With `u = x`, `v = page_h - y` (PDF y-up → MuPDF y-down), `s = scale`:

| rot | device (X, Y) |
|---|---|
| 0 | `(u·s, v·s)` |
| 90 | `((H − v)·s, u·s)` |
| 180 | `((W − u)·s, (H − v)·s)` |
| 270 | `(v·s, (W − u)·s)` |

then `widget = device − (origin_x, origin_y)`, where the origin is `pix.x, pix.y`
(non-zero only for a clipped focus render). Public API: `point_to_widget`,
`rect_to_widget` (transform both corners, min/max — the same normalisation as
`rectToViewport`, `viewer.js:1633`), `point_to_pdf` (exact inverse, used for hit
testing), `rect_to_clip` (`(x0, H−y1, x1, H−y0)`), `pymupdf_matrix`.

**Per-page guard:** assert `abs(page.rect.width − bake.pageWidth) < 1` and the same
for height; on mismatch drop that page's overlay. This is the per-page analogue of
`loadBakedRegions`'s existing `pageCount` guard and covers offset MediaBoxes.

### Rendering

Page canvas is a custom `Gtk.Widget` with `do_snapshot`, not `Gtk.DrawingArea`/Cairo:

```python
def do_snapshot(self, snapshot):
    if self._matrix is not None:                 # theme filter, texture only
        snapshot.push_color_matrix(self._matrix, self._offset)
    snapshot.append_scaled_texture(self._texture, Gsk.ScalingFilter.TRILINEAR, bounds)
    if self._matrix is not None:
        snapshot.pop()
    self._snapshot_overlay(snapshot)             # hotspots, unfiltered
```

Cairo would force a CPU `set_source_surface` + paint of a 5 MB surface every frame,
and the sepia/inverted filters would need a second filtered pixmap in RAM.

**Hotspots are drawn, not widgets** — `append_color` / `append_border` /
`append_outset_shadow` / `append_layout` per part. A page carries dozens of parts;
one widget each means real layout work per turn and a second hit-test that can
diverge from `linking.hit_test`.

**Focus stage** is the same `PageView` in crop configuration:

```python
clip = transform.rect_to_clip(region)
pix  = page.get_pixmap(matrix=Matrix(s, s).prerotate(rot), clip=clip, alpha=False)
# pix.x / pix.y -> PageTransform(origin_x=..., origin_y=...)
```

Scale selection ports `renderFocus` (`viewer.js:1750`) with its clamps:
`s = clamp(min(availW/dev_w, availH/dev_h) * bias, 0.4, 6)` with `bias` clamped
`0.5..3`, then `s = min(s, sqrt(8e6 / (region_w * region_h)))`, then `× scale_factor`
and the 8e6 cap re-applied. This replaces the web's `dpr -= 0.25` back-off, which
has no analogue here.

### The `linkInteractiveOges` port

`tools/hotspot_extraction/scanner/anchors.py` already carries `parse_oge_label`,
`PublisherOge`, `load_publisher_oges`, and the drift constants — but
`find_unplaced_anchors` (`anchors.py:135`) reimplements the pairing loop and
returns only the leftovers. Refactor so both sides share one rule:

1. Move `parse_oge_label`, `LABELLED_DRIFT`/`UNLABELLED_DRIFT`/`WRONG_SIDE_PENALTY`
   into `interaktiv_core/linking.py`; `anchors.py` re-exports them, so `scan.py` and
   `scanner/__init__.py` are untouched.
2. Add normalized views (`RegionView`, `OgeView`) so both an `ActivityRegion`
   (`rect: tuple`) and the reader's `Activity` (`Rect`) can be linked.
3. `link_oges(regions, oges, confidence=None, explain=None) -> dict[region_id, oge_id]`,
   gates in exactly the JS order (`no-position` → `confidence-gated` →
   `label-mismatch` → `drift` → `wrong-side`), then greedy 1-to-1, closest first.
4. `find_unplaced_anchors` shrinks to: build views, call `link_oges`, return the
   unclaimed. Delete `anchors.py:155-195`. Note it currently filters `(0,0)`
   positions *before* pairing while JS filters after — unify on the JS order and
   assert it in a test.
5. `hit_test(page, x, y)` moves here too (smallest-area-wins over
   `act.parts or [act.rect]`) — the baked reader needs it with no detector present.
6. Port the anchored-region override (`viewer.js:1541-1551`): after linking,
   force-link any activity whose id matches `-oge-(.+)$` to that oge. This is what
   stops an anchored activity being drawn both as a region and as a corner pin.

**The confidence gate.** `linkInteractiveOges` gates on
`pageData.confidence` ("none" → link only anchored regions; "weak" → require a
label for non-anchored). But `bakedPage()` (`viewer.js:1285`) never copies
`confidence` onto the page object, so **the gate is inert for every baked book
today** — School Edition runs ungated. The GTK reader *has* the value
(`RegionsBook.confidence`), so passing it can only reduce links. Implement it
behind `Settings.link_confidence_gate`, default off, and add a test that replays
every installed book's `regions.json` × `activities_meta/*.json` reporting links
gated vs ungated. Decide from the numbers, not from the port.

### What `server.py` becomes

The GTK app opens no socket. `server.py` and the web UI stay as-is for the full
edition. Endpoint by endpoint:

- **Die outright:** `/api/config`, `/api/status`, `/api/heartbeat`, `/api/pagehide`,
  `/api/shutdown`, `/api/perf_metrics`, `/api/client_perf`, `/api/perf_reset` —
  they exist only to detect a closed browser window. A GTK window closing *is* the signal.
- **Become direct `BooksManager` calls:** `/api/books`, `/api/books/status|install|cancel|uninstall`, `/api/thumbnail`, `/api/activities/meta`.
- **Lift into `interaktiv_core/jobs.py`:** the JIT-bake trigger (`server.py:60`,
  including its `active_bakes` dedup set and the `scan.py --only … --quiet` subprocess)
  and `resolve_activity_asset` (`server.py:690`) with its `realpath` confinement check.
- **`/api/activities/fetch` does not exist** — `viewer.js:1441` calls a route
  `server.py` never defined, which is why JIT activity caching is dead in the web
  School Edition. Implement it properly as `jobs.fetch_activity`, a worker-thread
  wrapper over `tools.content_extraction.extract_activities.extract_activity(...)`.
- **`/api/stream` has no native equivalent.** PyMuPDF cannot read a URL and its
  `stream=` argument takes complete bytes, so a local range proxy buys nothing and a
  partially-ranged file is not a parseable PDF. **Preview becomes a cancellable
  cached download:** add `BooksManager.download_to(book_id, target)` by factoring
  `_download_worker`'s body (the only change to `books_manager.py`), write to
  `$XDG_CACHE_HOME/interaktiv/previews/<id>.pdf`, show the existing progress bar +
  cancel with an ETA, LRU-evict to ≤ 2 files / ≤ 1 GB, and make "Install" an
  `os.replace` of the cached preview rather than a second download. Library covers
  for uninstalled books come from the shipped `thumbnails/` directory (all 56
  present — `tests/test_school_edition.py` asserts it), so `dashboard.js`'s
  "render page 1 over `/api/stream` with pdf.js" path disappears.

  *This is a real UX change — instant preview becomes a 63–443 MB wait — and is the
  one item in this plan that changes what a teacher experiences. Flagged for sign-off
  before M1 ships.*

### Widget mapping

| Web | GTK4 / Adw |
|---|---|
| `header.toolbar` 64 px | `Adw.ToolbarView` + `Adw.HeaderBar` + a second `Gtk.Box` row |
| `.tool-btn` 48 px | `Gtk.Button.tool-btn`, icons via `Gtk.IconTheme.add_search_path` |
| `#btn-prev/next-page` 76×54, hold-repeat | `Gtk.Button` + `Gtk.GestureLongPress` + `GLib.timeout_add` — **`bindHoldRepeat` (`viewer.js:2496`) has no GTK equivalent and must be reimplemented** (350 ms delay, 110 ms interval, swallow trailing click) |
| `#page-num-input` | `Gtk.SpinButton` |
| `#zoom-select` | `Gtk.DropDown` + `Gtk.StringList` |
| book/single/scroll radio | `Adw.ToggleGroup` + 3 `Adw.Toggle` |
| `#search-bar` | `Gtk.SearchBar` + `Gtk.SearchEntry`, `set_key_capture_widget` for Ctrl+F |
| `aside#sidebar` 320 px | `Adw.OverlaySplitView` (collapsible on narrow boards) |
| sidebar tabs | `Adw.InlineViewSwitcher` + `Adw.ViewStack` |
| `#thumbnails-list` | `Gtk.GridView` + `SignalListItemFactory` (recycling *is* the lazy loader) |
| `#outline-list` | `Gtk.ListView` over `Gtk.TreeListModel` from `doc.get_toc()` |
| `#viewer-pages` scroll mode | `Gtk.ListView` of `PageView`s; `unbind` drops the texture (`unmountPageCanvas` equivalent) |
| `.activity-hotspot` / `.activity-chip` / `.standalone-hotspot` | drawn in `PageView.do_snapshot` |
| `#focus-overlay` | `Gtk.Overlay` child + `Gtk.CenterBox` chrome |
| `dialog#activity-modal` + iframe | `Adw.Dialog` (FLOATING) + `WebKit.WebView`. **`sandbox="allow-scripts allow-same-origin allow-forms"` has no per-frame equivalent** — mitigate with an ephemeral `WebKit.NetworkSession` and `set_allow_file_access_from_file_urls(False)` |
| `.activity-offline-note` | `Adw.Banner` in the dialog |
| `dialog#help-modal` | `Adw.ShortcutsDialog` |
| `.statusbar` | `Gtk.ActionBar` via `add_bottom_bar` |
| `.filter-tab` × 7 | `Adw.ToggleGroup`; badge is a `Gtk.Label.tab-badge` |
| `.books-grid` `minmax(260px,1fr)` | `Gtk.GridView` — **no `auto-fill minmax`**; set `max_columns = floor(width / 280)` in a `notify::width` handler |
| `confirm()` uninstall | `Adw.AlertDialog` |
| silent `console.error` | `Adw.ToastOverlay` |
| `.textLayer` selection | **no equivalent** — see risks |

### Theming

Same four themes, same cycle order (`dark → light → sepia → inverted`, `M`,
persisted — `viewer.js:239`).

Chrome: dark/inverted → `Adw.StyleManager.FORCE_DARK`, light/sepia → `FORCE_LIGHT`;
sepia and inverted additionally set a `.theme-sepia` / `.theme-inverted` class on
the window, and `style.css` redefines libadwaita's named colours
(`@define-color window_bg_color` etc.) under them, alongside the School metrics
ported from `css/school.css`.

The canvas filters (`sepia(0.2) contrast(0.95)`; `invert(0.9) hue-rotate(180deg)
brightness(1.05) contrast(0.95)` — `css/viewer.css:523/658/1060`) are **not** baked
into the pixmap: that would be a CPU pass per page and would invalidate the whole
texture cache on every theme switch. Each CSS filter primitive is a 4×4 colour
matrix plus offset; multiply the chain once at startup (~20 lines of pure Python,
no numpy) into one `(Graphene.Matrix, Graphene.Vec4)` per theme and wrap **only the
texture node** in `push_color_matrix`. Hotspots, chips and highlights stay
untinted — matching the web, where the filter sits on `canvas` and not on
`.activityLayer`. A theme switch is then a `queue_draw()`: no re-render, no cache
invalidation.

---

## Milestones

Each is independently runnable via `python3 -m interaktiv_gtk` and independently verifiable.

**M0 — shared core, no UI.** `interaktiv_core/*`; refactor `anchors.py` onto
`linking.py`.
*Verify:* `scanner/test_*.py` pass; re-bake two books with `scan.py --force` and
`diff` the `regions.json` against the current ones (must be byte-identical); new
`tests/test_linking.py` reproduces the `test_interactive_links.mjs` fixtures;
`tests/test_geometry.py` round-trips rects through all four rotations.

**M1 — shell + library.** `app/window/library/*`, covers from `thumbnails/`,
`Adw.ToggleGroup` filters, search, install/cancel/uninstall, 800 ms poll, preview
download.
*Verify:* 56 books listed; grade filters match `dashboard.js` semantics
(`all/installed/9/10/11/12/0`); one book installs end-to-end with progress and cancel.

**M2 — reader skeleton.** `RenderService` + `TextureCache` + `PageView` +
`SpreadView`; book/single modes, page nav with hold-repeat, zoom modes, rotate,
status bar, per-book last page.
*Verify:* open `books/1cc573f6-….pdf` (443 MB), page through 200 pages fast; RSS
bounded (target < 700 MB steady); no main-loop stall > 16 ms.

**M3 — regions overlay.** `RegionsBook` + `OgeIndex` + `link_oges` + hotspot
drawing + hover/click + standalone pins + activities sidebar list.
*Verify:* hotspot count, labels and per-part rects for two books match
`dumpActivities()` (`viewer.js:2066`) output from the web reader.

**M4 — focus mode.** Crop render, next/prev activity across pages
(`stepActivityFrom`), sub-item stepping, zoom bias, exit restoring pre-focus state.
*Verify:* peak focus texture never exceeds 8e6 px; a part is always zoomed alone,
never unioned with its siblings.

**M5 — activity dialog.** `apt install gir1.2-webkit-6.0`; `Adw.Dialog` + WebView,
local-first then CDN, `jobs.fetch_activity` background caching, 7 s offline banner,
fullscreen, open-in-browser.
*Verify:* an uninstalled activity opens from the CDN and is on disk in
`activities_dir` afterwards.

**M6 — scroll mode + sidebar.** `Gtk.ListView` virtualization, page thumbnails with
spread grouping (`initBookThumbnails`, `viewer.js:2183`), bookmarks from `get_toc()`.
*Verify:* scroll a 289-page book end to end; texture cache stays at budget.

**M7 — search + shortcuts.** Background text pass (0.44 s / 165 pp),
`page.search_for()` rect highlights, `Adw.ShortcutsDialog`, full keyboard parity
with `viewer.js`'s bindings.

**M8 — theming, persistence, packaging.** Light/dark theme + colour matrices, `Settings`
JSON, board CSS, `.desktop` file + icon, `tools/build_school_gtk.sh` alongside
`tools/build_school.sh` (shipping `interaktiv_core/`, `interaktiv_gtk/`,
`books_manager.py`, catalogue, thumbnails, `activities/books/` — and **not**
`index.html`, `css/`, `js/`, `server.py`, `main.py`), PyGObject/WebKit notes in
`requirements.txt`.

---

## Risks

1. **PyMuPDF thread-safety.** One global MuPDF context. → Exactly one thread ever
   touches a `Document`; the main loop calls no PyMuPDF API; covers go through
   `pdftoppm` subprocesses.
2. **MuPDF store growth — `store_maxsize()` is a stub in 1.28** (verified: returns
   `None`). → `set_low_memory(True)` plus `trim_memory()` every 16 renders and on
   queue-empty, plus a `psutil` RSS watchdog (already a dependency) that force-trims
   and clears the texture cache above a ceiling. **No batch job may sweep the whole
   catalogue in one process** — bakes stay in short-lived `scan.py` children at the
   default heap.
3. **GPU texture memory on weak smartboard GPUs.** → Byte-budgeted LRU, full clear
   on mode change and on leaving the reader.
4. **Preview becomes a 63–443 MB wait.** → Cancel + LRU cache + ETA; install
   promotes the cached file. Needs product sign-off before M1 ships.
5. **WebKitGTK is missing right now.** → `gi.require_version('WebKit','6.0')` inside
   a try at import; on failure fall back to `Gtk.UriLauncher` and hide the embed
   button — the same shape as today's `features.jit_activities` flag.
6. **The confidence gate changes link counts.** → Behind `Settings.link_confidence_gate`,
   default off, with a gated-vs-ungated report test per book.
7. **No text selection in GTK.** The web's `.textLayer` gives selection and copy. →
   For School Edition, drop selection and keep search highlighting only (teachers
   project; they don't copy). If it is needed later, implement over
   `page.get_text("words")` rects + a drag gesture + `Gdk.Clipboard` as its own
   milestone — don't fake it.
8. **A slow image-dense page stalls a turn.** Typical render is 20–50 ms but the
   443 MB book will have worse pages. → Neighbour prefetch, stale-texture scaled
   painting, spinner after 150 ms, generation-drop. Measure the worst decile per
   book during M2; if needed add a scale-0.5 proxy render in lane 1.
9. **HiDPI / board scale factors.** → Textures keyed by `get_scale_factor()`;
   re-render on `notify::scale-factor`; the 8e6 cap applied *after* the multiply.
10. **A bake that doesn't match the file.** → `RegionsBook.matches(page_count)` at
    open (drop the whole overlay), the per-page ±1 pt dimension assertion (drop that
    page's overlay), and `jobs.trigger_bake` when `fingerprint` disagrees with
    `compute_fingerprint(pdf_path)` — the freshness rule `scan.py:186` already uses.
11. **`regions.json` is ~100–600 KB, parsed per open.** → Parse once per
    `DocumentSession`, materialize `PageRegions` on demand behind a 16-page LRU,
    drop on close. The `pages` map is sparse (93 of 289 pages in the sampled book).

---

## Verification

Run throughout, not just at the end:

```bash
python3 -m pytest tests/ tools/hotspot_extraction/scanner/ -q
```

```bash
python3 tools/hotspot_extraction/scan.py --only 03eb95bb-3a93-4f99-a2df-36d02f9c40d9 --force --out /tmp/bakecheck && diff <(python3 -m json.tool /tmp/bakecheck/03eb95bb-3a93-4f99-a2df-36d02f9c40d9/regions.json) <(python3 -m json.tool activities/books/03eb95bb-3a93-4f99-a2df-36d02f9c40d9/regions.json)
```

```bash
python3 -m interaktiv_gtk --edition school
```

End-to-end pass on a real book, with `books/1cc573f6-…pdf` (443 MB, the worst case)
as the memory target:

1. Library lists 56 books; grade tabs and search filter as `dashboard.js` does.
2. Install one book; progress advances, cancel works, thumbnail appears.
3. Open it; page through 200 pages holding the next button — RSS stays bounded and
   the UI never freezes (watch with `psutil` or `ps -o rss`).
4. Hotspots appear on baked pages with the right labels and per-part rects; compare
   against the web reader's `dumpActivities()` for the same pages.
5. Click a hotspot → focus zooms that part alone; `←/→` crosses pages; `↑/↓` steps
   questions; `Esc` restores the previous view exactly.
6. Click a `⚡` hotspot → the activity opens in the WebKit dialog and is cached on
   disk afterwards.
7. Cycle all four themes — page tint changes with no re-render and no memory growth.
8. Ctrl+F finds and highlights; T, B/S/C, +/-/0, R, F, ? all behave as the web does.

The existing `tests/test_school_edition.py` and `tests/test_library_mode.py` keep
passing untouched — they exercise `BooksManager` and `server.py`, neither of which
this plan changes beyond the additive `download_to`.
