/**
 * Precompute a book's activity regions once, on the server, instead of every
 * time someone opens it.
 *
 * `js/activities.js` stays the only implementation of detection -- this runs it
 * headlessly rather than reimplementing it -- but running it over the whole book
 * in one pass buys two things the browser cannot have:
 *
 *   - the folio map becomes deterministic. `printedPageFor()` is the join key to
 *     the publisher's manifest, and it is voted from whichever sheets have been
 *     looked at; in a reader that is whichever pages the user happened to visit,
 *     so the same activity can link on one session and not the next. Here every
 *     sheet votes, every time.
 *   - a book can be audited, or regressed against, without opening it.
 *
 * A book is read twice: once for text alone, which settles the label style and
 * the folio map, and once for the regions themselves. That split is what lets a
 * book be baked a page range at a time (`--chunked`) when one sheet's images
 * cost more than the rest of the book put together -- the ranges share the one
 * calibration and the one folio map, so a chunked bake and a whole-book bake
 * produce the same file.
 *
 * Usage:
 *   node tools/hotspot_extraction/bake_activities.mjs books/<id>.pdf [...]   bake the named books
 *   node tools/hotspot_extraction/bake_activities.mjs --all                  bake every installed book
 *   node tools/hotspot_extraction/bake_activities.mjs --all --force          re-bake even if current
 *   node tools/hotspot_extraction/bake_activities.mjs books/<id>.pdf --chunked [--chunk-pages 64]
 *                                                            bake one book a page range at a time
 *
 * `tools/hotspot_extraction/fetch_and_bake.py` is the catalogue driver on top of
 * this: it fetches each book, bakes it here, and falls back to `--chunked` for
 * any book a single pass cannot read.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import zlib from "node:zlib";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  root,
  pdfjsLib,
  ActivityDetector,
  openDocument,
  bookIdForFile,
  interactiveOges,
  unplacedAnchors,
} from "./tests/_harness.mjs";
import { MemoryWatchdog } from "./tests/_watchdog.mjs";

/** Bumped whenever the shape of regions.json or the detector's output changes. */
export const BAKE_VERSION = 2;

/**
 * Bumped whenever the shape of diagnostics.json.gz changes.
 *
 * Kept apart from BAKE_VERSION because the sidecar is scoring evidence, not
 * something the reader loads: a change here must not invalidate a book's regions.
 */
export const DIAGNOSTICS_VERSION = 1;

const OUT_DIR = path.join(root, "activities", "books");

/**
 * What identifies the bytes this bake was made from.
 *
 * Size plus a hash of the head, middle and tail: a full hash of a 140 MB PDF
 * costs about a second per book and buys nothing, since a book whose first,
 * middle and last 256 KB all match is the same download.
 */
function fingerprint(file) {
  const size = fs.statSync(file).size;
  const span = 256 * 1024;
  const fd = fs.openSync(file, "r");
  const hash = crypto.createHash("sha256");
  try {
    for (const at of [0, Math.max(0, (size >> 1) - (span >> 1)), Math.max(0, size - span)]) {
      const buf = Buffer.alloc(Math.min(span, size));
      const read = fs.readSync(fd, buf, 0, buf.length, at);
      hash.update(buf.subarray(0, read));
    }
  } finally {
    fs.closeSync(fd);
  }
  return `${size}-${hash.digest("hex").slice(0, 32)}`;
}

/**
 * A region as the viewer needs it, without the fields `analyzePage` only used
 * to build it.
 *
 * `parts` is carried whole and is never collapsed into `rect`: an activity that
 * continues in the next column stays one rect per piece, so hit-testing and
 * zooming land on the piece that was pointed at rather than on a union spanning
 * the gutter. `rect` is only the label-bearing piece, as it is in a live run.
 */
function rectArray(r) {
  return [r.x0, r.y0, r.x1, r.y1];
}

