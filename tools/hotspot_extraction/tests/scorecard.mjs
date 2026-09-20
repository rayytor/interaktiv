/**
 * How good are the activity hotspots, book by book?
 *
 * `detect.mjs` dumps what was found so two runs can be diffed; this asks whether
 * what was found is any good, across every book at once, so a change to the
 * algorithm can be judged on the catalogue rather than on the page that
 * prompted it.
 *
 * Three kinds of number come out:
 *
 *   - **yield**, what the detector found at all: regions per page, questions per
 *     region, how much of the book it saw nothing on. A book that quietly finds
 *     nothing looks exactly like a book with nothing to find, and only the
 *     calibration evidence beside it tells the two apart;
 *   - **join**, how many of the publisher's interactive activities were matched
 *     to a region, and how far the region's top sits from the icon the
 *     publisher hung beside it. The manifest is the one piece of outside truth
 *     there is, and it validates the top edge;
 *   - **violations**, rules that are bugs by definition rather than by taste. A
 *     region may not cover part of a drawn block, it may not cover part of an
 *     answer grid, it may not overlap another region, and it may not be a
 *     sliver or most of the sheet. These need no labelled data, so they can be
 *     run over the whole catalogue.
 *
 * There is deliberately no hand-labelled ground truth. The manifest is the only
 * outside oracle, and it validates presence and top edge; extent is answered by
 * the violation rules alone. A golden-file path used to be read from here and
 * never had a file to read, which made an absent metric look like a passing one.
 *
 * Each book is scored in a process of its own (`--in-process` runs one here
 * instead). The detector holds a lot of structure while it works, and V8 hands
 * a book's memory back to the system only under pressure, so ten books in one
 * process end up resident together -- enough, measured, to take the machine
 * into swap. A short-lived process per book returns its memory at the one
 * moment that is certain, and a book that dies takes only itself with it.
 *
 *   node converter/tests/scorecard.mjs                     # every installed book
 *   node converter/tests/scorecard.mjs books/<id>.pdf      # one book
 *   node converter/tests/scorecard.mjs --pages 1-40        # a slice, for a quick loop
 *   node converter/tests/scorecard.mjs --write-baseline    # record what today looks like
 *   node converter/tests/scorecard.mjs --baseline          # and compare against it
 *   node converter/tests/scorecard.mjs --from-bake         # score the bakes, no PDFs needed
 */
import fs from "node:fs";
import zlib from "node:zlib";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  here, root, ActivityDetector, openDocument, pdfjsLib,
  bookIdForFile, interactiveOges, linkInteractiveOges, unplacedAnchors, pageList,
} from "./_harness.mjs";
import { LINK_REASONS } from "../../../js/interactive-links.js";
import { MemoryWatchdog } from "./_watchdog.mjs";

// Mirrors of the detector's own constants (js/activities.js:66-124). They are
// module-private there; kept in step by name so a change is easy to find.
const MIN_HOTSPOT = 6;
// A region taller than this much of the sheet has almost certainly swallowed
// the section it sits in rather than covering one question.
const TALL_REGION = 0.7;
// How much of a drawn block a region must miss before it counts as having cut
// it: a rounded corner grazing a rect is not a sliced panel.
const WHOLE_TOL = 0.05;

const BASELINE = path.join(here, "scorecard.json");
const BUCKETS_OUT = path.join(here, "buckets.json");

/* ---------------------------------------------------------------- geometry */

const area = (r) => Math.max(0, r.x1 - r.x0) * Math.max(0, r.y1 - r.y0);

function overlapArea(a, b) {
  return (
    Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) *
    Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0))
  );
}

/**
 * Did this rect cut the block instead of taking it whole?
 *
 * The user's rule is that a region covers a drawn dialogue, panel or answer
 * grid entirely or leaves it alone, so any partial share is the bug -- whichever
 * way round the all-or-nothing question is answered for that block.
 */
function cuts(rect, block) {
  const blockArea = area(block);
  if (blockArea <= 0) return false;
  const share = overlapArea(rect, block) / blockArea;
  return share > WHOLE_TOL && share < 1 - WHOLE_TOL;
}

/**
 * Blocks a region could sensibly have taken whole.
 *
 * Two kinds of drawn matter have to be dropped first or the count is noise.
 * Geometry reaching outside the sheet is an artefact of the path bbox, not
 * something on the page -- one workbook sheet 570 pt wide ruled answer cells
 * recorded out to x=703. And a shape far larger than the region is a tinted
 * background the question is merely standing on: `panelFor` refuses to snap out
 * to those for exactly that reason, so clipping one is not a sliced panel.
 */
function takeable(blocks, rect, pageWidth, pageHeight, requireLength) {
  return blocks.filter((b) => {
    if (b.x0 < -2 || b.y0 < -2 || b.x1 > pageWidth + 2 || b.y1 > pageHeight + 2) return false;
    if (!requireLength) return true;
    const along = Math.min(rect.y1, b.y1) - Math.max(rect.y0, b.y0);
    return along >= 0.5 * (b.y1 - b.y0);
  });
}

function quantiles(xs) {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const at = (f) => s[Math.min(s.length - 1, Math.floor(s.length * f))];
  return {
    median: +at(0.5).toFixed(2),
    p75: +at(0.75).toFixed(2),
    p90: +at(0.9).toFixed(2),
    p99: +at(0.99).toFixed(2),
    max: +s[s.length - 1].toFixed(2),
  };
}

/* ------------------------------------------------------------------ buckets */

/**
 * Every interactive entry in a book's manifest lands in exactly one of these.
 *
 * The point is attribution. A single matched-rate says how much is missing but
 * not what to fix next, and the rate used to be computed over the entries whose
 * printed page had already resolved -- so a folio failure removed an entry from
 * the denominator instead of counting against it, and the two largest classes of
 * failure were invisible by construction. Here the denominator is the manifest,
 * and everything that is not `ok` names its own cause.
 *
 * Listed worst-understood first; the histogram prints in this order, and the
 * fix order is bucket size.
 */
const BUCKETS = [
  "ok",                // matched a region that breaks no rule
  "rule-violation",    // matched a region, but that region is a bug by definition
  "unresolved-page",   // sayfano reached no PDF page through the folio map
  "page-not-read",     // resolved to a page outside this run's --pages slice
  "page-blank",        // the sheet was read and no region was found on it at all
  "anchor-excluded",   // posx/posy are (0,0): the entry marks no point on a sheet
  "anchor-nogrow",     // handed to the detector as an anchor; no region grew
  "contested",         // a candidate region existed and another entry won it
  ...LINK_REASONS,     // wrong-side, drift, label-mismatch, confidence-gated, no-position
];

/**
 * Which gate an entry reached, given everything the page walk learned about it.
 *
 * `LINK_REASONS` is ordered nearest-miss first, so the first reason present is
 * the furthest the entry actually got. An entry rejected on drift by one region
 * and on its label by another is a drift problem: the label test never had to
 * be the thing standing in the way.
 */
function bucketFor(why) {
  if (!why) return "page-blank";
  if (why.candidates > 0) return "contested";
  for (const reason of LINK_REASONS) if (why.reasons.has(reason)) return reason;
  return "page-blank";
}

/* ------------------------------------------------------------------- tally */

/**
 * Every running count a book's row is built from.
 *
 * It is a plain bag rather than locals inside the walk because the same page
 * has to be scored two ways: read live out of the PDF, and replayed out of a
 * bake. Those two must agree exactly or `--from-bake` is measuring something
 * else, and the only way to be sure of that is for both to run the same code.
 */
function newTally() {
  return {
    regions: 0, parts: 0, questions: 0, emptyRegions: 0, pagesWithRegions: 0,
    markers: 0, markersInRegion: 0,
    panelsCut: 0, solutionsCut: 0, solutionsCovered: 0, solutionsTotal: 0,
    offSheetSolutions: 0, tall: 0, slivers: 0, overlaps: 0,
    // The same five counts again, restricted to regions grown from the
    // publisher's icon. A rule is a rule whoever broke it, but a rise has to be
    // attributable before it can be fixed, and "more regions, so more of
    // everything" is not an explanation.
    byAnchored: { panelsCut: 0, solutionsCut: 0, tall: 0, slivers: 0, overlaps: 0 },
    anchored: 0, ogesSeen: 0, ogesOnBlankPages: 0,
    // One bucket per manifest entry, filled as its sheet is walked. Entries
    // whose page is never reached stay unset and are resolved after the walk.
    bucketOf: new Map(),
    drifts: [],
  };
}