/**
 * The drawn geometry a violation check needs, and nothing else.
 *
 * Scoring the catalogue meant re-parsing every PDF, which is the reason a change
 * to the join rule cost hours instead of seconds. The rules the scorecard
 * applies -- did this region cut a panel, cut an answer grid, leave a marker
 * outside itself -- read only three lists of rectangles per sheet, so those are
 * written beside the regions and the PDF is not needed again. Anything that
 * carries text is dropped: it is not used, and a sidecar carrying the book's
 * words would be a copy of the book.
 *
 * Coordinates are written at full precision. Rounding them to a tenth of a point
 * halves the file and looks harmless -- the checks work in whole points with a
 * 5% tolerance -- but it was measured: on df1e313c it moved the violation count
 * from 588 to 569, because `cuts()` compares an area share against a threshold
 * and a tenth of a point either side of a block edge flips the blocks sitting
 * near it. A sidecar that does not reproduce the live score exactly is not worth
 * having, and the whole catalogue is a few MB either way.
 */
const r1 = (n) => n;
function diagRects(list) {
  return (list || []).map((r) => [r1(r.x0), r1(r.y0), r1(r.x1), r1(r.y1)]);
}

function serializeActivity(act) {
  const out = {
    id: act.id,
    label: act.label ?? null,
    column: act.column,
    rect: rectArray(act.rect),
    parts: (act.parts && act.parts.length ? act.parts : [act.rect]).map(rectArray),
  };
  if (act.headline) out.headline = act.headline;
  if (act.anchored) out.anchored = true;
  if (act.items && act.items.length) {
    out.items = act.items.map((q) => ({
      id: q.id,
      label: q.label ?? null,
      number: q.number ?? null,
      partIndex: q.partIndex ?? 0,
      rect: rectArray(q.rect),
      ...(q.text ? { text: q.text } : {}),
    }));
  }
  return out;
}

/**
 * Let pdf.js drop what it parsed for one page.
 *
 * A document keeps every page proxy it has handed out and each proxy holds that
 * sheet's fonts and operator list, so a sweep that never cleans up carries the
 * whole book whatever the detector releases.
 */
async function releasePdfPage(doc, pageNum) {
  try {
    (await doc.getPage(pageNum)).cleanup();
  } catch (_) {}  // nothing parsed for this sheet, nothing to release
}

/** What `calibrate()` learned, in the shape the bake writes and a child reads back. */
function calibrationOf(det) {
  return {
    enabled: !!det.enabled,
    confidence: det.confidence,
    markerSize: det.markerSize,
    markerStyles: Array.from(det.markerStyles || []),
    markerFonts: Array.from(det.markerFonts || []),
  };
}

/**
 * Hand a detector what another process already settled.
 *
 * A book baked in page ranges is read by several children, and each of them
 * must work from the *same* calibration and the same folio map as the others:
 * a child that learns the label style from its own 60 sheets can disagree with
 * its neighbour, and a child that votes the folio from a range that prints none
 * files its regions under the wrong printed page. So both are settled once, in
 * the light first pass, and seeded here. `calibrated` is set so `calibrate()`
 * returns without sampling the book again.
 */
function seedDetector(det, seed) {
  const cal = seed.calibration || {};
  det.markerStyles = new Set(cal.markerStyles || []);
  det.markerFonts = new Set(cal.markerFonts || []);
  det.markerSize = cal.markerSize ?? null;
  det.enabled = !!cal.enabled;
  det.confidence = cal.confidence || "none";
  det.calibrated = true;
  det.folioOffset = seed.folio.offset;
  // Only the folios actually read off a sheet are seeded. The rest are answered
  // by the offset, exactly as they are in a single-process bake.
  for (const [p, f] of Object.entries(seed.folio.read || {})) det.folioByPage.set(Number(p), f);
}