/**
 * Score one sheet: its regions against the rules, its entries against the join.
 *
 * @param {object} t          the tally to add to
 * @param {number} p          PDF page number
 * @param {object} res        the page as analyzePage returns it, or as a bake replays it
 * @param {object|null} diag  that sheet's drawn geometry, live or from the sidecar
 * @param {object[]} pageOges the manifest entries whose printed page is this sheet
 * @param {Set<string>} wasAnchorIds  entries handed to the growth pass for this sheet
 */
function tallyPage(t, p, res, diag, pageOges, wasAnchorIds) {
  if (res.activities.length) t.pagesWithRegions++;
  t.regions += res.activities.length;

  const allParts = [];
  const anchoredParts = new Set();
  // Which regions on this sheet break a rule. A manifest entry matched to one
  // of these is not a success -- the region exists and is wrong -- so the join
  // below files it under `rule-violation` rather than `ok`.
  const badRegions = new Set();
  const partOwner = new Map();
  for (const act of res.activities) {
    const own = act.parts && act.parts.length ? act.parts : [act.rect];
    const fromAnchor = !!act.anchored;
    t.parts += own.length;
    t.questions += act.items.length;
    if (!act.items.length) t.emptyRegions++;
    for (const rect of own) {
      allParts.push(rect);
      partOwner.set(rect, act.id);
      if (fromAnchor) anchoredParts.add(rect);
      const h = rect.y1 - rect.y0;
      if (h > TALL_REGION * res.pageHeight) {
        t.tall++; badRegions.add(act.id); if (fromAnchor) t.byAnchored.tall++;
      }
      if (h < MIN_HOTSPOT || rect.x1 - rect.x0 < MIN_HOTSPOT) {
        t.slivers++; badRegions.add(act.id); if (fromAnchor) t.byAnchored.slivers++;
      }
      if (diag) {
        const W = res.pageWidth, H = res.pageHeight;
        for (const panel of takeable(diag.panels, rect, W, H, true)) {
          if (cuts(rect, panel)) {
            t.panelsCut++; badRegions.add(act.id);
            if (fromAnchor) t.byAnchored.panelsCut++;
          }
        }
        for (const block of takeable(diag.solutions, rect, W, H, false)) {
          if (cuts(rect, block)) {
            t.solutionsCut++; badRegions.add(act.id);
            if (fromAnchor) t.byAnchored.solutionsCut++;
          }
        }
      }
    }
  }

  // separateRects() is supposed to have pushed these apart already, so
  // anything still intersecting is a region stealing another's clicks.
  for (let i = 0; i < allParts.length; i++) {
    for (let j = i + 1; j < allParts.length; j++) {
      if (overlapArea(allParts[i], allParts[j]) > 1) {
        t.overlaps++;
        badRegions.add(partOwner.get(allParts[i]));
        badRegions.add(partOwner.get(allParts[j]));
        if (anchoredParts.has(allParts[i]) || anchoredParts.has(allParts[j])) {
          t.byAnchored.overlaps++;
        }
      }
    }
  }

  if (diag) {
    t.markers += diag.markers.length;
    for (const m of diag.markers) {
      if (allParts.some((r) => overlapArea(r, m) > 0)) t.markersInRegion++;
    }
    const onSheet = diag.solutions.filter(
      (b) => b.x0 >= -2 && b.y0 >= -2 && b.x1 <= res.pageWidth + 2 && b.y1 <= res.pageHeight + 2
    );
    t.offSheetSolutions += diag.solutions.length - onSheet.length;
    t.solutionsTotal += onSheet.length;
    for (const block of onSheet) {
      if (allParts.some((r) => overlapArea(r, block) >= 0.5 * area(block))) {
        t.solutionsCovered++;
      }
    }
  }

  // ---- the join against the publisher's manifest
  if (!pageOges.length) return;
  t.ogesSeen += pageOges.length;
  if (!res.activities.length) t.ogesOnBlankPages += pageOges.length;

  // A region grown from an entry's own icon, keyed by the entry it came from.
  const anchorRegionFor = new Map();
  for (const a of res.activities) {
    if (a.id.startsWith(`p${p}-oge-`)) {
      anchorRegionFor.set(a.id.slice(`p${p}-oge-`.length), a.id);
    }
  }
  t.anchored += anchorRegionFor.size;

  const explain = new Map();
  const links = linkInteractiveOges(res, pageOges, explain);
  const linkedRegionFor = new Map();
  for (const [actId, oge] of links) linkedRegionFor.set(String(oge.id), actId);

  for (const oge of pageOges) {
    const id = String(oge.id);
    if (t.bucketOf.has(id)) continue;   // an entry is filed once, on the first sheet that claims it
    // A match has to be a match to a region that is not itself a bug. The
    // region existing is not the question -- an anchored region always exists,
    // which is exactly why anchoring used to count as success by construction
    // and told us nothing.
    const actId = linkedRegionFor.get(id) ?? anchorRegionFor.get(id);
    if (actId) {
      t.bucketOf.set(id, badRegions.has(actId) ? "rule-violation" : "ok");
      continue;
    }
    if (!res.activities.length) { t.bucketOf.set(id, "page-blank"); continue; }
    // (0, 0) is the publisher's "belongs to no point on this sheet", so the
    // entry is never offered to the detector as an anchor at all.
    if (oge.posx === 0 && oge.posy === 0) { t.bucketOf.set(id, "anchor-excluded"); continue; }
    if (wasAnchorIds.has(id)) { t.bucketOf.set(id, "anchor-nogrow"); continue; }
    t.bucketOf.set(id, bucketFor(explain.get(id)));
  }

  for (const [actId, oge] of links) {
    const act = res.activities.find((a) => a.id === actId);
    const top = ((res.pageHeight - act.rect.y1) / res.pageHeight) * 100;
    t.drifts.push(Math.abs(oge.posy - top));
  }
}