/** Is the bake on disk already the one these bytes would produce? */
function bakeIsCurrent(outPath, print) {
  if (!fs.existsSync(outPath)) return false;
  // A book with no sidecar cannot be scored from its bake, so it is not done
  // however current its regions look: `scorecard --from-bake` refuses it.
  const diagPath = path.join(path.dirname(outPath), "diagnostics.json.gz");
  if (!fs.existsSync(diagPath)) return false;
  try {
    const prev = JSON.parse(fs.readFileSync(outPath, "utf8"));
    if (prev.version !== BAKE_VERSION || prev.fingerprint !== print) return false;
    const diag = JSON.parse(zlib.gunzipSync(fs.readFileSync(diagPath)).toString("utf8"));
    return diag.version === DIAGNOSTICS_VERSION && diag.fingerprint === print;
  } catch (_) {
    return false;   // unreadable bake: rebuild it
  }
}

/**
 * Pass one: learn the labels and settle the folio map, reading text only.
 *
 * printedPageFor() answers from a modal vote, so a region analysed while the
 * vote is still one page deep can be filed under the wrong printed page. Every
 * sheet votes here before any region is read off one. textItems() notes the
 * folio itself; the vote is kept and the sheet's text is dropped again straight
 * away, because holding a whole book's text items is by itself more memory than
 * this pass can afford.
 *
 * Nothing here decodes an image, which is what makes it safe to run over a book
 * whose region pass has to be split into ranges: the sheet that cannot be
 * afforded is always one carrying a picture.
 */
export async function folioPass(file, { watchdog = new MemoryWatchdog() } = {}) {
  const bookId = bookIdForFile(file);
  if (!bookId) throw new Error(`${file} is not named after a catalogue book id`);
  const doc = await openDocument(file);
  const det = new ActivityDetector(doc, path.basename(file));
  // No localStorage in Node, and a stale calibration would be baked in anyway:
  // every bake calibrates from the book in front of it.
  det.calibrated = false;
  await det.calibrate();

  for (let p = 1; p <= doc.numPages; p++) {
    await det.textItems(p);
    det.releasePage(p);
    await releasePdfPage(doc, p);
    watchdog.check(`folio pass, page ${p}`);
  }

  const read = {};
  for (const [p, f] of det.folioByPage) read[p] = f;
  const byPage = {};
  for (let p = 1; p <= doc.numPages; p++) {
    const printed = det.printedPageFor(p);
    if (printed !== null) byPage[p] = printed;
  }

  const seed = {
    version: BAKE_VERSION,
    bookId,
    fingerprint: fingerprint(file),
    pageCount: doc.numPages,
    calibration: calibrationOf(det),
    folio: { offset: det.folioOffset, read, byPage },
    peakRssMb: watchdog.peakMb,
  };
  await doc.destroy();
  return seed;
}

/**
 * Pass two, over one range of sheets: the regions themselves.
 *
 * Whole-book and chunked bakes run the same loop; a chunk is only a narrower
 * `from`/`to`. What it returns is a shard — the pages it read and nothing about
 * the ones it did not — so shards merge by assignment.
 */
export async function regionPass(file, seed, {
  from = 1, to = seed.pageCount, watchdog = new MemoryWatchdog(),
} = {}) {
  const doc = await openDocument(file);
  const det = new ActivityDetector(doc, path.basename(file));
  det.diagnostics = new Map();   // page geometry, for the scorecard's sidecar
  seedDetector(det, seed);

  const oges = interactiveOges(seed.bookId);
  const byPrinted = new Map();
  for (const o of oges) {
    if (!byPrinted.has(o.sayfano)) byPrinted.set(o.sayfano, []);
    byPrinted.get(o.sayfano).push(o);
  }
  det.anchorsByPage = new Map();

  const pages = {};
  const diagnostics = {};
  const anchors = {};
  let unplaced = 0;
  let regions = 0;
  let questions = 0;
  let anchored = 0;
  for (let p = from; p <= to; p++) {
    let data = await det.analyzePage(p, pdfjsLib);
    const printed = det.printedPageFor(p);

    // Which of the publisher's entries no label on this sheet accounts for.
    // The answer depends on what the labelled pass matched, so it is read off
    // that pass and the sheet is then analysed again with the leftovers as
    // anchors -- an anchor can never change which letters were accepted. This
    // used to be a second walk of the whole book; done per sheet it holds one
    // page's structure at a time instead of the book's.
    const list = (printed !== null && byPrinted.get(printed)) || [];
    if (list.length) {
      const left = unplacedAnchors(data, list);
      if (left.length) {
        unplaced += left.length;
        det.anchorsByPage.set(p, left.map((o) => ({ id: o.id, posx: o.posx, posy: o.posy })));
        anchors[p] = left.map((o) => String(o.id));
        det.releasePage(p);
        data = await det.analyzePage(p, pdfjsLib);
      }
    }

    const diag = det.diagnostics.get(p);
    if (diag) {
      diagnostics[p] = {
        pageWidth: r1(data.pageWidth),
        pageHeight: r1(data.pageHeight),
        contentTop: diag.contentTop == null ? null : r1(diag.contentTop),
        contentBottom: diag.contentBottom == null ? null : r1(diag.contentBottom),
        panels: diagRects(diag.panels),
        solutions: diagRects(diag.solutions),
        markers: diagRects(diag.markers),
      };
    }

    if (data && data.activities && data.activities.length) {
      regions += data.activities.length;
      anchored += data.activities.filter((a) => a.id.startsWith(`p${p}-oge-`)).length;
      questions += data.activities.reduce((n, a) => n + (a.items ? a.items.length : 0), 0);
      pages[p] = {
        pageWidth: data.pageWidth,
        pageHeight: data.pageHeight,
        columns: (data.columns || []).map((c) => [c.x0, c.x1]),
        activities: data.activities.map(serializeActivity),
      };
    }
    det.releasePage(p);
    await releasePdfPage(doc, p);
    watchdog.check(`page ${p}`);
  }

  // A document that is finished with has to be let go, or a run that bakes book
  // after book in one process carries every book it has already done.
  await doc.destroy();
  return {
    from, to, pages, diagnostics, anchors,
    regions, questions, anchored, unplaced, peakRssMb: watchdog.peakMb,
  };
}

/**
 * Write the book's bake and its sidecar from the shards that produced it.
 *
 * One shard or twenty makes no difference to what is written: a chunked bake is
 * byte-comparable with a whole-book one, which is the only reason splitting a
 * book across processes is allowed to be a memory decision rather than a
 * detection one.
 */
function writeBake(seed, shards, { log = () => {} } = {}) {
  const pages = {};
  const diagnostics = {};
  const anchors = {};
  let regions = 0, questions = 0, anchored = 0, unplaced = 0, peakRssMb = 0;
  for (const s of shards) {
    Object.assign(pages, s.pages);
    Object.assign(diagnostics, s.diagnostics);
    Object.assign(anchors, s.anchors);
    regions += s.regions;
    questions += s.questions;
    anchored += s.anchored;
    unplaced += s.unplaced;
    peakRssMb = Math.max(peakRssMb, s.peakRssMb || 0);
  }

  const baked = {
    version: BAKE_VERSION,
    bookId: seed.bookId,
    fingerprint: seed.fingerprint,
    builtAt: new Date().toISOString(),
    pageCount: seed.pageCount,
    calibration: seed.calibration,
    folio: { offset: seed.folio.offset, byPage: seed.folio.byPage },
    // Which manifest entries were handed to the detector as anchors, per sheet.
    // Scoring a baked book has to tell an entry that was offered to the growth
    // pass and produced nothing from one that was never offered at all, and
    // that difference cannot be recovered from the regions alone.
    anchors,
    pages,
  };

  const outPath = path.join(OUT_DIR, seed.bookId, "regions.json");
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, JSON.stringify(baked));

  // Gzipped because it is three quarters redundant digits and is read once per
  // scoring run, not per page view.
  fs.writeFileSync(
    path.join(path.dirname(outPath), "diagnostics.json.gz"),
    zlib.gzipSync(
      JSON.stringify({
        version: DIAGNOSTICS_VERSION,
        bookId: seed.bookId,
        fingerprint: seed.fingerprint,
        pages: diagnostics,
      })
    )
  );

  const cal = seed.calibration;
  log(
    `${seed.bookId}: ${seed.pageCount} pages, ${regions} regions (${anchored} anchored` +
    `${unplaced ? ` of ${unplaced} unplaced` : ""}), ${questions} questions` +
    `, folio offset ${seed.folio.offset}${cal.enabled ? "" : ", NOT calibrated"}` +
    `${shards.length > 1 ? `, ${shards.length} page ranges` : ""}` +
    `, peak ${peakRssMb} MB`
  );
  return {
    bookId: seed.bookId, skipped: false, path: outPath,
    regions, questions, anchored, unplaced, chunks: shards.length, peakRssMb,
  };
}