/**
 * Turn a finished tally into the book's row, filing whatever the walk never
 * reached.
 *
 * Those leftovers are the entries the old denominator dropped: their printed
 * page resolved to no sheet, so no sheet ever offered them to the join and they
 * cost the score nothing at all.
 */
function finishRow(t, ctx) {
  const { file, bookId, pages, oges, calibration, pagesForPrinted, pagesSpec,
          folioCollisions, peakRssMb, source } = ctx;
  const printedSeen = [...pagesForPrinted.keys()];
  const lowest = printedSeen.length ? Math.min(...printedSeen) : null;
  const highest = printedSeen.length ? Math.max(...printedSeen) : null;
  for (const oge of oges) {
    const id = String(oge.id);
    if (t.bucketOf.has(id)) continue;
    // On a --pages slice, a printed page beyond the sheets read is not a folio
    // failure, it is simply out of frame; on a whole-book run the two coincide
    // and this reports nothing.
    const outOfFrame =
      pagesSpec && lowest !== null && (oge.sayfano < lowest || oge.sayfano > highest);
    t.bucketOf.set(id, outOfFrame ? "page-not-read" : "unresolved-page");
  }

  const buckets = Object.fromEntries(BUCKETS.map((b) => [b, 0]));
  for (const b of t.bucketOf.values()) buckets[b] = (buckets[b] ?? 0) + 1;

  return {
    book: path.basename(file),
    bookId,
    source,
    pages,
    calibration,
    yield: {
      regions: t.regions,
      parts: t.parts,
      questions: t.questions,
      pagesWithRegions: t.pagesWithRegions,
      blankPageShare: +(1 - t.pagesWithRegions / pages).toFixed(3),
      emptyRegionShare: t.regions ? +(t.emptyRegions / t.regions).toFixed(3) : 0,
    },
    join: {
      oges: oges.length,
      ogesOnReadPages: t.ogesSeen,
      matched: buckets.ok,
      anchored: t.anchored,
      // Against every interactive entry in the manifest. A partial run reports
      // its out-of-frame entries as `page-not-read` rather than quietly leaving
      // them out of the denominator, so a slice and a whole book are comparable
      // only through the buckets -- which is the honest answer.
      matchRate: oges.length ? +(buckets.ok / oges.length).toFixed(3) : null,
      // Entries whose printed page reached no sheet at all: the folio map's own
      // failure rate, previously invisible.
      unresolvedPage: buckets["unresolved-page"],
      folioCollisions,
      onPagesWithNoRegion: t.ogesOnBlankPages,
      drift: quantiles(t.drifts),
    },
    buckets,
    ...(peakRssMb === undefined ? {} : { peakRssMb }),
    violations: {
      panelsCut: t.panelsCut, solutionsCut: t.solutionsCut,
      tall: t.tall, slivers: t.slivers, overlaps: t.overlaps,
    },
    violationsFromAnchors: t.byAnchored,
    stats: {
      markers: t.markers,
      markersOutsideRegions: t.markers - t.markersInRegion,
      solutionCoverage: t.solutionsTotal
        ? +(t.solutionsCovered / t.solutionsTotal).toFixed(3)
        : null,
      // Answer blocks recorded outside the sheet. Not a region bug -- a
      // solutionBlocks() one -- but it hides real cuts, so it is watched.
      offSheetSolutions: t.offSheetSolutions,
    },
  };
}

/* ------------------------------------------------------------------ scoring */

async function scoreBook(file, pagesSpec) {
  const watchdog = new MemoryWatchdog();
  const bookId = bookIdForFile(file);
  const doc = await openDocument(file);
  const det = new ActivityDetector(doc, path.basename(file));
  det.loadOverrides({});                 // the algorithm, not the hand corrections
  det.diagnostics = new Map();           // page structure, for the violation checks
  await det.calibrate();

  const pages = pageList(pagesSpec, doc.numPages);

  // The folio is read off whichever pages have been looked at, and it is the key
  // the publisher's manifest is indexed by. Settle it over the whole run first
  // so the join does not depend on where the walk below happens to start.
  // textItems() notes the folio itself, and the sheet's text is dropped again
  // straight away: the vote is what this pass is for, and a whole book's text
  // items held at once is a large part of what made a full run unaffordable.
  for (const p of pages) {
    await det.textItems(p);
    det.releasePage(p);
    watchdog.check(`folio pass, page ${p}`);
  }

  const pagesForPrinted = invertFolio(pages.map((p) => [p, det.printedPageFor(p)]));
  const oges = bookId ? interactiveOges(bookId) : [];
  const ogesByPrintedPage = groupByPrintedPage(oges);

  // Anchors: the entries the publisher placed on a sheet that the label join
  // cannot account for. Resolved sheet by sheet inside the walk below, off that
  // sheet's own labelled pass, so the scorecard judges the same regions the
  // reader will show without a second walk of the book.
  det.anchorsByPage = new Map();

  const t = newTally();
  for (const p of pages) {
    let res = await det.analyzePage(p, pdfjsLib);

    // Entries on this sheet that the labelled pass did not reach become
    // anchors, and the sheet is analysed again with them in hand. The anchors
    // are read off the labelled answer, so they can never change which letters
    // were accepted.
    const printedNow = det.printedPageFor(p);
    const pageOges = (printedNow !== null && ogesByPrintedPage.get(printedNow)) || [];
    if (pageOges.length) {
      const left = unplacedAnchors(res, pageOges);
      if (left.length) {
        det.anchorsByPage.set(p, left.map((o) => ({ id: o.id, posx: o.posx, posy: o.posy })));
        det.releasePage(p);
        res = await det.analyzePage(p, pdfjsLib);
      }
    }

    const wasAnchorIds = new Set((det.anchorsByPage.get(p) || []).map((a) => String(a.id)));
    tallyPage(t, p, res, det.diagnostics.get(p), pageOges, wasAnchorIds);

    det.releasePage(p);                  // one page at a time; do not hold the book
    try {
      (await doc.getPage(p)).cleanup();  // nor the fonts pdf.js parsed for it
    } catch (_) {}
    watchdog.check(`page ${p}`);
  }

  const calibration = {
    enabled: det.enabled,
    confidence: det.confidence,
    markerSize: det.markerSize,
    styles: det.markerStyles ? [...det.markerStyles] : [],
    folioOffset: det.folioOffset,
  };
  await doc.destroy();

  return finishRow(t, {
    file, bookId, pages: pages.length, oges, calibration, pagesForPrinted,
    pagesSpec, folioCollisions: collisionsIn(pagesForPrinted),
    peakRssMb: watchdog.peakMb, source: "pdf",
  });
}

/* ---------------------------------------------------------------- from bake */

/** printed page -> the sheets claiming it, so a folio collision is visible. */
function invertFolio(entries) {
  const byPrinted = new Map();
  for (const [p, printed] of entries) {
    if (printed === null || printed === undefined) continue;
    if (!byPrinted.has(printed)) byPrinted.set(printed, []);
    byPrinted.get(printed).push(p);
  }
  return byPrinted;
}

const collisionsIn = (m) => [...m.values()].filter((v) => v.length > 1).length;

function groupByPrintedPage(oges) {
  const by = new Map();
  for (const o of oges) {
    if (!by.has(o.sayfano)) by.set(o.sayfano, []);
    by.get(o.sayfano).push(o);
  }
  return by;
}

const toRect = (a) => ({ x0: a[0], y0: a[1], x1: a[2], y1: a[3] });