/** Bake one book in this process: the folio pass, then every page. */
export async function bakeBook(file, { force = false, log = () => {} } = {}) {
  const bookId = bookIdForFile(file);
  if (!bookId) throw new Error(`${file} is not named after a catalogue book id`);

  const outPath = path.join(OUT_DIR, bookId, "regions.json");
  const print = fingerprint(file);
  if (!force && bakeIsCurrent(outPath, print)) {
    log(`${bookId}: current, skipped`);
    return { bookId, skipped: true, path: outPath };
  }

  const watchdog = new MemoryWatchdog();
  const seed = await folioPass(file, { watchdog });
  const shard = await regionPass(file, seed, { watchdog });
  return writeBake(seed, [shard], { log });
}

/**
 * Bake one book a page range at a time, each range in a process of its own.
 *
 * Some sheets cannot be afforded twice. Sheet 164 of `1cc573f6` embeds a
 * 318x208 pt illustration stored at 15071x9830 px, which pdf.js decodes to
 * roughly 590 MB of RGBA that nothing gives back -- `page.cleanup()`, a forced
 * `global.gc()` and `doc.cleanup()` were all measured and none of them return
 * it -- so a single-process bake of that book crosses the watchdog's limit two
 * thirds of the way through and dies, having done the work of 230 sheets.
 *
 * Splitting the book is the sanctioned answer: the standing rule is one short
 * lived child per book *or per page range*. A range that ends returns its
 * memory to the system outright, so the cost of the worst sheet is paid by one
 * chunk instead of being carried to the end of the book.
 */
export function bakeBookChunked(file, {
  force = false, chunkPages = CHUNK_PAGES, log = () => {},
} = {}) {
  const bookId = bookIdForFile(file);
  if (!bookId) throw new Error(`${file} is not named after a catalogue book id`);

  const outPath = path.join(OUT_DIR, bookId, "regions.json");
  const print = fingerprint(file);
  if (!force && bakeIsCurrent(outPath, print)) {
    log(`${bookId}: current, skipped`);
    return { bookId, skipped: true, path: outPath };
  }

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `bake-${bookId.slice(0, 8)}-`));
  try {
    const seedPath = path.join(dir, "seed.json");
    if (!runChild([file, "--folio-only", "--out", seedPath])) {
      throw new Error("folio pass failed");
    }
    const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));

    const shards = [];
    for (let from = 1; from <= seed.pageCount; from += chunkPages) {
      const to = Math.min(seed.pageCount, from + chunkPages - 1);
      const shardPath = path.join(dir, `shard-${from}.json`);
      const args = [file, "--shard", "--from", String(from), "--to", String(to),
                    "--seed", seedPath, "--out", shardPath];
      if (!runChild(args)) throw new Error(`pages ${from}-${to} failed`);
      shards.push(JSON.parse(fs.readFileSync(shardPath, "utf8")));
      // Read and dropped as it lands: the parent holds the book's regions, and
      // there is no reason for it to hold them twice.
      fs.rmSync(shardPath, { force: true });
    }
    return writeBake(seed, shards, { log });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