/**
 * Score a book from what `bake_activities.mjs` already wrote, without the PDF.
 *
 * Re-parsing every PDF to answer a question about the *join* is what made this
 * loop cost hours: the regions did not change, only the rule applied to them.
 * A bake plus its geometry sidecar carries everything the scoring reads, so the
 * whole catalogue can be rescored in seconds and in a few hundred MB -- and any
 * change to the join, the tolerances or the assignment can be measured before
 * it is committed to. Only a change inside the detector needs a re-bake.
 */
function scoreBaked(bookId, pagesSpec) {
  const dir = path.join(root, "activities", "books", bookId);
  const regionsPath = path.join(dir, "regions.json");
  const diagPath = path.join(dir, "diagnostics.json.gz");
  if (!fs.existsSync(regionsPath)) throw new Error("not baked");
  const baked = JSON.parse(fs.readFileSync(regionsPath, "utf8"));

  // Without the sidecar there is no drawn geometry, so every violation check
  // silently passes and the book scores as clean -- which is worse than not
  // scoring it, because it looks like a result. A book is scored from its bake
  // only when the sidecar is present and describes the same build of it.
  if (!fs.existsSync(diagPath)) {
    throw new Error("no diagnostics sidecar; re-bake this book");
  }
  const diag = JSON.parse(zlib.gunzipSync(fs.readFileSync(diagPath)).toString("utf8"));
  if (diag.fingerprint !== baked.fingerprint) {
    throw new Error("diagnostics sidecar is stale; re-bake this book");
  }
  const diagPages = diag.pages || {};

  const pages = pageList(pagesSpec, baked.pageCount);
  const pagesForPrinted = invertFolio(
    pages.map((p) => [p, baked.folio.byPage[p] ?? null])
  );
  const oges = interactiveOges(bookId);
  const ogesByPrintedPage = groupByPrintedPage(oges);
  const anchorsByPage = baked.anchors || {};

  const t = newTally();
  for (const p of pages) {
    const page = baked.pages[p];
    const printed = baked.folio.byPage[p] ?? null;
    const pageOges = (printed !== null && ogesByPrintedPage.get(printed)) || [];
    const res = {
      pageWidth: page ? page.pageWidth : (diagPages[p] ? diagPages[p].pageWidth : 0),
      pageHeight: page ? page.pageHeight : (diagPages[p] ? diagPages[p].pageHeight : 0),
      confidence: baked.calibration.confidence,
      activities: (page ? page.activities : []).map((a) => ({
        ...a,
        rect: toRect(a.rect),
        parts: (a.parts || []).map(toRect),
        items: a.items || [],
      })),
    };
    // A sheet with no regions is baked as nothing at all, but it may still be
    // the page the publisher put an entry on -- and that entry is a `page-blank`
    // failure, not an unreachable one. Skipping it here would quietly move it
    // into the folio's column instead.
    const d = diagPages[p];
    const diag = d
      ? { panels: d.panels.map(toRect), solutions: d.solutions.map(toRect), markers: d.markers.map(toRect) }
      : null;
    tallyPage(t, p, res, diag, pageOges, new Set(anchorsByPage[p] || []));
  }

  return finishRow(t, {
    file: `${bookId}.pdf`, bookId, pages: pages.length, oges,
    calibration: {
      enabled: baked.calibration.enabled,
      confidence: baked.calibration.confidence,
      markerSize: baked.calibration.markerSize,
      styles: baked.calibration.markerStyles || [],
      folioOffset: baked.folio.offset,
    },
    pagesForPrinted, pagesSpec, folioCollisions: collisionsIn(pagesForPrinted),
    source: "bake",
  });
}

/* -------------------------------------------------------------------- books */

function installedBooks() {
  const dir = path.join(root, "books");
  const seen = new Set();
  const out = [];
  for (const name of fs.readdirSync(dir).sort()) {
    if (!name.endsWith(".pdf")) continue;
    const file = path.join(dir, name);
    // full_pdf.pdf and matematik.pdf are symlinks to two of the guids beside
    // them; scoring a book twice under two names says nothing new.
    const real = fs.realpathSync(file);
    if (seen.has(real)) continue;
    seen.add(real);
    out.push(real);
  }
  return out;
}

/* ------------------------------------------------------------------ report */