/** Every installed PDF named after a catalogue id, symlinks deduped. */
export function installedBooks() {
  const dir = path.join(root, "books");
  if (!fs.existsSync(dir)) return [];
  const seen = new Set();
  const out = [];
  for (const name of fs.readdirSync(dir).sort()) {
    if (!name.toLowerCase().endsWith(".pdf")) continue;
    const file = path.join(dir, name);
    let real;
    try {
      real = fs.realpathSync(file);
    } catch (_) {
      continue;
    }
    if (seen.has(real)) continue;
    seen.add(real);
    if (bookIdForFile(file)) out.push(file);
  }
  return out;
}

/** How many sheets one child of a chunked bake reads. */
const CHUNK_PAGES = 64;

/**
 * Run this same script again for one piece of the work.
 *
 * The heap cap is a fuse, not a budget: a bake fits in well under it, and a
 * child that runs away kills itself instead of taking the desktop into swap.
 * The watchdog inside the child is the earlier fuse, on resident memory, which
 * is what actually grows here -- pdf.js holds decoded images outside the heap.
 */
function runChild(args) {
  const run = spawnSync(
    process.execPath,
    ["--max-old-space-size=1536", fileURLToPath(import.meta.url), ...args],
    { stdio: "inherit" }
  );
  return run.status === 0;
}

function bakeInChild(file, force) {
  const args = [file, "--in-process"];
  if (force) args.push("--force");
  return runChild(args);
}

async function main() {
  const args = process.argv.slice(2);
  const flag = (name) => args.includes(name);
  const value = (name) => {
    const i = args.indexOf(name);
    return i >= 0 ? args[i + 1] : null;
  };
  const force = flag("--force");
  const files = flag("--all") ? installedBooks() : args.filter((a) => !a.startsWith("--") && a.endsWith(".pdf"));

  // The two halves of a chunked bake, each run by `bakeBookChunked` in a child
  // of its own. Neither writes a bake; they write what the parent merges.
  if (flag("--folio-only") || flag("--shard")) {
    const file = files[0];
    const out = value("--out");
    if (!file || !out) {
      console.error("usage: --folio-only --out <seed.json> | --shard --from N --to M --seed <seed.json> --out <shard.json>");
      process.exit(2);
    }
    if (flag("--folio-only")) {
      fs.writeFileSync(out, JSON.stringify(await folioPass(file)));
    } else {
      const seed = JSON.parse(fs.readFileSync(value("--seed"), "utf8"));
      const from = Number(value("--from") || 1);
      const to = Number(value("--to") || seed.pageCount);
      fs.writeFileSync(out, JSON.stringify(await regionPass(file, seed, { from, to })));
    }
    return;
  }

  const chunked = flag("--chunked");
  const chunkPages = Number(value("--chunk-pages") || CHUNK_PAGES);
  // One file named on the command line is baked here; `--all` gives each book a
  // child of its own, because ten books in one process end up resident
  // together. A chunked bake is always its own parent: it spawns per range.
  const inProcess = !chunked && (flag("--in-process") || (!flag("--all") && files.length === 1));

  if (!files.length) {
    console.error(
      "usage: node tools/hotspot_extraction/bake_activities.mjs <book.pdf>... | --all\n" +
      "       [--force] [--chunked [--chunk-pages N]]"
    );
    process.exit(2);
  }

  let failed = 0;
  for (const file of files) {
    try {
      if (chunked) {
        bakeBookChunked(file, { force, chunkPages, log: (m) => console.log(m) });
      } else if (inProcess) {
        await bakeBook(file, { force, log: (m) => console.log(m) });
      } else if (!bakeInChild(file, force)) {
        // A book that cannot be read in one pass is not a failed book: it is a
        // book with a sheet too expensive to carry to the end. Split it.
        console.log(`${path.basename(file)}: single-pass bake failed, retrying in page ranges`);
        bakeBookChunked(file, { force, chunkPages, log: (m) => console.log(m) });
      }
    } catch (e) {
      failed++;
      console.error(`${path.basename(file)}: ${e.message}`);
    }
  }
  if (failed) process.exit(1);
}

if (import.meta.url === `file://${process.argv[1]}` || process.argv[1]?.endsWith("bake_activities.mjs")) {
  await main();
}