function table(rows) {
  const line = (r) => [
    r.book.slice(0, 40).padEnd(40),
    String(r.pages).padStart(5),
    String(r.yield.regions).padStart(7),
    String(r.yield.questions).padStart(6),
    (r.join.matchRate === null ? "-" : `${(r.join.matchRate * 100).toFixed(0)}%`).padStart(6),
    String(r.join.oges || "-").padStart(5),
    String(
      r.violations.panelsCut + r.violations.solutionsCut +
      r.violations.tall + r.violations.slivers + r.violations.overlaps
    ).padStart(5),
    String(r.join.unresolvedPage ?? "-").padStart(6),
  ].join(" ");
  console.log(
    "book".padEnd(40) + " " + "pages".padStart(5) + " " + "regions".padStart(7) +
    " " + "quest.".padStart(6) + " " + "match".padStart(6) + " " + "oges".padStart(5) +
    " " + "viol.".padStart(5) + " " + "nofol".padStart(6)
  );
  for (const r of rows) console.log(line(r));
}

/**
 * Where every one of the publisher's entries went, across the run.
 *
 * Printed under the table because it, not the headline rate, is the work queue:
 * the rate says how much is missing, the histogram says what to fix first. Only
 * non-empty buckets are printed, in the taxonomy's own order.
 */
function histogram(rows) {
  const total = Object.fromEntries(BUCKETS.map((b) => [b, 0]));
  for (const r of rows) {
    if (!r.buckets) continue;
    for (const [b, n] of Object.entries(r.buckets)) total[b] = (total[b] ?? 0) + n;
  }
  const n = Object.values(total).reduce((a, b) => a + b, 0);
  if (!n) return total;
  console.log(`\nwhere the ${n} manifest entries went:`);
  const width = Math.max(...BUCKETS.map((b) => b.length));
  for (const b of BUCKETS) {
    if (!total[b]) continue;
    const share = total[b] / n;
    console.log(
      `  ${b.padEnd(width)}  ${String(total[b]).padStart(5)}  ${(share * 100).toFixed(1).padStart(5)}%  ` +
      "#".repeat(Math.round(share * 40))
    );
  }
  return total;
}

function compare(rows, baseline) {
  const by = new Map(baseline.books.map((b) => [b.book, b]));
  let regressions = 0;
  console.log("\nagainst baseline:");
  for (const row of rows) {
    const was = by.get(row.book);
    if (!was) { console.log(`  ${row.book}: new, no baseline`); continue; }
    const deltas = [];
    const note = (name, now, then, worseWhen) => {
      if (now === then || then === null || now === null) return;
      const worse = worseWhen(now, then);
      if (worse) regressions++;
      deltas.push(`${name} ${then}->${now}${worse ? " WORSE" : ""}`);
    };
    const vSum = (v) => v.panelsCut + v.solutionsCut + v.tall + v.slivers + v.overlaps;
    note("violations", vSum(row.violations), vSum(was.violations), (a, b) => a > b);
    // A bare total says a rule broke more often without saying which rule, and
    // the five are not the same bug. Name the ones that moved.
    if (vSum(row.violations) > vSum(was.violations)) {
      const moved = Object.keys(row.violations)
        .filter((k) => row.violations[k] > (was.violations[k] ?? 0))
        .map((k) => {
          const from = row.violationsFromAnchors ? row.violationsFromAnchors[k] : null;
          return `${k} ${was.violations[k] ?? 0}->${row.violations[k]}` +
            (from ? ` (${from} anchored)` : "");
        });
      if (moved.length) deltas.push(`[${moved.join("; ")}]`);
    }
    note("matched", row.join.matched, was.join.matched, (a, b) => a < b);
    note("regions", row.yield.regions, was.yield.regions, () => false);
    note("questions", row.yield.questions, was.yield.questions, () => false);
    if (deltas.length) console.log(`  ${row.book}: ${deltas.join(", ")}`);
  }
  console.log(regressions ? `\n${regressions} regression(s)` : "\nno regressions");
  return regressions;
}

/* -------------------------------------------------------------------- main */

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(name);
const value = (name) => {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : null;
};
const files = argv.filter((a) => a.endsWith(".pdf"));
const pagesSpec = value("--pages");
const fromBake = flag("--from-bake");

/** Every book that has been baked, whether or not its PDF is still on disk. */
function bakedBooks() {
  const dir = path.join(root, "activities", "books");
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).sort().filter((id) =>
    fs.existsSync(path.join(dir, id, "regions.json"))
  );
}

const targets = files.length ? files.map((f) => path.resolve(f)) : installedBooks();
// Scoring from bakes costs a few hundred MB for the whole catalogue, so there
// is nothing to isolate in a child and no reason to pay for one.
const inProcess = fromBake || flag("--in-process") || targets.length === 1;

/**
 * Score one book in a child process and read its row back.
 *
 * The child is this same script with `--in-process`, so there is one scoring
 * implementation and the child's row is built by the code above verbatim. Its
 * stdout (the one-row table) is dropped; stderr is passed through so progress
 * and failures stay visible.
 */
function scoreInChild(file) {
  const out = path.join(os.tmpdir(), `scorecard-${process.pid}-${path.basename(file)}.json`);
  // A heap cap the machine can absorb. It is not a budget the scoring needs --
  // a book fits in well under this -- it is a fuse: a book that runs away kills
  // its own process instead of taking the desktop into swap with it. The
  // watchdog inside the child is the earlier fuse, on resident memory rather
  // than on the JS heap; this one only catches what that cannot see.
  const args = [
    "--max-old-space-size=1536",
    fileURLToPath(import.meta.url), file, "--in-process", "--json", out,
  ];
  if (pagesSpec) args.push("--pages", pagesSpec);
  const run = spawnSync(process.execPath, args, { stdio: ["ignore", "ignore", "inherit"] });
  try {
    if (run.status === 0 && fs.existsSync(out)) {
      const row = JSON.parse(fs.readFileSync(out, "utf8")).books[0];
      if (row) return row;
    }
    const how = run.signal ? `killed by ${run.signal}` : `exit ${run.status}`;
    return { book: path.basename(file), error: `scoring process ${how}` };
  } finally {
    fs.rmSync(out, { force: true });
  }
}

const rows = [];
if (fromBake) {
  const ids = files.length ? files.map((f) => bookIdForFile(f)).filter(Boolean) : bakedBooks();
  if (!ids.length) {
    console.error("nothing baked yet; run `npm run bake -- --all` first");
    process.exit(2);
  }
  for (const id of ids) {
    try {
      rows.push(scoreBaked(id, pagesSpec));
    } catch (err) {
      process.stderr.write(`  ${id}: ${err && err.message}\n`);
      rows.push({ book: `${id}.pdf`, bookId: id, error: String(err && err.message) });
    }
  }
} else {
  for (const file of targets) {
    // The child announces nothing: the parent has already said which book this is.
    if (!flag("--in-process")) process.stderr.write(`scoring ${path.basename(file)}\n`);
    try {
      rows.push(inProcess ? await scoreBook(file, pagesSpec) : scoreInChild(file));
    } catch (err) {
      process.stderr.write(`  failed: ${err && err.message}\n`);
      rows.push({ book: path.basename(file), error: String(err && err.message) });
    }
  }
}

const usable = rows.filter((r) => !r.error);
table(usable);

if (fromBake || !flag("--in-process")) {
  const total = histogram(usable);
  const ok = total.ok ?? 0;
  const all = Object.values(total).reduce((a, b) => a + b, 0);
  if (all) {
    console.log(`\nmatched ${ok} / ${all} = ${((ok / all) * 100).toFixed(1)}%`);
  }
  fs.writeFileSync(
    BUCKETS_OUT,
    JSON.stringify(
      {
        builtAt: new Date().toISOString(),
        pagesSpec,
        total,
        byBook: Object.fromEntries(usable.filter((r) => r.buckets).map((r) => [r.bookId, r.buckets])),
      },
      null, 2
    ) + "\n"
  );
  console.log(`buckets written to ${path.relative(root, BUCKETS_OUT)}`);
}

const report = { builtAt: new Date().toISOString(), pagesSpec, books: rows };

if (flag("--write-baseline")) {
  fs.writeFileSync(BASELINE, JSON.stringify(report, null, 2) + "\n");
  console.log(`\nbaseline written to ${path.relative(root, BASELINE)}`);
} else if (flag("--baseline")) {
  if (!fs.existsSync(BASELINE)) {
    console.error("no baseline yet; run with --write-baseline first");
    process.exit(2);
  }
  const regressions = compare(usable, JSON.parse(fs.readFileSync(BASELINE, "utf8")));
  if (regressions) process.exit(1);
} else {
  const out = value("--json");
  if (out) fs.writeFileSync(out, JSON.stringify(report, null, 2) + "\n");
}
