/**
 * Activity / question detection for the Interaktiv PDF reader.
 *
 * The source PDF is an untagged InDesign export (no structure tree, no annotations),
 * so activities are recovered typographically: the lettered labels (a, b, c ...) are
 * single-character text items drawn in one specific font at one specific size that is
 * used for nothing else in the document, and the steps of a practical carry their
 * number and word together ("1. Adım", "2. Adım"). That style is *learned* per
 * document (calibrate()) rather than hardcoded, so other PDFs either work or
 * cleanly opt out.
 *
 * All rectangles are stored in PDF user space (y-up, origin bottom-left) so they stay
 * valid across zoom, rotation and re-render; callers convert with
 * viewport.convertToViewportRectangle().
 */
import { hitTestRegion } from "./interactive-links.js";

// A list label is a single letter or a one/two digit number, on its own, with at
// most the usual decoration around it: "a", "a)", "a.", "(a)", "1", "1.", "1)".
// Books differ in which of those they use and the reader cannot know in advance,
// so the grammar accepts them all and calibration decides which style of item is
// actually a label in this document. The alphabet is the Turkish one as well as
// the Latin -- a book that labels its photographs "a b c ç" stops being a list
// the moment the ç falls out of the grammar -- and a label's `value` orders it
// inside whichever alphabet its book is using: Latin letters keep their Latin
// places (so an English book's p -> q -> r stays a run) and the eight letters
// Turkish inserts take the fractional place between their neighbours, so a
// Turkish book's c -> ç -> d reads as consecutive too.
const LABEL_RE = /^\(?\s*([a-zçğıöşü]|\d{1,2})\s*[.):\]]?\s*\)?$/u;
// A step marker is a number with the word attached: "1. Adım", "2 Adım",
// "(3) Adım" -- the way a Turkish book numbers the steps of a practical or an
// experiment. The word is matched letter by letter rather than case-insensitively
// because the dotless ı does not case-fold to i in a /u regex, and a book sets
// the word in any of its casings ("Adım", "adım", "ADIM"). The trailing [.:]
// takes the colon a step routinely carries before its instruction.
const STEP_RE = /^\(?\s*(\d{1,2})\s*[.):\]]?\s*\)?\s*[Aa][Dd][ıiIİ][Mm]\s*[.:]?\s*$/u;
const NUMBER_RE = /^\d{1,2}$/;
const LABEL_VALUES = (() => {
  const values = new Map();
  "abcdefghijklmnopqrstuvwxyz".split("").forEach((ch, i) => values.set(ch, i + 1));
  values.set("ç", 3.5);
  values.set("ğ", 7.5);
  values.set("ı", 8.5);
  values.set("ö", 15.5);
  values.set("ş", 19.5);
  values.set("ü", 21.5);
  return values;
})();
// The letters that may follow a label in a run: its value plus the fractional
// insertions that fall before the next Latin letter. "c" is followed by "ç" in
// a Turkish book and by "d" in a Latin one, so both are successors of "c".
// Digits succeed themselves by one, and share the map so one `follows` serves
// both alphabets.
const LABEL_SUCCESSORS = (() => {
  const successors = new Map();
  for (let n = 1; n <= 100; n++) successors.set(n, [n + 1]);
  for (const [, v] of LABEL_VALUES) {
    const nexts = [];
    for (const v2 of LABEL_VALUES.values()) {
      if (v2 > v && v2 - v <= 1) nexts.push(v2);
    }
    successors.set(v, nexts);
  }
  return successors;
})();

const COLUMN_TOLERANCE = 12;   // pt: x spread within which markers are the same column
const HEADER_BAND = 40;        // pt from top: running "THEME n" header
const FOOTER_BAND = 44;        // pt from bottom: folio / page number
const PAD = 8;                 // pt of breathing room around a detected region
const GUTTER_RATIO = 0.5;      // a label needs this much of its own size clear on its left
const HANGING_INDENT = 26;     // pt: how far the instruction text sits to the right
const HOTSPOT_GAP = 3;         // pt of clear space kept between two hotspots
const MIN_HOTSPOT = 6;         // pt: below this a region is dropped as degenerate
const MIN_CONTINUATION = 60;   // pt: shorter cross-column overflow is a stray banner
const SAME_DIRECTION = 0.98;   // dot product above which two runs share a text direction
const FOLIO_MAX = 4;           // digits a printed page number may run to
const FOLIO_SPAN = 500;        // pages a folio may differ from its sheet before it reads as a year, a price, a figure

// Calibration sampling (see calibrate). A sample is read in widening passes so a
// book whose activities are thinly spread is still found, without making every
// book pay for the pages that took to read.
const SAMPLE_FIRST = 14;       // pages read before the evidence is judged at all
const SAMPLE_DECISIVE = 12;    // score above which more pages cannot change the pick
const SAMPLE_LIMIT = 64;       // pages read at most, however long the book is
const SAMPLE_SHARE = 0.35;     // and never more than this fraction of the book
const SIZE_TOLERANCE = 0.75;   // pt: sizes this close in one face are one type size

// Secondary label schemes (see calibrate). A textbook does not set all its
// questions in one style: each kind of section -- the work at the top of a
// topic, the check at its foot, the test at the end of the book -- has a
// convention of its own, and the styles stand beside one another rather than
// nesting. A style with list evidence of its own is therefore kept whatever the
// dominant style is doing, on evidence read in absolute terms:
const SECONDARY_SCORE = 2;     // follows found across the sample, at least
const SECONDARY_RUN = 3;       // labels in one strict a->b->c run on a single page
// How many lines have to start at the same x before it is a column and not an
// indent. Only used where a page has no labels to read its columns off.
const MIN_COLUMN_LINES = 4;
// How much of the content height two candidate columns must spend side by
// side before the channel between them is read as a gutter.
const COLUMN_PAIRED = 0.35;
// How far above an icon the first line of its activity may still start: the
// icon is commonly hung against the middle of that line, not its top.
const ANCHOR_ABOVE = 24;  // pt
const SECONDARY_MIN_SIZE = 7;  // pt: below this, numbers label figures, not questions

// Page-structure segmentation (see segmentStrips).
const COLUMN_CHANNEL = 10;     // pt: whitespace this wide still reads as a column gutter
const BRIDGE = 12;             // pt: a clear gap this short does not interrupt a full-width run
const MIN_STRIP = 24;          // pt: shorter strips are absorbed into their taller neighbour
const STACK_GAP = 14;          // pt: vertical gap two pieces of one region may still bridge
const STACK_OVERLAP = 0.6;     // fraction of the narrower piece that must overlap horizontally
const COLUMN_CLEAR = 0.5;      // fraction of the decided height a real column gutter stays open
const COLUMN_EDGE = 6;         // pt a block may overhang its column and still count as its own
const FLOW_LEADING = 1.8;      // multiple of the leading that still reads as the next line of a question
const FLOW_BREAK = 90;         // pt of blank that ends a column's flow rather than merely spacing it
const HEADING_RATIO = 1.15;    // type this much larger than a question's own opens a new section
const BODY_EVIDENCE = 100;     // pt of text in one size before it counts as the question's own
const HEADING_MEASURE = 0.6;   // fraction of the measure a heading keeps under: it names, not says

// Drawn panels that carry text: the dialogue bubble, the coloured box, the
// worked example set on a tint (see textPanels).
const PANEL_MIN_W = 60;        // pt: narrower than this a filled path is a rule or a chip
const PANEL_MIN_H = 30;        // pt: shorter than this it is a banner, not a panel
const PANEL_LINES = 2;         // lines of prose inside before a shape is a panel of text

// Solution space: the ruled lines, empty table cells and answer panels a
// workbook prints for the reader to write in (see solutionBlocks).
const RULE_THICK = 2.5;        // pt: a path this thin is a drawn rule, not a box
const RULE_MIN = 20;           // pt: a shorter path is a tick or a border, not a rule
const RULE_CLUSTER = 6;        // pt: rules this close across their length share a grid
const RULE_ROW = 30;           // pt: half the tallest row one grid may be ruled in
const MIN_CELL = 8;            // pt: below this a cell is too small to write in

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/**
 * Is this run something the page *says*, as opposed to something it merely
 * numbers? An empty answer cell is not made non-empty by the "3." printed in
 * its corner, and a ruled answer box is still blank paper with a "1" beside
 * each line -- but one word of prose in there means the space is already
 * filled, and it is a worked example rather than somewhere to write.
 */
function isProse(item) {
  const text = (item.text || "").trim();
  if (!text) return false;
  if (parseLabel(text)) return false;
  return /[\p{L}\p{N}]/u.test(text);
}

/**
 * Read a text run as a list label, or return null. `kind` separates the
 * alphabets a book may number a list in, and `value` is the position in that
 * alphabet, so "c", "c)" and "3." all order the same way and a run of labels can
 * be checked for consecutiveness without caring which alphabet it is drawn from.
 *
 * Steps are an alphabet of their own rather than digits with a suffix, because a
 * page may carry both lists at once -- questions numbered "1. 2." and the steps
 * of its practical numbered "1. Adım 2. Adım" -- and each must be validated as
 * the complete list it is without the other interrupting it. Their values are
 * numeric, so they order and succeed exactly as digits do.
 */
function parseLabel(text) {
  const step = STEP_RE.exec(text);
  if (step) {
    const value = parseInt(step[1], 10);
    return value >= 1 ? { kind: "step", value } : null;
  }
  const m = LABEL_RE.exec(text);
  if (!m) return null;
  const token = m[1];
  if (/^\d/.test(token)) {
    const value = parseInt(token, 10);
    return value >= 1 ? { kind: "digit", value } : null;
  }
  return { kind: "letter", value: LABEL_VALUES.get(token) ?? token.charCodeAt(0) - 96 };
}

/** Do two labels follow one another in the same alphabet? */
function follows(prev, next) {
  return prev.kind === next.kind && (LABEL_SUCCESSORS.get(prev.value) || []).includes(next.value);
}

function unionRect(a, b) {
  if (!a) return { ...b };
  if (!b) return a;
  return {
    x0: Math.min(a.x0, b.x0),
    y0: Math.min(a.y0, b.y0),
    x1: Math.max(a.x1, b.x1),
    y1: Math.max(a.y1, b.y1),
  };
}

function rectArea(r) {
  return Math.max(0, r.x1 - r.x0) * Math.max(0, r.y1 - r.y0);
}

/** Vertical overlap between a rect and a [lo, hi] band. */
function bandOverlap(rect, lo, hi) {
  return Math.max(0, Math.min(rect.y1, hi) - Math.max(rect.y0, lo));
}

/** Does `r` hold the whole of `inner`, give or take `slack` points? */
function encloses(r, inner, slack = 1) {
  return r.x0 - slack <= inner.x0 && r.x1 + slack >= inner.x1 &&
         r.y0 - slack <= inner.y0 && r.y1 + slack >= inner.y1;
}

/** The distinct values in `xs`, treating anything within `tol` as one. */
function dedupe(xs, tol = RULE_CLUSTER / 2) {
  const out = [];
  for (const v of [...xs].sort((a, b) => a - b)) {
    if (!out.length || v - out[out.length - 1] > tol) out.push(v);
  }
  return out;
}

/** Is the centre of `it` inside rect `r`? */
function contains(r, it) {
  const cx = (it.x0 + it.x1) / 2;
  const cy = (it.y0 + it.y1) / 2;
  return cx >= r.x0 && cx <= r.x1 && cy >= r.y0 && cy <= r.y1;
}

/** Fuse neighbouring strips whose columns are the same. */
function coalesceStrips(strips) {
  const out = [];
  for (const s of strips) {
    const prev = out[out.length - 1];
    if (
      prev &&
      prev.dead === s.dead &&
      prev.active.every((a, i) => a === s.active[i]) &&
      prev.absorb.every((a, i) => a === s.absorb[i])
    ) {
      prev.bottom = Math.min(prev.bottom, s.bottom);
    } else {
      out.push({ ...s, active: s.active.slice(), absorb: s.absorb.slice() });
    }
  }
  return out;
}

/**
 * Merge text runs into the lines they were set as.
 *
 * PDF.js emits one line as several runs -- a change of style, a soft hyphen, a
 * word set apart all start a new one -- and the runs of a line that spans the
 * page each sit wholly inside one column band. So any question of the form
 * "does this column carry content of its own here?" has to be asked of lines:
 * asked of runs, the tail of a full-width sentence answers yes for a column the
 * sentence merely passes through.
 *
 * Runs join when they share a baseline and a direction and stand no further
 * apart than a column gutter, so a row of separate blocks -- three drawing
 * areas printed side by side -- stays three lines while a sentence stays one.
 */
function textLines(items) {
  const rows = [];
  for (const it of items.slice().sort((a, b) => b.y - a.y)) {
    const row = rows[rows.length - 1];
    if (
      row &&
      Math.abs(row.y - it.y) < 2.5 &&
      row.dx * it.dx + row.dy * it.dy >= SAME_DIRECTION
    ) {
      row.items.push(it);
    } else {
      rows.push({ y: it.y, dx: it.dx, dy: it.dy, items: [it] });
    }
  }

  const lines = [];
  for (const row of rows) {
    row.items.sort((a, b) => a.x0 - b.x0);
    let line = null;
    for (const it of row.items) {
      if (line && it.x0 - line.x1 <= COLUMN_CHANNEL) {
        line.x1 = Math.max(line.x1, it.x1);
        line.y0 = Math.min(line.y0, it.y0);
        line.y1 = Math.max(line.y1, it.y1);
      } else {
        line = { x0: it.x0, x1: it.x1, y0: it.y0, y1: it.y1 };
        lines.push(line);
      }
    }
  }
  return lines;
}

/**
 * The items of a band grouped into the lines they were set as, top down. Unlike
 * textLines() nothing is split at a horizontal gap: inside one question the
 * label, the answer and whatever is tabbed across from it are all the same line.
 */
function groupLines(items) {
  const lines = [];
  for (const it of items.slice().sort((a, b) => b.y - a.y)) {
    const line = lines[lines.length - 1];
    if (line && Math.abs(line.y - it.y) < 2.5) {
      line.items.push(it);
      line.x0 = Math.min(line.x0, it.x0);
      line.x1 = Math.max(line.x1, it.x1);
      line.y0 = Math.min(line.y0, it.y0);
      line.y1 = Math.max(line.y1, it.y1);
    } else {
      lines.push({ y: it.y, x0: it.x0, x1: it.x1, y0: it.y0, y1: it.y1, items: [it] });
    }
  }
  for (const line of lines) line.items.sort((a, b) => a.x0 - b.x0);
  return lines;
}

/**
 * The spans of [lo, hi] that `rects` cover, merged so overlapping pieces are
 * counted once. Used to discount whatever is drawn between two lines of text
 * from the blank that appears to separate them.
 */
function mergeSpans(rects, lo, hi) {
  const spans = rects
    .map((r) => [Math.max(r.y0, lo), Math.min(r.y1, hi)])
    .filter(([a, b]) => b - a > 0.5)
    .sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const span of spans) {
    const last = out[out.length - 1];
    if (last && span[0] <= last[1]) last[1] = Math.max(last[1], span[1]);
    else out.push(span);
  }
  return out;
}

function intersects(a, b) {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
}

/**
 * Push apart any two hotspot rectangles that still overlap after band clipping
 * (a question set full width where the page is not in columns is free to reach
 * across it, so two of them can meet). Each overlapping pair is
 * separated along its axis of least penetration and the contested strip is
 * split down the middle, so neither region loses more than it has to and the
 * result is independent of the order the pairs are visited.
 */
function separateRects(parts, passes = 6) {
  for (let pass = 0; pass < passes; pass++) {
    let moved = false;
    for (let i = 0; i < parts.length; i++) {
      for (let j = i + 1; j < parts.length; j++) {
        const a = parts[i].rect;
        const b = parts[j].rect;
        if (!intersects(a, b)) continue;

        const sameOwnerSameCol =
          parts[i].owner === parts[j].owner && parts[i].column === parts[j].column;
        const upperIsA = (a.y0 + a.y1) >= (b.y0 + b.y1);
        const upper = upperIsA ? a : b;
        const lower = upperIsA ? b : a;
        const upperPart = upperIsA ? parts[i] : parts[j];
        const lowerPart = upperIsA ? parts[j] : parts[i];

        if (sameOwnerSameCol) {
          // Bands of one activity within a column: if the lower piece took a
          // panel (e.g. a reading passage), the upper question part must not
          // invade that panel; if the upper piece took a panel, the lower part
          // must stay below it; otherwise split down the middle.
          if (lowerPart.panel) {
            upper.y0 = Math.max(upper.y0, lowerPart.panel.y1);
          } else if (upperPart.panel) {
            lower.y1 = Math.min(lower.y1, upperPart.panel.y0);
          } else {
            const mid = (Math.max(a.y0, b.y0) + Math.min(a.y1, b.y1)) / 2;
            upper.y0 = Math.max(upper.y0, mid);
            lower.y1 = Math.min(lower.y1, mid);
          }
          moved = true;
          continue;
        }

        const dx = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0);
        const dy = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
        // Trimming must never push a region off its own label: an activity you
        // cannot click on its label is worse than two regions that touch.
        const anchorA = parts[i].anchor;
        const anchorB = parts[j].anchor;
        const upperAnchor = upperIsA ? anchorA : anchorB;
        const lowerAnchor = upperIsA ? anchorB : anchorA;
        const upperPanel = upperPart.panel;
        const lowerPanel = lowerPart.panel;

        if (dy <= dx) {
          const mid = (Math.max(a.y0, b.y0) + Math.min(a.y1, b.y1)) / 2;
          const uMin = lowerPanel ? Math.max(mid + HOTSPOT_GAP / 2, lowerPanel.y1) : mid + HOTSPOT_GAP / 2;
          const lMax = upperPanel ? Math.min(mid - HOTSPOT_GAP / 2, upperPanel.y0) : mid - HOTSPOT_GAP / 2;
          upper.y0 = Math.min(uMin, upper.y1, upperAnchor ? upperAnchor.y0 : Infinity);
          lower.y1 = Math.max(lMax, lower.y0, lowerAnchor ? lowerAnchor.y1 : -Infinity);
        } else {
          const mid = (Math.max(a.x0, b.x0) + Math.min(a.x1, b.x1)) / 2;
          const rightIsA = (a.x0 + a.x1) >= (b.x0 + b.x1);
          const right = rightIsA ? a : b;
          const left = rightIsA ? b : a;
          const rightAnchor = rightIsA ? anchorA : anchorB;
          const leftAnchor = rightIsA ? anchorB : anchorA;
          right.x0 = Math.min(mid + HOTSPOT_GAP / 2, right.x1, rightAnchor ? rightAnchor.x0 : Infinity);
          left.x1 = Math.max(mid - HOTSPOT_GAP / 2, left.x0, leftAnchor ? leftAnchor.x1 : -Infinity);
        }
        moved = true;
      }
    }
    if (!moved) break;
  }

  return parts.filter(
    (p) => p.rect.x1 - p.rect.x0 >= MIN_HOTSPOT && p.rect.y1 - p.rect.y0 >= MIN_HOTSPOT
  );
}

export class ActivityDetector {
  static calibrationMemoryCache = new Map();

  constructor(pdfDoc, docNameOrOptions = null) {
    this.pdfDoc = pdfDoc;
    const options = typeof docNameOrOptions === "string" ? { docName: docNameOrOptions } : (docNameOrOptions || {});
    this.docName = options.docName || null;
    this.cache = new Map();          // pageNum -> PageActivities
    this.textItemsCache = new Map(); // pageNum -> textItems[]
    this.rawOverrides = {};          // entire JSON from activity-overrides.json
    this.overrides = {};             // scoped to active doc: pageNum(string) -> { drop:[], "a": {rect:[...]}}
    this.overridesReady = Promise.resolve();
    this.markerStyles = null;        // Set of "font|size" keys, null until calibrated
    this.markerFonts = new Set();    // font names carrying labels (and numbered items)
    this.markerSize = null;
    this.calibrated = false;
    this.calibrating = null;         // in-flight promise
    // The number the page prints on itself, which is what every outside index
    // of the book -- a contents list, a publisher's activity manifest -- counts
    // in. It is not the sheet's ordinal: a cover, a half-title or an inserted
    // plate shifts one against the other for the rest of the book.
    this.folioByPage = new Map();    // pageNum -> printed page number read off the sheet
    this.folioOffset = null;         // modal (pageNum - folio), for sheets that print none
    this.enabled = false;            // false when the document has no lettered activities
    this.confidence = "none";        // "strong" | "weak" | "none"
    // Off by default. When a caller sets this to a Map, analyzePage records the
    // page structure it worked from -- the drawn paths, the panels and answer
    // blocks read off them, the cells -- so a harness can audit a region against
    // the geometry that produced it without replaying the operator list a second
    // time. Nothing in detection reads it.
    this.diagnostics = null;

    // Optional: Map<pageNum, [{ id, posx, posy }]>, the publisher's icon
    // positions for activities its manifest does not label. Set by a caller
    // that has the manifest; see anchoredActivities().
    this.anchorsByPage = null;
  }

  /* ------------------------------------------------------------------ */
  /* Setup                                                               */
  /* ------------------------------------------------------------------ */

  setDocName(docName) {
    this.docName = docName;
    if (this.rawOverrides && Object.keys(this.rawOverrides).length > 0) {
      this.overrides = this.resolveDocumentOverrides(this.rawOverrides);
    }
  }

  resolveDocumentOverrides(data) {
    if (!data || typeof data !== "object" || Array.isArray(data)) return {};

    const fingerprints = [];
    if (Array.isArray(this.pdfDoc?.fingerprints)) {
      for (const fp of this.pdfDoc.fingerprints) {
        if (typeof fp === "string" && fp.trim()) fingerprints.push(fp.trim());
      }
    }
    if (typeof this.pdfDoc?.fingerprint === "string" && this.pdfDoc.fingerprint.trim()) {
      const fp = this.pdfDoc.fingerprint.trim();
      if (!fingerprints.includes(fp)) fingerprints.push(fp);
    }

    const nameCandidates = [];
    if (typeof this.docName === "string" && this.docName.trim()) {
      const clean = this.docName.trim().split("?")[0].split("#")[0];
      const base = clean.split(/[/\\]/).pop();
      if (base) {
        const noExt = base.replace(/\.[^.]+$/, "");
        if (noExt && noExt !== base) nameCandidates.push(noExt);
        nameCandidates.push(base);
      }
    }

    // Lowest to highest precedence:
    // name without extension -> full basename -> fingerprint
    const orderedCandidates = [...nameCandidates, ...fingerprints];

    let matched = {};
    let found = false;

    for (const cand of orderedCandidates) {
      const matchKey = Object.prototype.hasOwnProperty.call(data, cand)
        ? cand
        : Object.keys(data).find((k) => k.toLowerCase() === cand.toLowerCase());
      if (matchKey && typeof data[matchKey] === "object" && !Array.isArray(data[matchKey])) {
        found = true;
        const docObj = data[matchKey];
        for (const [pageNum, pageOv] of Object.entries(docObj)) {
          if (pageOv && typeof pageOv === "object" && !Array.isArray(pageOv)) {
            matched[pageNum] = { ...(matched[pageNum] || {}), ...pageOv };
          }
        }
      }
    }

    if (found) return matched;

    // Backwards compatibility: flat format where all keys are page numbers (digits)
    const keys = Object.keys(data);
    if (keys.length > 0 && keys.every((k) => /^\d+$/.test(k))) {
      return data;
    }

    return {};
  }

  loadOverrides(source = "activity-overrides.json") {
    this.overridesReady = (async () => {
      try {
        if (source && typeof source === "object") {
          this.rawOverrides = source;
          this.overrides = this.resolveDocumentOverrides(this.rawOverrides);
          return;
        }
        const res = await fetch(source, { cache: "no-cache" });
        if (res.ok) {
          this.rawOverrides = await res.json();
          this.overrides = this.resolveDocumentOverrides(this.rawOverrides);
        } else {
          this.rawOverrides = {};
          this.overrides = {};
        }
      } catch (_) {
        this.rawOverrides = {};
        this.overrides = {};
      }
    })();
    return this.overridesReady;
  }

  /* ------------------------------------------------------------------ */
  /* Calibration Caching                                                */
  /* ------------------------------------------------------------------ */

  getCalibrationCacheKey() {
    let fp = null;
    if (Array.isArray(this.pdfDoc?.fingerprints) && this.pdfDoc.fingerprints[0]) {
      fp = this.pdfDoc.fingerprints[0];
    } else if (typeof this.pdfDoc?.fingerprint === "string" && this.pdfDoc.fingerprint.trim()) {
      fp = this.pdfDoc.fingerprint.trim();
    }
    let base = null;
    if (typeof this.docName === "string" && this.docName.trim()) {
      base = this.docName.trim().split("?")[0].split("#")[0].split(/[/\\]/).pop();
      if (base) base = base.replace(/\.[^.]+$/, "");
    }
    const total = this.pdfDoc?.numPages || 0;
    // "5_" pins the format: bump it when what calibration learns changes, so a
    // book calibrated by an older build re-reads its sample instead of showing
    // styles the current selection rules would never have kept.
    return `interaktiv_calib_5_${fp || "nofp"}_${base || "nobase"}_${total}`;
  }

  readCalibrationCache() {
    const key = this.getCalibrationCacheKey();
    if (!key) return null;
    if (ActivityDetector.calibrationMemoryCache.has(key)) {
      return ActivityDetector.calibrationMemoryCache.get(key);
    }
    try {
      if (typeof localStorage !== "undefined") {
        const raw = localStorage.getItem(key);
        if (raw) {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === "object" && Array.isArray(parsed.markerStyles)) {
            ActivityDetector.calibrationMemoryCache.set(key, parsed);
            return parsed;
          }
        }
      }
    } catch (_) {}
    return null;
  }

  saveCalibrationCache() {
    const key = this.getCalibrationCacheKey();
    if (!key) return;
    const record = {
      markerStyles: Array.from(this.markerStyles || []),
      markerSize: this.markerSize,
      markerFonts: Array.from(this.markerFonts || []),
      enabled: !!this.enabled,
      confidence: this.confidence,
      folioOffset: this.folioOffset,
    };
    ActivityDetector.calibrationMemoryCache.set(key, record);
    try {
      if (typeof localStorage !== "undefined") {
        localStorage.setItem(key, JSON.stringify(record));
      }
    } catch (_) {}
  }

  checkCache() {
    if (this.calibrated) return true;
    const cached = this.readCalibrationCache();
    if (cached) {
      this.markerStyles = new Set(cached.markerStyles);
      this.markerSize = cached.markerSize;
      this.markerFonts = new Set(cached.markerFonts);
      this.enabled = !!cached.enabled;
      this.confidence = cached.confidence || (this.enabled ? "strong" : "none");
      if (this.folioOffset === null && typeof cached.folioOffset === "number") {
        this.folioOffset = cached.folioOffset;
      }
      this.calibrated = true;
      this.calibrateStats = {
        totalDurationMs: 0.1,
        pagesSampled: 0,
        textItemsTotalMs: 0,
        tallyMs: 0,
        cached: true,
        confidence: this.confidence,
      };
      return true;
    }
    return false;
  }

  /**
   * Learn which (font, size) pairs carry the activity labels by looking for the
   * styles whose standalone lowercase letters form the longest consecutive runs
   * (a -> b -> c ...) down a column.
   *
   * How much of the book that takes is not a constant, because how much evidence
   * a page carries is not. A workbook puts a lettered list on nearly every page,
   * and fourteen pages of it settle the question outright. A textbook of running
   * prose puts two numbered questions at the foot of a section and then reads on
   * for six pages, so the same fourteen pages -- spread over three hundred --
   * may land on two of those lists and score three, under the threshold, and the
   * whole feature switches itself off for a book that is full of activities.
   *
   * So the sample widens instead of being fixed: pages are read in passes that
   * interleave with the ones already read, and the reading stops as soon as the
   * leading style's evidence is decisive -- three passes deep, because a book
   * keeps more than one scheme of labels and the rarer ones show themselves
   * slowly -- or when the budget below is spent.
   */
  async calibrate(sampleCount = SAMPLE_FIRST) {
    if (this.calibrated) return;
    if (this.checkCache()) return;
    if (this.calibrating) return this.calibrating;

    this.calibrating = (async () => {
      const t_calib_0 = performance.now();
      let workerFetchWallTimeMs = 0;
      let tallyWallTimeMs = 0;
      const total = this.pdfDoc.numPages;
      const budget = Math.max(sampleCount, Math.min(SAMPLE_LIMIT, Math.round(total * SAMPLE_SHARE)));

      // styleKey -> pageNum -> [{letter, x, y}]
      const buckets = new Map();
      const read = new Set();

      const readPage = async (pageNum) => {
        if (read.has(pageNum) || pageNum < 1 || pageNum > total) return;
        read.add(pageNum);
        let items;
        try {
          items = await this.textItems(pageNum);
        } catch (_) {
          return;
        }
        for (const it of items) {
          if (!it.label) continue;
          if (!this.hasHangingIndent(it, items)) continue;
          const key = `${it.font}|${it.size.toFixed(1)}`;
          if (!buckets.has(key)) buckets.set(key, new Map());
          const byPage = buckets.get(key);
          if (!byPage.has(pageNum)) byPage.set(pageNum, []);
          byPage.get(pageNum).push(it);
        }
      };

      // The score of every style seen so far, best first.
      const tally = () => {
        const out = [];
        for (const [key, byPage] of buckets) {
          let score = 0;
          for (const entries of byPage.values()) score += this.sequenceScore(entries);
          const cut = key.lastIndexOf("|");
          out.push({ key, score, font: key.slice(0, cut), size: parseFloat(key.slice(cut + 1)) });
        }
        // One nominal type size arrives as several: PDF.js reports the size off
        // the text matrix, so a run the designer set at 10.5pt and a run of the
        // same style optically scaled to fit its box come back 10.0 and 10.5 and
        // are counted as two styles. Split that way, one page's worth of labels
        // can fall under the bar that a second scheme has to clear, and the book
        // loses every activity set in the half of the style that lost. Sizes
        // this close in one face are one style, so their evidence is pooled and
        // the size that carries most of it names the style.
        out.sort((a, b) => b.score - a.score);
        const merged = [];
        for (const s of out) {
          const into = merged.find(
            (m) => m.font === s.font && Math.abs(m.size - s.size) < SIZE_TOLERANCE
          );
          if (into) {
            into.score += s.score;
            into.keys.push(s.key);
          } else {
            merged.push({ ...s, keys: [s.key] });
          }
        }
        merged.sort((a, b) => b.score - a.score);
        return merged;
      };

      let scored = [];
      const CONCURRENCY = 3;
      let passes = 0;
      // Each pass halves the stride, so pass n reads the pages that fall halfway
      // between the ones pass n-1 read: the sample stays spread over the whole
      // book at every depth rather than crawling forward from the front.
      for (let stride = Math.max(1, Math.floor(total / sampleCount)); ; stride = Math.floor(stride / 2)) {
        passes++;
        const pagesToRead = [];
        for (let p = 1; p <= total && read.size + pagesToRead.length < budget; p += Math.max(1, stride)) {
          if (!read.has(p)) pagesToRead.push(p);
        }

        for (let i = 0; i < pagesToRead.length; i += CONCURRENCY) {
          const batch = pagesToRead.slice(i, i + CONCURRENCY);
          const t_batch_0 = performance.now();
          await Promise.all(batch.map((p) => readPage(p)));
          workerFetchWallTimeMs += (performance.now() - t_batch_0);
          if (i + CONCURRENCY < pagesToRead.length) {
            await new Promise((resolve) => setTimeout(resolve, 0));
          }
        }

        const t_tally_0 = performance.now();
        scored = tally();
        tallyWallTimeMs += (performance.now() - t_tally_0);
        // The dominant style being decisive says nothing about the others: a
        // book that numbers its sections' questions in several styles shows the
        // rarer ones only a little at a time, so the sample always goes three
        // passes deep -- a quarter of the first stride -- before the evidence
        // is called complete.
        if (scored[0] && scored[0].score >= SAMPLE_DECISIVE && passes >= 3) break;
        if (read.size >= budget || stride <= 1) break;
      }

      const top = scored[0];
      if (!top || top.score < 4) {
        this.enabled = false;
        this.confidence = "none";
        this.calibrated = true;
        this.calibrateStats = {
          totalDurationMs: performance.now() - t_calib_0,
          pagesSampled: read.size,
          textItemsTotalMs: workerFetchWallTimeMs,
          tallyMs: tallyWallTimeMs,
          confidence: "none",
        };
        this.saveCalibrationCache();
        return;
      }

      // Every style that forms comparably strong runs is evidence of a list, but
      // a book numbers its lists at more than one level -- "a" holding "1 2 3",
      // "1." holding "a) b) c)" -- and the activities are the *outer* level. The
      // most numerous style is therefore the wrong pick: the inner level always
      // has more entries. Type size is what separates the levels, since a label
      // is never set smaller than the labels nested under it, so among the
      // styles with real evidence the largest one opens the list.
      //
      // But only within one face. Two levels of one list are one typeface set at
      // two sizes; a label in a different face is a different scheme entirely --
      // a book routinely numbers the questions under a reading passage in one
      // face and the steps of a practical in another, and neither is nested in
      // the other. Read across faces, "the largest wins" lets a style with a
      // fraction of the evidence displace the one the book actually uses, which
      // is how a book of three hundred pages came out with two dozen activities.
      const strong = scored.filter((s) => s.score >= Math.max(4, top.score * 0.5));
      // The outer level of the dominant face's list is the largest size that
      // face shows with real evidence -- not the highest-scoring one, which is
      // the inner level whenever a book nests one list in another, since the
      // inner level always has more entries. "Real evidence" is read in
      // absolute terms here: scored against the floor alone, because a book
      // whose sub-questions outnumber its activities scores the outer level
      // under any relative bar.
      const best = scored
        .filter((s) => s.font === top.font && s.score >= 4)
        .reduce((a, b) => (b.size > a.size ? b : a), top);

      // Keep the dominant style plus any secondary style that forms comparably
      // strong runs at the same (or larger) size. Anything weaker is body text
      // that happens to contain lone letters.
      const kept = [
        best,
        ...strong.filter(
          (s) => s.size >= best.size - SIZE_TOLERANCE && !s.keys.some((k) => best.keys.includes(k))
        ),
      ];

      // Beyond the dominant scheme, a book keeps others. Each kind of section
      // has its own convention -- the questions at the top of a topic, the check
      // at its foot, the test at the end -- and they are set in different faces
      // or at different sizes, with neither nested in the other. Such a scheme
      // earns its place on evidence read in absolute terms: a run of consecutive
      // labels down one column of a single page (SECONDARY_RUN of them, so a
      // numbered figure or a stray pair cannot carry it), a floor on the score
      // the whole sample gives it, and a floor on the size, since nothing asks a
      // question in type too small to read -- but a map legend will number its
      // places in 5pt.
      //
      // One kind of smaller style is *not* its own scheme: the inner level of a
      // list whose outer level is set larger in the same face. There the two
      // share their pages, the inner labels sit deeper than the outer ones by
      // more than a column's width, and keeping both would make the inner items
      // top-level activities of every page they share -- which is what the size
      // rule above is there to prevent. Where the smaller style never shares a
      // page with the larger one, nothing nests: the book simply asks its
      // questions one size down in another section, and those pages are read
      // with both styles.
      const entriesFor = (s) => {
        const merged = new Map();
        for (const k of s.keys) {
          const byPage = buckets.get(k);
          if (!byPage) continue;
          for (const [p, es] of byPage) {
            if (!merged.has(p)) merged.set(p, []);
            merged.get(p).push(...es);
          }
        }
        return merged;
      };
      const isKept = (s) => s.keys.some((k) => kept.some((t) => t.keys.includes(k)));
      const candidates = scored
        .filter((s) => !isKept(s))
        .filter((s) => s.score >= SECONDARY_SCORE && s.size >= SECONDARY_MIN_SIZE)
        .filter((s) => {
          let run = 0;
          for (const entries of entriesFor(s).values()) {
            run = Math.max(run, this.longestConsecutiveRun(entries));
          }
          return run >= SECONDARY_RUN;
        })
        .sort((a, b) => b.size - a.size);
      const accepted = [...kept];
      for (const s of candidates) {
        const es = entriesFor(s);
        const nested = accepted.some((t) => {
          if (t.font !== s.font || t.size <= s.size) return false;
          const te = entriesFor(t);
          for (const [p, ses] of es) {
            const tes = te.get(p);
            if (!tes || !ses.length) continue;
            const deeper = Math.min(...ses.map((e) => e.x));
            const outer = Math.min(...tes.map((e) => e.x));
            if (deeper > outer + COLUMN_TOLERANCE) return true;
          }
          return false;
        });
        if (!nested) accepted.push(s);
      }

      this.markerStyles = new Set(accepted.flatMap((s) => s.keys));
      this.markerSize = best.size;
      this.markerFonts = new Set(
        [...this.markerStyles].map((k) => k.slice(0, k.lastIndexOf("|")))
      );
      this.enabled = true;
      this.calibrated = true;

      // Compute detection confidence
      let pagesWithRun3 = 0;
      for (const p of read) {
        let maxRun = 0;
        for (const s of accepted) {
          const es = entriesFor(s).get(p);
          if (es && es.length) {
            const r = this.longestConsecutiveRun(es);
            if (r > maxRun) maxRun = r;
          }
        }
        if (maxRun >= 3) pagesWithRun3++;
      }

      let totalHanging = 0;
      for (const byPage of buckets.values()) {
        for (const es of byPage.values()) totalHanging += es.length;
      }
      let acceptedTotal = 0;
      for (const s of accepted) {
        for (const es of entriesFor(s).values()) acceptedTotal += es.length;
      }
      const labelShare = totalHanging > 0 ? acceptedTotal / totalHanging : 0;

      // Find runner-up among unaccepted styles, excluding nested sub-questions
      const unaccepted = scored.filter((s) =>
        !isKept(s) &&
        !accepted.some((a) => a.keys.some((k) => s.keys.includes(k))) &&
        !accepted.some((t) => {
          if (t.font !== s.font || t.size <= s.size) return false;
          const te = entriesFor(t);
          const es = entriesFor(s);
          for (const [p, ses] of es) {
            const tes = te.get(p);
            if (!tes || !ses.length) continue;
            const deeper = Math.min(...ses.map((e) => e.x));
            const outer = Math.min(...tes.map((e) => e.x));
            if (deeper > outer + COLUMN_TOLERANCE) return true;
          }
          return false;
        })
      );
      const runnerUp = unaccepted[0];
      const runnerUpScore = runnerUp ? runnerUp.score : 0;
      const margin = runnerUpScore > 0 ? best.score / runnerUpScore : Infinity;

      const isStrong = best.score >= 20 && margin >= 1.3 && pagesWithRun3 >= 3 && labelShare >= 0.10;
      this.confidence = isStrong ? "strong" : "weak";

      this.calibrateStats = {
        totalDurationMs: performance.now() - t_calib_0,
        pagesSampled: read.size,
        textItemsTotalMs: workerFetchWallTimeMs,
        tallyMs: tallyWallTimeMs,
        confidence: this.confidence,
      };
      this.saveCalibrationCache();
    })();

    return this.calibrating;
  }

  /** Number of a->b->c (or 1->2->3) increments found reading column by column. */
  sequenceScore(entries) {
    const columns = this.clusterColumns(entries);
    let score = 0;
    for (const col of columns) {
      const sorted = col.slice().sort((a, b) => b.y - a.y);
      for (let i = 1; i < sorted.length; i++) {
        if (follows(sorted[i - 1].label, sorted[i].label)) score++;
      }
    }
    return score;
  }

  /**
   * The longest strict a->b->c run found reading down any one column of
   * `entries`. Where sequenceScore() adds up every increment on the page, this
   * asks whether one column carries an unbroken list -- the shape a run of
   * questions or items has and a scattering of numbered captions does not.
   */
  longestConsecutiveRun(entries) {
    let best = 0;
    for (const col of this.clusterColumns(entries)) {
      const sorted = col.slice().sort((a, b) => b.y - a.y);
      let run = 0;
      for (let i = 0; i < sorted.length; i++) {
        run = i > 0 && follows(sorted[i - 1].label, sorted[i].label) ? run + 1 : 1;
        if (run > best) best = run;
      }
    }
    return best;
  }

  /**
   * A real activity label opens a column: it has clear space in the gutter to its
   * left and the instruction text follows at a hanging indent. That separates it
   * from the a/b/c option letters inside matching exercises, which sit mid-line
   * right after another word. The gutter test (rather than "leftmost on the line")
   * is what keeps right-column labels, whose baseline is shared with the left
   * column's body text.
   */
  hasHangingIndent(item, items) {
    return this.labelPosition(item, items).hangingIndent;
  }

  /**
   * The two halves of that test, reported separately, because the two levels of
   * a list are not held to the same standard. An activity label must open its
   * column outright. A numbered question only has to stand clear in the gutter:
   * its text may sit a whole tab stop away, or the line may be a blank for the
   * reader to fill in, and neither makes the digit any less a question number --
   * while a digit with a word right up against it on the left ("has 1 minute")
   * is prose in every book.
   */
  labelPosition(item, items) {
    let nearestLeft = -Infinity;
    const right = [];
    for (const other of items) {
      if (other === item) continue;
      // Only a run flowing the same way as the label shares its line. A rotated
      // caption whose baseline happens to cross this one is not a neighbour, and
      // treating it as one is what used to delete labels next to the audio QR
      // codes.
      if (other.dx * item.dx + other.dy * item.dy < SAME_DIRECTION) continue;
      if (Math.abs(other.y - item.y) >= 2.5) continue;
      if (other.x0 < item.x0 - 0.5) {
        // Genuinely overlapping: not a label at all.
        if (other.x1 > item.x0 + 0.5) return { clearLeft: false, hangingIndent: false };
        nearestLeft = Math.max(nearestLeft, other.x1);
      } else if (other.x0 > item.x1) {
        right.push(other);
      } else if (other.x1 > item.x1 + 0.5) {
        // The label's raw run width often includes a trailing space, so the run
        // that follows it ("2. adım" + ": ...") starts *inside* the reported box
        // while its ink stands clear of the glyphs. Starting inside but reaching
        // past the box is the line's continuation; starting inside and staying
        // there is glyph noise neither side should see.
        right.push(other);
      }
    }

    // The body a question's text makes to the right of its label. Walking the
    // row, the first prose run is the body; so is a wide run of anything else,
    // because the blank a workbook prints for the reader to write on -- the
    // dotted or ruled line a question sits above -- is not prose, and a label
    // over a blank is still a question. A narrow non-prose run -- the operator
    // between a formula's numbers, a mark -- is neither body nor wall and is
    // stepped over. A label-shaped run is stepped over as well, since a
    // question may open with a nested item of its own list ("3. a) ..."), but
    // at most two of them: a row of graph ticks is label after label and never
    // reaches text or blank. A run set off by more than the hanging indent ends
    // the walk, and so does the row.
    let hasBodyToTheRight = false;
    let cursor = item.x1;
    let stepped = 0;
    right.sort((a, b) => a.x0 - b.x0);
    for (const other of right) {
      if (other.x0 - cursor > HANGING_INDENT) break;
      if (isProse(other) || other.x1 - other.x0 >= 2 * item.size) {
        hasBodyToTheRight = true;
        break;
      }
      if (other.label && stepped >= 2) break;
      if (other.label) stepped++;
      cursor = Math.max(cursor, other.x1);
    }

    // Measure the clear space rather than testing a fixed-width window: an
    // option letter inside a matching exercise follows the previous word by
    // about a space width, while a label opening a column stands well clear of
    // whatever ends the column to its left -- which may be only a full stop.
    const clearLeft = item.x0 - nearestLeft >= item.size * GUTTER_RATIO;
    return { clearLeft, hangingIndent: clearLeft && hasBodyToTheRight };
  }

  clusterColumns(entries) {
    const sorted = entries.slice().sort((a, b) => a.x - b.x);
    const columns = [];
    let current = [];
    let anchor = null;
    for (const e of sorted) {
      if (anchor === null || Math.abs(e.x - anchor) <= COLUMN_TOLERANCE) {
        if (anchor === null) anchor = e.x;
        current.push(e);
      } else {
        columns.push(current);
        current = [e];
        anchor = e.x;
      }
    }
    if (current.length) columns.push(current);
    return columns;
  }

  /**
   * The columns a page is set in, read off its text rather than off its labels.
   *
   * Used only where there are no labels to read -- a book that titles its
   * activities instead of lettering them. The rule is deliberately the same one
   * the labelled path uses: cluster the left edges, then keep a cluster as a
   * column only where a whitespace channel is genuinely open down the page at
   * that boundary. What changes is the seed, not the test, so a page is read as
   * two columns here exactly when it would have been read as two columns had
   * its exercises been lettered.
   *
   * A cluster needs a few lines behind it: one line starting further right is an
   * indent, a quotation or a figure caption, not a column.
   */
  bodyColumns(body, contentTop, contentBottom, contentRight) {
    const lines = textLines(body).filter(
      (l) => l.y1 <= contentTop + 2 && l.y0 >= contentBottom - 2
    );
    if (!lines.length) return [];

    const starts = lines.map((l) => ({ x: l.x0, line: l }));
    const clusters = this.clusterColumns(starts)
      .filter((c) => c.length >= MIN_COLUMN_LINES)
      .sort((a, b) => Math.min(...a.map((e) => e.x)) - Math.min(...b.map((e) => e.x)));
    if (!clusters.length) return [];

    const kept = [];
    let columnLeft = -Infinity;
    for (const cluster of clusters) {
      const left = Math.min(...cluster.map((e) => e.x));
      const own = body.filter((b) => b.x0 >= columnLeft - 4);
      if (kept.length) {
        // Two columns are two columns because they run *beside* each other. A
        // label cluster is structural and sparse, so the labelled path can take
        // that for granted; line starts are neither -- every indent, hanging
        // quotation and tabbed answer starts a cluster -- and an open channel
        // alone would call each of them a column, because a boundary with text
        // on only one side of it is clear everywhere by default. So the share of
        // the height where both sides actually carry something has to be real
        // before the channel over it means anything.
        if (this.sideBySideShare(own, left - 4, contentTop, contentBottom) < COLUMN_PAIRED) continue;
        if (this.columnClearance(own, left - 4, contentTop, contentBottom) < COLUMN_CLEAR) continue;
      }
      kept.push(cluster);
      columnLeft = left;
    }
    if (!kept.length) return [];

    return kept.map((cluster, idx) => {
      const left = Math.min(...cluster.map((e) => e.x)) - 6;
      const nextLeft = idx + 1 < kept.length
        ? Math.min(...kept[idx + 1].map((e) => e.x)) - 10
        : contentRight + 6;
      return {
        x0: left,
        x1: nextLeft,
        markers: [],
        // A labelled column opens at its first label because what is printed
        // above that label -- the running header, the section banner -- belongs
        // to no activity on the page. A column read off the text has no label to
        // be above, so that reasoning does not apply to it and nothing here is
        // dead ground: the heading a page opens with may well be the first line
        // of the activity an icon is pointing at.
        opensAt: contentTop,
        labelFloor: null,
      };
    });
  }

  /* ------------------------------------------------------------------ */
  /* Primitive extraction                                                */
  /* ------------------------------------------------------------------ */

  /**
   * A name for a font that means the same thing on every page.
   *
   * PDF.js hands out `g_d0_fN` ids per *font object*, and a book assembled from
   * per-chapter exports embeds the same face many times over, so one typeface
   * arrives under dozens of ids. Calibration would then split one style's
   * evidence across all of them and find nothing, and any page whose ids were
   * not in the calibration sample would match no style at all. The metrics
   * PDF.js reports for a face are the same wherever it was embedded, so they
   * identify it across those copies; the raw id is the fallback for a run whose
   * style PDF.js did not report.
   */
  fontKey(styles, fontName) {
    const st = styles[fontName];
    if (!st) return fontName || "?";
    return `${st.fontFamily}|${st.ascent}|${st.descent}`;
  }

  parseTextContent(tc) {
    const styles = tc?.styles || {};
    const items = tc?.items || [];
    const out = [];
    for (const it of items) {
      if (!it.str || !it.str.trim()) continue;
      const tr = it.transform;
      // The text matrix is not necessarily [size, 0, 0, size, x, y]. Rotated runs
      // (the vertical "Audio 4.4" captions beside the QR codes, sidebar credits,
      // anything set on a path) carry the rotation in tr[1]/tr[2], and laying
      // their advance out along the x axis invents a box that covers space the
      // glyphs never touch -- which is how a caption two columns away ends up
      // vetoing a real label. Build the run's box along its own advance and
      // ascent axes instead and take the axis-aligned hull; for unrotated text
      // this reduces exactly to the old formula.
      const size = Math.hypot(tr[2], tr[3]) || Math.abs(tr[3]) || 10;
      const advance = Math.hypot(tr[0], tr[1]) || 1;
      const dx = tr[0] / advance;
      const dy = tr[1] / advance;
      const ux = tr[2] / size;
      const uy = tr[3] / size;
      const width = it.width || 0;
      const xs = [];
      const ys = [];
      for (const along of [0, width]) {
        for (const up of [-size * 0.25, size * 0.85]) {
          xs.push(tr[4] + dx * along + ux * up);
          ys.push(tr[5] + dy * along + uy * up);
        }
      }
      const text = it.str.trim();
      out.push({
        text,
        raw: it.str,
        label: parseLabel(text),
        font: this.fontKey(styles, it.fontName),
        // The raw PDF.js font id, which is per page rather than per document
        // (see fontKey). Useless for calibration, and exactly right for asking
        // whether two runs *on one page* were set in the same face -- which is
        // how a heading is told from the question printed above it.
        face: it.fontName || "?",
        size,
        x: tr[4],
        y: tr[5],
        dx,
        dy,
        x0: Math.min(...xs),
        x1: Math.max(...xs),
        y0: Math.min(...ys),
        y1: Math.max(...ys),
      });
    }
    return out;
  }

  setTextContent(pageNum, tc) {
    if (!tc || this.textItemsCache.has(pageNum)) return;
    this.textItemsCache.set(pageNum, this.parseTextContent(tc));
  }

  /**
   * Forget everything held for one page.
   *
   * The reader keeps every page it has looked at, which is what makes paging
   * back to one instant, and nothing there ever walks a whole book. A sweep
   * does: the raw text items alone run to tens of megabytes over a few hundred
   * sheets, and `cache` is only one of four maps that grow with every page
   * touched. Deleting the analysis but leaving the text behind -- which is what
   * the callers used to do -- released almost nothing. Callers that sweep a
   * book release each page as they leave it.
   */
  releasePage(pageNum, { keepText = false } = {}) {
    this.cache.delete(pageNum);
    // A caller that is about to analyse the same sheet again -- the reader,
    // adding anchors to a page it has just read -- keeps the text: it is the
    // expensive half to fetch and none of it is about to change.
    if (!keepText) this.textItemsCache.delete(pageNum);
    if (this.diagnostics) this.diagnostics.delete(pageNum);
    if (this.analyzeStats) this.analyzeStats.delete(pageNum);
  }

  async textItems(pageNum) {
    if (this.textItemsCache.has(pageNum)) {
      return this.textItemsCache.get(pageNum);
    }
    const page = await this.pdfDoc.getPage(pageNum);
    const tc = await page.getTextContent();
    const items = this.parseTextContent(tc);
    this.textItemsCache.set(pageNum, items);
    const vp = page.getViewport({ scale: 1, rotation: 0 });
    this.noteFolio(pageNum, items, vp.width, vp.height);
    return items;
  }

  /* ------------------------------------------------------------------ */
  /* The number the page prints on itself                                */
  /* ------------------------------------------------------------------ */

  /**
   * The folio: the page number the sheet prints in its own margin.
   *
   * It is read rather than assumed because the sheet's ordinal in the file and
   * the number printed on it are two different counts, and everything outside
   * the file -- a contents list, the publisher's manifest of which activity
   * belongs to which page -- counts in the printed one. A cover, a half-title,
   * an inserted plate or a book opened at a mid-volume extract shifts the two
   * apart, in either direction and by any amount, so no fixed offset is safe.
   *
   * A folio is a bare number alone in the head or foot margin. Restricting it
   * to the margin bands is what keeps a figure caption, a price or a year out:
   * the running text of the page never reaches into them.
   */
  readFolio(items, pageWidth, pageHeight) {
    const candidates = [];
    for (const it of items) {
      if (!/^\d{1,4}$/.test(it.text)) continue;
      // Reaching into the margin band is enough to be in it: a folio set on the
      // last baseline of the foot margin still has its ascenders above the band.
      const inFoot = it.y0 <= FOOTER_BAND;
      const inHead = it.y1 >= pageHeight - HEADER_BAND;
      if (!inFoot && !inHead) continue;
      const value = parseInt(it.text, 10);
      if (!value || it.text.length > FOLIO_MAX) continue;
      // The outer corner is where a book sets its folio; the middle of the foot
      // is its other home. Either way it stands alone, so the nearer it is to an
      // edge of the sheet the better it reads as one.
      const edge = Math.min(it.x0, pageWidth - it.x1);
      candidates.push({ value, inFoot, edge });
    }
    if (!candidates.length) return null;
    candidates.sort((a, b) => (b.inFoot ? 1 : 0) - (a.inFoot ? 1 : 0) || a.edge - b.edge);
    return candidates[0].value;
  }

  /**
   * Record what one page prints, and keep the running modal offset so a sheet
   * that prints nothing -- a full-bleed plate, a chapter opener -- can still be
   * placed. Every page the detector reads feeds this, so the mapping sharpens
   * as the book is used and costs no extra fetch.
   */
  noteFolio(pageNum, items, pageWidth, pageHeight) {
    if (this.folioByPage.has(pageNum) || !pageHeight) return;
    const folio = this.readFolio(items, pageWidth, pageHeight);
    if (folio === null) return;
    const offset = pageNum - folio;
    if (Math.abs(offset) > FOLIO_SPAN) return;
    this.folioByPage.set(pageNum, folio);

    const votes = new Map();
    for (const [p, f] of this.folioByPage) {
      const o = p - f;
      votes.set(o, (votes.get(o) || 0) + 1);
    }
    let best = null;
    for (const [o, n] of votes) {
      if (!best || n > best.n || (n === best.n && Math.abs(o) < Math.abs(best.o))) best = { o, n };
    }
    this.folioOffset = best ? best.o : null;
  }

  /**
   * The printed page number of a sheet: what it prints if it prints one, else
   * what the rest of the book says it should print. Null while nothing has been
   * read yet, so a caller can tell "not known" from "page 1".
   */
  printedPageFor(pageNum) {
    if (this.folioByPage.has(pageNum)) return this.folioByPage.get(pageNum);
    if (this.folioOffset === null) return null;
    return pageNum - this.folioOffset;
  }

  /**
   * Everything the page draws that is not text, recovered by replaying the CTM
   * over the operator list: the placed images, and the vector paths.
   *
   * The paths are what a workbook rules its answers on -- the writing lines, the
   * table grid, the panel an answer box is set in -- and they are the only trace
   * of that furniture, since the PDF is untagged and carries no annotations. The
   * two are kept apart because they are used for different questions: images
   * take part in the page's layout, paths never do (see analyzePage).
   */
  async drawnMatter(pageNum, pdfjsLib, pageArea) {
    const page = await this.pdfDoc.getPage(pageNum);
    const ops = await page.getOperatorList();
    const OPS = pdfjsLib.OPS;
    const names = {};
    for (const k in OPS) names[OPS[k]] = k;

    const mul = (a, b) => [
      a[0] * b[0] + a[2] * b[1],
      a[1] * b[0] + a[3] * b[1],
      a[0] * b[2] + a[2] * b[3],
      a[1] * b[2] + a[3] * b[3],
      a[0] * b[4] + a[2] * b[5] + a[4],
      a[1] * b[4] + a[3] * b[5] + a[5],
    ];
    // A rect in device space from the four corners of a box in path space: the
    // CTM may rotate or flip, so the axis-aligned hull of the corners is the
    // only safe reading of it.
    const hull = (m, x0, y0, x1, y1) => {
      const pts = [[x0, y0], [x1, y0], [x0, y1], [x1, y1]].map(([u, v]) => [
        m[0] * u + m[2] * v + m[4],
        m[1] * u + m[3] * v + m[5],
      ]);
      const xs = pts.map((c) => c[0]);
      const ys = pts.map((c) => c[1]);
      return {
        x0: Math.min(...xs), x1: Math.max(...xs),
        y0: Math.min(...ys), y1: Math.max(...ys),
      };
    };

    let ctm = [1, 0, 0, 1, 0, 0];
    const stack = [];
    const images = [];
    const backdrops = [];
    const paths = [];

    for (let i = 0; i < ops.fnArray.length; i++) {
      const fn = names[ops.fnArray[i]];
      const args = ops.argsArray[i];
      if (fn === "save") {
        stack.push(ctm.slice());
      } else if (fn === "restore") {
        ctm = stack.pop() || ctm;
      } else if (fn === "transform") {
        ctm = mul(ctm, args);
      } else if (fn && fn.startsWith("paintImage")) {
        const r = hull(ctm, 0, 0, 1, 1);
        // Full-bleed background art is kept apart rather than dropped: it must
        // not take part in the page's layout, where it would swallow every
        // column at once, but it is still a picture, and a title standing on it
        // is that picture's title (see flowContent).
        if (rectArea(r) < pageArea * 0.65) images.push(r);
        else backdrops.push(r);
      } else if (fn === "constructPath") {
        // pdf.js hands the path's own bounding box along with its segments, so
        // the shape never has to be walked: [minX, minY, maxX, maxY] in the
        // space the path was built in.
        const box = args && args[2];
        if (!box || box.length < 4) continue;
        // What is done with the path decides whether it is drawn at all. A
        // clipping path bounds something else and is never ink of its own.
        let paint = null;
        for (let j = i + 1; j < Math.min(i + 5, ops.fnArray.length); j++) {
          const n = names[ops.fnArray[j]];
          if (n && /^(close)?(eo)?(fill|stroke|fillStroke)$/i.test(n)) { paint = n; break; }
          if (n === "clip" || n === "eoClip" || n === "endPath") { paint = null; break; }
        }
        if (!paint) continue;
        const r = hull(ctm, box[0], box[1], box[2], box[3]);
        const w = r.x1 - r.x0;
        const h = r.y1 - r.y0;
        if (w <= 0 && h <= 0) continue;
        if (rectArea(r) >= pageArea * 0.65) continue;
        r.stroked = /stroke/i.test(paint);
        paths.push(r);
      }
    }
    return { images, backdrops, paths };
  }

  /* ------------------------------------------------------------------ */
  /* Detection                                                           */
  /* ------------------------------------------------------------------ */

  /**
   * Everything about a page that is true before any activity is identified:
   * what is printed on it, what is drawn on it, and how it is laid out.
   *
   * This is separate from finding the activities because the two questions have
   * different answers in different books. A page whose exercises are lettered
   * yields its activities from its labels; a page in a book that titles its
   * activities descriptively has no labels to yield anything, and used to be
   * abandoned right here -- `analyzePage` returned empty as soon as the label
   * filter came back empty, before a single column, strip or panel had been
   * measured. The page structure is the same either way, and anchoredActivities()
   * needs it, so it is computed first and independently of any label.
   */
  async pageStructure(pageNum, pdfjsLib) {
    const page = await this.pdfDoc.getPage(pageNum);
    const vp = page.getViewport({ scale: 1, rotation: 0 });
    const pageWidth = vp.width;
    const pageHeight = vp.height;

    const items = await this.textItems(pageNum);
    this.noteFolio(pageNum, items, pageWidth, pageHeight);
    // Anything that could open an item of a list: a label glyph standing clear of
    // the gutter with its text at a hanging indent. Which level of the list each
    // one opens is decided below, from its style and where it sits.
    for (const it of items) {
      const pos = it.label ? this.labelPosition(it, items) : null;
      it.opensItem = pos ? pos.hangingIndent : false;
      it.clearLeft = pos ? pos.clearLeft : false;
    }
    const labels = items.filter(
      (it) => it.opensItem && this.markerStyles.has(`${it.font}|${it.size.toFixed(1)}`)
    );
    const contentTop = pageHeight - HEADER_BAND;
    const contentBottom = FOOTER_BAND;
    const body = items.filter((it) => it.y1 <= contentTop + 2 && it.y0 >= contentBottom - 2);

    // ---- Columns and nesting ------------------------------------------
    // Label x-positions cluster, but a cluster further right is a second
    // *column* only when a whitespace channel actually separates the two down
    // the page. Where the text of the cluster to its left runs straight through
    // that x instead, it is no column at all: it is a deeper level of the same
    // list -- "a) b) c)" indented under "1." -- and those labels are sub-items
    // of the activity that contains them rather than activities of their own.
    // Some books set both levels in one style, so the alphabet cannot tell them
    // apart and only the page geometry can.
    const clusters = this.clusterColumns(labels);
    clusters.sort((a, b) => Math.min(...a.map((m) => m.x)) - Math.min(...b.map((m) => m.x)));

    const leaders = [];
    const nestedLabels = new Set();
    let columnLeft = -Infinity;
    for (const cluster of clusters) {
      const left = Math.min(...cluster.map((m) => m.x));
      // Only what belongs to the column being split can say whether this is a
      // gutter. Text in a column further left ends well before either candidate
      // and would leave a wide gap here whatever this is, vouching for every
      // indent on the page as a column of its own.
      const own = body.filter((b) => b.x0 >= columnLeft - 4);
      const clearance = leaders.length
        ? this.columnClearance(own, left - 4, contentTop, contentBottom)
        : 1;
      if (clearance >= COLUMN_CLEAR) {
        leaders.push(cluster);
        columnLeft = left;
      } else {
        for (const m of cluster) nestedLabels.add(m);
      }
    }
    const markers = leaders.flat();

    const contentRight = Math.max(pageWidth - 30, ...body.map((it) => it.x1));
    let columns = leaders.map((cl, idx) => {
      const left = Math.min(...cl.map((m) => m.x)) - 6;
      const nextLeft = idx + 1 < leaders.length
        ? Math.min(...leaders[idx + 1].map((m) => m.x)) - 10
        : contentRight + 6;
      // Where the column opens, and how far down its own labels reach. A
      // labelled column opens at its first label and must survive at least as
      // far as its last; a column derived from the text alone (below) has no
      // labels to protect and says so.
      return {
        x0: left,
        x1: nextLeft,
        markers: cl,
        opensAt: Math.max(...cl.map((m) => m.y1 + 2)),
        labelFloor: Math.min(...cl.map((m) => m.y0)),
      };
    });

    // A page whose activities are titled rather than lettered has no labels to
    // cluster, so it had no columns, no strips and no cells -- the layout was
    // never measured at all. Its text still sets to a grid, so where there is
    // nothing to read the columns off, they are read off the text itself: the
    // left edges of the lines, clustered and then put to exactly the same
    // whitespace-channel test the label clusters face.
    if (!columns.length) {
      columns = this.bodyColumns(body, contentTop, contentBottom, contentRight);
    }

    // ---- Page structure -----------------------------------------------
    // The two-column grid is not a property of the page, only of parts of it:
    // a full-width photo strip or dialogue breaks the grid and the columns
    // resume underneath. Segment the page into horizontal strips first, each
    // carrying the column structure that actually holds over its own height,
    // instead of assuming one grid for the whole page.
    let images = [];
    let backdrops = [];
    let paths = [];
    try {
      const inContent = (r) =>
        r.y1 > contentBottom && r.y0 < contentTop && r.x1 > 0 && r.x0 < pageWidth;
      const drawn = await this.drawnMatter(pageNum, pdfjsLib, pageWidth * pageHeight);
      images = drawn.images.filter(inContent);
      backdrops = drawn.backdrops.filter(inContent);
      paths = drawn.paths.filter(inContent);
    } catch (err) {
      console.warn(`Activity drawing scan skipped for page ${pageNum}:`, err);
    }

    // The space a question is answered in: the ruled lines, the empty cells of
    // a table, the panel an answer box is set in. It is found once for the page
    // and handed to every region that grows into it.
    const solutions = this.solutionBlocks(paths, body);
    // And the panels of text it draws: the block a question introduces is taken
    // whole or left alone, never sliced down the middle (see textPanels).
    const panels = this.textPanels(paths, body);

    // Lines, not runs, decide where a column's own flow begins and ends; images
    // count as content of whichever column they sit in.
    const lines = [...textLines(body), ...images];
    const strips = this.segmentStrips([...body, ...images], lines, columns, contentTop, contentBottom);

    // A cell is one column of one strip, and the cells tile the content box.
    // They are read block by block (XY-cut), column by column within each block,
    // top to bottom within a column. Strips shape where a cell reaches, while
    // grouping strips with matching column layout preserves reading order across
    // horizontal dividing strips.
    const blocks = [];
    let currentBlock = [];

    const sameLayout = (s1, s2) => {
      if (!s1 || !s2) return false;
      if (s1.dead !== s2.dead) return false;
      if (s1.cells.length !== s2.cells.length) return false;
      for (let k = 0; k < s1.cells.length; k++) {
        const c1 = s1.cells[k];
        const c2 = s2.cells[k];
        if (c1.column !== c2.column) return false;
        if (Math.abs(c1.x0 - c2.x0) > 1 || Math.abs(c1.x1 - c2.x1) > 1) return false;
      }
      return true;
    };

    for (const strip of strips) {
      if (!currentBlock.length || sameLayout(currentBlock[currentBlock.length - 1], strip)) {
        currentBlock.push(strip);
      } else {
        blocks.push(currentBlock);
        currentBlock = [strip];
      }
    }
    if (currentBlock.length) blocks.push(currentBlock);

    const cells = [];
    for (let blockIndex = 0; blockIndex < blocks.length; blockIndex++) {
      const block = blocks[blockIndex];
      const blockCells = [];
      for (const strip of block) {
        const dead = strip.dead;
        const shared = strip.cells.length > 1;
        for (const span of strip.cells) {
          blockCells.push({
            x0: span.x0,
            x1: span.x1,
            column: span.column,
            shared,
            top: strip.top,
            bottom: strip.bottom,
            dead,
            blockIndex,
            markers: [],
          });
        }
      }
      blockCells.sort((a, b) => a.column - b.column || b.top - a.top);
      cells.push(...blockCells);
    }

    // A strip boundary marks where *some* column opens, so it also falls across
    // columns whose geometry does not change there. Fuse those back together, or
    // an activity would be split into two stacked hotspots by a boundary that
    // belongs to the column beside it.
    for (let i = cells.length - 1; i > 0; i--) {
      const above = cells[i - 1];
      const cell = cells[i];
      if (
        above.blockIndex === cell.blockIndex &&
        above.column === cell.column &&
        above.dead === cell.dead &&
        above.shared === cell.shared &&
        Math.abs(above.x0 - cell.x0) < 0.5 &&
        Math.abs(above.x1 - cell.x1) < 0.5 &&
        Math.abs(above.bottom - cell.top) < 0.5
      ) {
        above.bottom = cell.bottom;
        cells.splice(i, 1);
      }
    }

    for (const m of markers) {
      const cell = this.cellFor(cells, m, contentTop, contentBottom);
      if (cell) cell.markers.push(m);
    }
    return {
      pageNum, pageWidth, pageHeight,
      items, body, labels, markers, nestedLabels,
      contentTop, contentBottom,
      columns, cells, lines,
      images, backdrops, paths, panels, solutions,
    };
  }

  /**
   * The activities a page's own labels declare: the longest valid run of
   * labels, one activity each.
   *
   * Narrows `structure.cells` to the markers that survived the run check,
   * because the band walk in buildRegions() reads the activities off the cells.
   */
  labelledActivities(structure) {
    const { pageNum, cells } = structure;
    // ---- Reading order + sequence validation -------------------------
    const ordered = [];
    cells.forEach((cell, cellIndex) => {
      cell.markers.sort((a, b) => b.y - a.y);
      for (const m of cell.markers) ordered.push({ marker: m, cell, cellIndex });
    });

    const kept = this.longestLabelRun(ordered);
    if (!kept.length) {
      for (const cell of cells) cell.markers = [];
      return [];
    }
    const keptMarkers = new Set(kept.map((e) => e.marker));
    for (const cell of cells) cell.markers = cell.markers.filter((m) => keptMarkers.has(m));

    // ---- Seed regions ------------------------------------------------
    // Walking the cells in reading order, an activity owns the band from its own
    // label down to the next label in the same cell, and then every following
    // cell up to the one that holds the next label. That is what lets a question
    // whose text runs on past the foot of its column be read at the top of the
    // next one, instead of that continuation being handed to whichever activity
    // opens there. The band is where the question may reach, not what it covers:
    // flowContent() decides which of what is printed there is the question.
    // A page often carries two independent lists -- the left column running
    // "d e f g" while the right column starts a new section back at "a", or a
    // boxed exercise numbered "1. 2." above a full-width section that also
    // starts at "1.". Those are separate questions and must stay separately
    // clickable, so the id names the region, not the label: everything keyed by
    // id (the hotspot elements, the hover highlight, the focus animation,
    // next/previous stepping) would otherwise treat the two as one activity.
    // The label itself is left exactly as the book prints it.
    const labelCounts = new Map();
    const activities = kept.map((entry) => {
      const m = entry.marker;
      const seen = (labelCounts.get(m.text) || 0) + 1;
      labelCounts.set(m.text, seen);
      const act = {
        id: seen === 1 ? `p${pageNum}-${m.text}` : `p${pageNum}-${m.text}-${seen}`,
        label: m.text,
        pageNum,
        column: entry.cell.column,
        bands: [],
        marker: m,
        rect: { x0: m.x0, y0: m.y0, x1: m.x1, y1: m.y1 },
        members: [],
        items: [],
      };
      return act;
    });
    return activities;
  }

  /**
   * Activities the page does not label, placed from where the publisher put
   * their icon.
   *
   * The publisher's manifest titles activities by label in the English ELT
   * books ("42/a") but descriptively in the Turkish subject books ("Kontrol
   * Noktası Çözümlü Soru 1"), so there is no letter to find on the sheet and
   * nothing for the label path to detect -- those activities used to degrade to
   * a small pin at the icon's coordinates. What the manifest does give is
   * `posx`/`posy`, the point beside the activity where its own reader draws the
   * icon, and that is enough to say which part of the page is being pointed at.
   *
   * The region is then grown by exactly the same chain a lettered activity goes
   * through, not by a second set of rules: the anchor is resolved to a real item
   * of text on the page, that item is placed in a cell as though it were a
   * label, and buildRegions() does the rest. So an anchored region takes a
   * referenced panel whole, includes the space the answer is written in, and
   * keeps its pieces separate, because those are properties of that chain.
   *
   * @param {object} structure     from pageStructure()
   * @param {object[]} anchors     `{ id, posx, posy }`, percentages of the sheet
   *                               with the origin at its top-left corner
   * @returns {object[]} activities, already placed in `structure.cells`
   */
  anchoredActivities(structure, anchors) {
    const { pageWidth, pageHeight, body, cells, contentTop, contentBottom } = structure;
    if (!anchors || !anchors.length || !cells.length) return [];

    const prose = body.filter(isProse);
    if (!prose.length) return [];

    const placed = [];
    const taken = new Set(structure.cells.flatMap((c) => c.markers));
    for (const anchor of anchors) {
      if (typeof anchor.posx !== "number" || typeof anchor.posy !== "number") continue;
      // The manifest measures from the top-left corner of the sheet in percent;
      // the page is in PDF user space, which counts up from the bottom-left.
      const ax = (anchor.posx / 100) * pageWidth;
      const ay = pageHeight - (anchor.posy / 100) * pageHeight;

      // 1. Pick the cell containing the anchor
      const cell = this.cellFor(cells, { x0: ax, x1: ax, y: ay }, contentTop, contentBottom);
      if (!cell || cell.dead) continue;

      // 2. Lines within this cell
      const cellProse = prose.filter(
        (it) => (it.x0 + it.x1) / 2 >= cell.x0 - 2 && (it.x0 + it.x1) / 2 <= cell.x1 + 2
      );
      if (!cellProse.length) continue;
      const cellLines = groupLines(cellProse);

      // 3. Find marker within the cell
      const marker = this.anchorMarker(cellLines, ax, ay, pageWidth);
      if (!marker || taken.has(marker)) continue;

      // Do not place an anchored marker if an activity in this cell already starts on this baseline
      if (cell.markers.some((m) => Math.abs(m.y - marker.y) < 5)) continue;

      taken.add(marker);
      placed.push({
        // The publisher's own id for the entry, so two activities anchored to
        // the same sheet are two activities with two hotspots -- never merged,
        // and never sharing an id with a lettered one.
        id: `p${structure.pageNum}-oge-${anchor.id}`,
        label: null,
        anchored: true,
        pageNum: structure.pageNum,
        column: cell.column,
        bands: [],
        marker,
        rect: { x0: marker.x0, y0: marker.y0, x1: marker.x1, y1: marker.y1 },
        members: [],
        items: [],
      });
      cell.markers.push(marker);
    }

    // The band walk reads each cell top to bottom, and an anchored marker may
    // have landed above a lettered one; re-sorting keeps one activity's band
    // ending where the next one starts, whichever kind either is.
    for (const cell of cells) cell.markers.sort((a, b) => b.y - a.y);
    return placed;
  }

  /**
   * The item of text an icon at (ax, ay) is pointing at: the leftmost word of
   * the nearest line at or below it.
   *
   * Standing in for the label glyph a lettered activity would have, it has to be
   * a real item on the page rather than a synthetic point, because everything
   * downstream measures against it -- the type size the question is set in, the
   * baseline the flow reads down from, the left edge its text hangs off.
   */
  anchorMarker(lines, ax, ay, pageWidth) {
    let best = null;
    let bestCost = Infinity;
    for (const line of lines) {
      // Lines that start to the right of the anchor by more than a margin are
      // in another column; lines well above it are what came before.
      const below = ay - line.y;
      if (below < -ANCHOR_ABOVE) continue;
      const across = Math.abs(line.x0 - ax) / pageWidth;
      const cost = Math.max(below, 0) + across * pageWidth * 0.25;
      if (cost < bestCost) {
        bestCost = cost;
        best = line;
      }
    }
    return best ? best.items[0] : null;
  }

  /**
   * Turn a page's activities into regions: bands, then the content of each
   * band, then one padded rect per band, de-overlapped.
   *
   * Shared by labelled and anchored activities alike, which is the point.
   * "Take the panel whole", "include the space the answer is written in" and
   * "keep the pieces of one activity separate" are properties of this chain, so
   * an anchored activity inherits them by construction instead of through a
   * second implementation that would have to be kept in step with this one.
   */
  buildRegions(structure, activities) {
    const {
      pageNum, pageWidth, pageHeight, body, markers, nestedLabels,
      contentTop, contentBottom, columns, cells, lines,
      images, backdrops, panels, solutions,
    } = structure;

    // ---- Bands ---------------------------------------------------------
    // Walking the cells in reading order, an activity owns the band from its own
    // marker down to the next marker in the same cell, and then every following
    // cell up to the one that holds the next marker. That is what lets a
    // question whose text runs on past the foot of its column be read at the top
    // of the next one, instead of that continuation being handed to whichever
    // activity opens there. The band is where the question may reach, not what
    // it covers: flowContent() decides which of what is printed there is the
    // question.
    const byMarker = new Map();
    for (const act of activities) {
      act.bands = [];
      byMarker.set(act.marker, act);
    }

    let running = null;
    let runningBlock = null;
    for (const cell of cells) {
      if (cell.dead) continue;
      if (cell.blockIndex !== runningBlock) {
        running = null;
        runningBlock = cell.blockIndex;
      }
      const cellMarkers = cell.markers;
      if (!cellMarkers.length) {
        if (running && cell.column > running.column) {
          running.bands.push({
            x0: cell.x0, x1: cell.x1, column: cell.column, shared: cell.shared,
            top: cell.top, bottom: cell.bottom, own: false, items: [],
          });
        }
        continue;
      }
      const firstTop = cellMarkers[0].y1 + 2;
      if (running && cell.column > running.column && cell.top - firstTop > 1) {
        running.bands.push({
          x0: cell.x0, x1: cell.x1, column: cell.column, shared: cell.shared,
          top: cell.top, bottom: firstTop, own: false, items: [],
        });
      }
      cellMarkers.forEach((m, i) => {
        const act = byMarker.get(m);
        if (!act) return;
        act.bands.push({
          x0: cell.x0,
          x1: cell.x1,
          column: cell.column,
          shared: cell.shared,
          top: m.y1 + 2,
          bottom: i + 1 < cellMarkers.length ? cellMarkers[i + 1].y1 + 2 : cell.bottom,
          own: true,
          items: [],
        });
        running = act;
        runningBlock = cell.blockIndex;
      });
    }

    // ---- Content assignment -------------------------------------------
    // One pass, most-overlap wins. Because the bands tile the content box below
    // the first marker, nothing inside it can be orphaned the way it was when a
    // block had to overlap a column band horizontally to be seen at all.
    //
    // Which band a run falls in is only the first half of the answer, though. A
    // band is a slice of the page, and a region is a question: everything the
    // question is set in belongs to it, and everything merely printed in the
    // same slice of paper -- the photo it refers to, the dialogue underneath it,
    // the section banner beside it -- does not. So the band collects candidates
    // and flowContent() keeps the ones that read as the question's own text
    // (§ "Regions" below). Images are never candidates at all: a picture is
    // something a question points at, never part of its wording. They still
    // shape the page in segmentStrips(), which is a question about layout.
    const allBands = [];
    for (const act of activities) for (const band of act.bands) allBands.push({ act, band });

    for (const it of body) {
      const target = this.bestBandFor(it, allBands);
      if (!target) continue;
      target.band.items.push(it);
    }

    // ---- Regions: one padded rect per band, then de-overlap -----------
    // Each band becomes its own hotspot rect, clipped to the band vertically so
    // an activity can never reach over the label above or below it, and to its
    // column horizontally wherever the page is in columns at that height. A rect
    // in a full-width strip is free to span the page, so whatever still collides
    // afterwards is pushed apart by separateRects().
    let parts = [];
    for (const act of activities) {
      act.parts = [];
      const ownBand = act.bands[0];
      // The label's own column, not the cell's: a cell widened over a column
      // that has not opened yet keeps the index of its leftmost column, and the
      // flow of a question set in the right half of one is still the width of
      // the right column.
      const ownColumn =
        columns.find((c) => c.markers.includes(act.marker)) || columns[act.column];
      for (const band of act.bands) {
        const flow = this.flowContent(
          act, band, band === ownBand ? ownColumn : columns[band.column], lines, panels, [...images, ...backdrops]
        );
        act.members.push(...flow.items);
        let content = band === ownBand
          ? unionRect(flow.rect, act.rect)      // seed rect is the label glyph
          : flow.rect;
        if (!content) continue;
        // The dialogue, passage or boxed text the question introduces is part
        // of what was asked, and it is drawn as one object: the region covers
        // all of it or none of it.
        const panel = this.panelFor(act, band, content, panels, markers, flow.items);
        // What the question is set in, as opposed to the frame drawn round it.
        // The padding below is room around text, and this is the text it is
        // measured from.
        let bare = content;
        content = unionRect(content, panel);
        // A workbook asks a question and then leaves the space to answer it in.
        // That space is part of what was asked, so the region grows into it --
        // down over the writing lines under the question, and out over the
        // empty cells of the row it is answered on.
        const solution = this.solutionFor(
          act, band, band === ownBand ? ownColumn : columns[band.column],
          content, solutions, body, markers
        );
        bare = unionRect(bare, solution);
        content = unionRect(content, solution);
        // Where the page really is in columns at this height, a region stays in
        // its own column: a panel with a soft edge, or a photo bleeding a few
        // points past the gutter, must not drag the region over its neighbour --
        // the neighbour is then pushed back off its own label to make room.
        // Where the strip is one wide cell there is nothing to stay clear of.
        const topLimit = band.own && panel ? Math.max(band.top, panel.y1) : band.top;
        const leftLimit = band.shared ? band.x0 - PAD : 0;
        const rightLimit = band.shared ? band.x1 + PAD : pageWidth;
        const rect = {
          x0: clamp(Math.max(content.x0 - PAD, leftLimit), 0, pageWidth),
          x1: clamp(Math.min(content.x1 + PAD, rightLimit), 0, pageWidth),
          y0: clamp(Math.max(content.y0 - PAD, band.bottom), contentBottom - PAD, pageHeight),
          y1: clamp(Math.min(content.y1 + PAD, topLimit), 0, contentTop + PAD),
        };
        // The breathing room a region is given is room around *text*. A drawn
        // frame is already the edge the reader sees, so padding out past it buys
        // nothing and can reach over the gutter into the column beside it.
        if (panel) {
          rect.x0 = bare.x0 < panel.x0 ? Math.max(rect.x0, bare.x0 - PAD) : Math.min(rect.x0, panel.x0);
          rect.x1 = bare.x1 > panel.x1 ? Math.min(rect.x1, bare.x1 + PAD) : Math.max(rect.x1, panel.x1);
          rect.y0 = Math.max(rect.y0, bare.y0 < panel.y0 ? bare.y0 - PAD : panel.y0);
          rect.y1 = Math.max(rect.y1, bare.y1 > panel.y1 ? bare.y1 + PAD : panel.y1);
        }
        if (rect.x1 - rect.x0 < MIN_HOTSPOT || rect.y1 - rect.y0 < MIN_HOTSPOT) continue;
        // A genuine spill into the *next column* is at least a few lines tall; a
        // shallow strip there is the section banner sitting above that column's
        // first label, which belongs to no activity. Content continuing straight
        // down the same column is never in doubt, so it is not held to this.
        if (!band.own) {
          const overlap = Math.min(band.x1, ownBand.x1) - Math.max(band.x0, ownBand.x0);
          const narrower = Math.min(band.x1 - band.x0, ownBand.x1 - ownBand.x0);
          const sameFlow = overlap > 0.5 * narrower;
          if (!sameFlow && rect.y1 - rect.y0 < MIN_CONTINUATION) continue;
        }
        parts.push({
          owner: act,
          column: band.column,
          rect,
          anchor: band === ownBand ? { ...act.rect } : null,
          panel: panel ? { ...panel } : null,
        });
      }
    }

    parts = this.mergeStackedParts(parts);
    parts = separateRects(parts);
    for (const part of parts) part.owner.parts.push(part.rect);

    // ---- Sub-questions, headline --------------------------------------
    for (const act of activities) {
      // The label glyph is the fallback region for an activity whose bands ended
      // up empty, so every activity keeps a clickable target.
      if (!act.parts.length) act.parts = [{ ...act.rect }];
      // Parts are deliberately *not* merged into one bounding box: an activity
      // that continues in the next column keeps one independent region per
      // piece, so zooming, hit-testing and highlighting all operate on the piece
      // the reader actually pointed at instead of a union that spans the gutter
      // and covers unrelated activities in between. `rect` is just the primary
      // (label-bearing) piece, kept for callers that want a single anchor.
      act.rect = { ...act.parts[0] };
      act.items = this.detectSubItems(act, pageNum, nestedLabels, solutions, body, markers);
      act.headline = this.buildHeadline(act);
      delete act.bands;
      delete act.marker;
      delete act.members;
    }
    return activities;
  }

  /**
   * The publisher's unlabelled entries for one sheet, if a caller has supplied
   * them.
   *
   * The detector never fetches anything itself -- it reads a PDF -- so anchors
   * are handed to it: `anchorsByPage`, keyed by sheet ordinal, is filled by the
   * viewer from the manifest it has already loaded, and by the baker from the
   * manifest on disk. A detector with no anchors behaves exactly as it did.
   */
  anchorsFor(pageNum) {
    if (!this.anchorsByPage) return [];
    return this.anchorsByPage.get(pageNum) || [];
  }

  async analyzePage(pageNum, pdfjsLib) {
    if (this.cache.has(pageNum)) return this.cache.get(pageNum);
    const t_an0 = performance.now();
    await Promise.all([this.calibrate(), this.overridesReady]);

    const empty = async () => {
      const page = await this.pdfDoc.getPage(pageNum);
      const vp = page.getViewport({ scale: 1, rotation: 0 });
      const blank = {
        pageNum, pageWidth: vp.width, pageHeight: vp.height,
        columns: [], activities: [],
        confidence: this.confidence,
      };
      this.cache.set(pageNum, blank);
      return blank;
    };
    if (!this.enabled && !this.anchorsFor(pageNum).length) return empty();

    const structure = await this.pageStructure(pageNum, pdfjsLib);
    const activities = (this.confidence === "none") ? [] : this.labelledActivities(structure);
    // Entries the publisher placed on this sheet that no label on it accounts
    // for. They are added after the label run is settled, so an anchor can never
    // change which letters were accepted; and they go through buildRegions()
    // with the lettered ones in one set, so the de-overlap pass keeps an
    // anchored region out of a lettered one's space -- while the two stay
    // separate activities with separate ids, as two activities must.
    const anchors = this.anchorsFor(pageNum);
    if (anchors.length) {
      activities.push(...this.anchoredActivities(structure, anchors));
    }
    if (!activities.length) return empty();
    this.buildRegions(structure, activities);

    if (this.diagnostics) {
      this.diagnostics.set(pageNum, {
        body: structure.body,
        images: structure.images,
        backdrops: structure.backdrops,
        paths: structure.paths,
        panels: structure.panels,
        solutions: structure.solutions,
        cells: structure.cells,
        markers: structure.markers,
        contentTop: structure.contentTop,
        contentBottom: structure.contentBottom,
      });
    }

    const result = {
      pageNum,
      pageWidth: structure.pageWidth,
      pageHeight: structure.pageHeight,
      columns: structure.columns,
      activities: this.applyOverrides(pageNum, activities),
      confidence: this.confidence,
    };
    this.analyzeStats = this.analyzeStats || new Map();
    this.analyzeStats.set(pageNum, performance.now() - t_an0);
    this.cache.set(pageNum, result);
    return result;
  }


  /**
   * Split the content box into horizontal strips, each carrying the column
   * structure that genuinely holds over its own height.
   *
   * A column is not a property of the page, only of the part of it that the
   * column actually occupies -- it has a bottom as well as a top, and both are
   * measured here. Two things end a column upwards, and both have to agree
   * before the space above it is treated as one wide region:
   *
   *   1. the column has not opened yet -- there is no label above this height to
   *      start it, so nothing here can be the top of that column's flow; and
   *   2. no whitespace channel separates it from the column to its left, so
   *      what is drawn here is one object spanning the page rather than two
   *      columns of content.
   *
   * Both are needed. Reading the page as one fixed grid strands a full-width
   * figure (no column band reaches it) or hands it to whichever activity opens
   * the next column further down. Reading it as one wide region whenever the
   * next column has not opened does the opposite damage: a left column whose
   * numbered list simply continues on the right would have that continuation
   * sliced up by the horizontal bands of the activities beside it.
   *
   * Below a column the same two questions are asked again, of `closesAt` rather
   * than `opensAt`: where the column's own flow has ended and the page is no
   * longer in columns there, the space belongs to the region beside it. Without
   * that, a column band runs on to the folio and its last activity keeps
   * swallowing whatever is printed underneath -- typically the right half of a
   * full-width section whose own activities are clipped to the left column in
   * exchange.
   *
   * Strips shorter than MIN_STRIP are absorbed into their taller neighbour, so
   * neither a sliver between two columns opening a fraction of a point apart nor
   * a single line of whitespace can shred a region into stacked hotspots.
   */
  segmentStrips(blocks, lines, columns, contentTop, contentBottom) {
    const opensAt = columns.map((col) => col.opensAt);
    // Only a column that can be absorbed into the one on its left has a closing
    // height that means anything: the leftmost column is never merged away, so
    // asking where its flow ends would just cut strips nothing acts on.
    const closesAt = columns.map((col, i) => {
      if (i === 0) return contentBottom;
      // A column lives at least as far down as its own last label: the label is
      // drawn there and opens an item of that column's list, so however little
      // else the column carries, the space its labels occupy is never merged
      // away -- that would drop them into a cell they do not belong to and hand
      // their bands to the flow beside them.
      const flowEnd = this.columnFlowEnd(lines, col, opensAt[i], contentBottom);
      return col.labelFloor === null ? flowEnd : Math.min(flowEnd, col.labelFloor);
    });
    const boundaries = columns.length - 1;

    const baseCuts = [contentTop, contentBottom];
    for (const y of opensAt) if (y > contentBottom && y < contentTop) baseCuts.push(y);
    baseCuts.sort((a, b) => b - a);

    // Where a column has not opened, look at what is actually drawn there and
    // record the heights over which no channel separates it from the column to
    // its left. Those runs, not every block edge, are the extra cut points.
    const blocked = columns.slice(0, -1).map(() => []);
    for (let i = 0; i + 1 < baseCuts.length; i++) {
      const top = baseCuts[i];
      const bottom = baseCuts[i + 1];
      if (top - bottom < 0.5) continue;
      const mid = (top + bottom) / 2;
      for (let c = 0; c < boundaries; c++) {
        if (mid > opensAt[c] || mid <= opensAt[c + 1]) continue;
        blocked[c].push(...this.blockedRuns(blocks, columns[c + 1].x0 - 4, top, bottom));
      }
    }

    // The same question below the flow instead of above it. It is asked of the
    // whole region at once, not run by run: a full-width line alone at the foot
    // of the page is too short to register as a run of its own, and leaving it
    // in an unmerged sliver is enough to hand the bottom of the page back to the
    // column that has already ended.
    for (let c = 0; c < boundaries; c++) {
      let end = closesAt[c + 1];
      if (end <= contentBottom + 0.5) continue;
      const boundary = columns[c + 1].x0 - 4;
      if (this.columnClearance(blocks, boundary, end, contentBottom, true) >= COLUMN_CLEAR) continue;
      // The merged region must not begin inside something the column on the
      // left is still drawing -- a photo running past the height where the
      // right column happened to stop, or a list item long enough to overrun
      // the gutter. Text is set left to right, so a block that begins in that
      // column belongs to it; cutting there would slice its region in two at a
      // boundary that has nothing to do with it, so the merge waits for the
      // block to finish.
      const from = columns[c];
      for (const b of lines) {
        const startsHere = b.x0 >= from.x0 - COLUMN_EDGE && b.x0 <= from.x1;
        if (startsHere && b.y0 < end && b.y1 > end) end = b.y0;
      }
      if (end <= contentBottom + 0.5) continue;
      blocked[c].push([contentBottom, end]);
    }

    const cuts = new Set(baseCuts);
    for (const list of blocked) {
      for (const [lo, hi] of list) {
        if (lo > contentBottom && lo < contentTop) cuts.add(lo);
        if (hi > contentBottom && hi < contentTop) cuts.add(hi);
      }
    }

    const ys = [...cuts].sort((a, b) => b - a);
    let strips = [];
    for (let i = 0; i + 1 < ys.length; i++) {
      const top = ys[i];
      const bottom = ys[i + 1];
      if (top - bottom < 0.5) continue;
      const mid = (top + bottom) / 2;
      const opened = opensAt.map((y) => mid <= y);
      const active = opened.map((isOpen, i) => isOpen && mid > closesAt[i]);
      const absorb = blocked.map((runs) => runs.some(([lo, hi]) => mid > lo && mid < hi));
      strips.push({ top, bottom, active, absorb, dead: !opened.some(Boolean) });
    }
    if (!strips.length) {
      strips = [{
        top: contentTop,
        bottom: contentBottom,
        active: columns.map(() => true),
        absorb: new Array(boundaries).fill(false),
        dead: false,
      }];
    }
    strips = coalesceStrips(strips);

    // Absorb strips too short to be a layout of their own. A strip holding a
    // label is never one of them, however thin: absorbing it would move the
    // label into a neighbour's cell, where it is read in that cell's order and
    // its own band collapses to nothing -- the activity is then left with its
    // bare glyph while the flow beside it owns the label's row.
    const holdsLabel = (s) =>
      columns.some((col) => col.markers.some((m) => m.y > s.bottom && m.y <= s.top));
    while (strips.length > 1) {
      let shortest = -1;
      for (let i = 0; i < strips.length; i++) {
        if (holdsLabel(strips[i])) continue;
        if (shortest === -1 ||
            strips[i].top - strips[i].bottom < strips[shortest].top - strips[shortest].bottom) {
          shortest = i;
        }
      }
      if (shortest === -1) break;
      const sliver = strips[shortest];
      if (sliver.top - sliver.bottom >= MIN_STRIP) break;
      // A dead strip is the space above the page's first label. Growing one
      // downwards over a live sliver buries whatever the sliver holds, so a
      // live sliver only ever joins a live neighbour.
      const usable = (t) => t && (sliver.dead || !t.dead);
      const height = (t) => (usable(t) ? t.top - t.bottom : -1);
      const above = strips[shortest - 1];
      const below = strips[shortest + 1];
      const host = height(above) >= height(below) ? above : below;
      if (!usable(host)) break;
      host.top = Math.max(host.top, sliver.top);
      host.bottom = Math.min(host.bottom, sliver.bottom);
      strips.splice(shortest, 1);
      strips = coalesceStrips(strips);
    }

    for (const s of strips) s.cells = this.cellsFor(columns, s.active, s.absorb);
    return strips;
  }

  /**
   * The height at which a column's own flow ends -- the counterpart of the
   * `opensAt` that says where it begins.
   *
   * Own content is measured on lines that stay inside the band, with COLUMN_EDGE
   * of slack so a photo bleeding a little past the gutter still counts: a line
   * that crosses the band is a full-width object passing through, not the column
   * continuing. Reading down from the top of the column, the flow ends at the
   * first blank taller than FLOW_BREAK, because a flow is continuous and a gap
   * that size is a break in the page rather than leading or a paragraph space.
   *
   * Closing a column early is cheap: the space below only merges into its
   * neighbour where the page has stopped being in columns there anyway.
   */
  columnFlowEnd(lines, column, top, bottom) {
    const own = lines
      .filter((b) => this.ownsBlock(column, b) && b.y1 <= top + 1 && b.y0 >= bottom - 1)
      .sort((a, b) => b.y1 - a.y1);

    let end = top;
    for (const b of own) {
      if (b.y1 < end - FLOW_BREAK) break;
      end = Math.min(end, b.y0);
    }
    return Math.max(end, bottom);
  }

  /**
   * Is this block content of `column` itself, rather than something drawn across
   * it? COLUMN_EDGE of slack lets a photo bleed a little past the gutter and
   * still belong to the column it sits in.
   */
  ownsBlock(column, b) {
    const cx = (b.x0 + b.x1) / 2;
    return (
      cx >= column.x0 &&
      cx <= column.x1 &&
      b.x0 >= column.x0 - COLUMN_EDGE &&
      b.x1 <= column.x1 + COLUMN_EDGE
    );
  }

  /**
   * The heights within [bottom, top] over which no channel separates the two
   * sides of `boundary`.
   *
   * Deciding this line by line makes the answer flicker -- a paragraph's short
   * last line clears the channel that the lines above it close -- and a
   * flickering answer shreds one region into a stack of hotspots. So the raw
   * per-line answer is smoothed: clear gaps shorter than BRIDGE do not interrupt
   * a run (a full-width block is still full-width across its own line spacing),
   * and runs shorter than MIN_STRIP are discarded as too small to be a layout of
   * their own.
   */
  blockedRuns(blocks, boundary, top, bottom) {
    const edges = new Set([top, bottom]);
    for (const b of blocks) {
      for (const y of [b.y0, b.y1]) if (y > bottom && y < top) edges.add(y);
    }
    const ys = [...edges].sort((a, b) => b - a);

    const runs = [];
    let clearHeight = 0;
    for (let i = 0; i + 1 < ys.length; i++) {
      const hi = ys[i];
      const lo = ys[i + 1];
      if (hi - lo < 0.25) continue;
      const width = this.channelWidth(blocks, boundary, hi, lo);
      // Nothing drawn on one side of the boundary here: the slice separates
      // nothing, so it neither opens a run nor interrupts one. Leading between
      // lines and the space around a block are all of this kind, and counting
      // them as clear is what used to chop a full-width block into slices.
      if (width === null) continue;
      if (width >= COLUMN_CHANNEL) {
        clearHeight += hi - lo;
        continue;
      }
      const last = runs[runs.length - 1];
      if (last && clearHeight <= BRIDGE) last[0] = lo;
      else runs.push([lo, hi]);
      clearHeight = 0;
    }
    return runs.filter(([lo, hi]) => hi - lo >= MIN_STRIP);
  }

  /**
   * Clear horizontal space across `boundary` over a slice of the page: the gap
   * between the rightmost block belonging to the left of it and the leftmost
   * block belonging to the right of it. Blocks are sorted by their centre, so a
   * line overrunning its column by a point or two does not close the channel,
   * while one object spanning the boundary closes it outright (its own centre
   * lands on one side and its far edge reaches past the other).
   *
   * Returns null when one side is empty: there is nothing here to separate, so
   * the slice says nothing about whether the columns are columns.
   */
  channelWidth(blocks, boundary, top, bottom, straddleCloses = false) {
    let leftEdge = -Infinity;
    let rightEdge = Infinity;
    let straddles = false;
    for (const b of blocks) {
      if (b.y1 <= bottom || b.y0 >= top) continue;
      if (b.x0 < boundary && b.x1 > boundary) straddles = true;
      if ((b.x0 + b.x1) / 2 < boundary) leftEdge = Math.max(leftEdge, b.x1);
      else rightEdge = Math.min(rightEdge, b.x0);
    }
    // Asking whether a column still exists below its own flow (straddleCloses)
    // is not the same question as where this page's gutters run. There, one
    // object drawn straight across the boundary settles it on its own: a
    // full-width line whose centre happens to fall on the left would otherwise
    // leave the slice undecided, and whether the fix fires would depend on
    // which side of the boundary the middle of the line landed. Where the page
    // is being read as columns in the first place, the same rule is far too
    // eager -- a photo overlapping the gutter by a point would deny the gutter
    // -- so the default stays the conservative centre-based measurement.
    if (straddleCloses && straddles) return 0;
    if (leftEdge === -Infinity || rightEdge === Infinity) return null;   // nothing to separate
    return rightEdge - leftEdge;
  }

  /**
   * How much of the page keeps a whitespace channel open across `boundary`:
   * the share of the height where the question is decided at all (both sides
   * carry something) over which the two sides stand COLUMN_CHANNEL apart.
   *
   * A real gutter is open nearly everywhere the two columns run side by side. An
   * indent is the opposite: the only thing left of it is the outer list's own
   * labels, and the outer instruction text -- which begins left of the indent
   * and runs past it -- closes the channel wherever the two meet.
   */
  /**
   * The share of [bottom, top] over which both sides of `boundary` carry
   * something -- that is, how much of the page the two candidate columns
   * actually spend side by side.
   *
   * columnClearance() only measures the slices where the question is decided,
   * and reports a boundary with nothing on one side of it as perfectly clear.
   * That is the right answer to "is this a gutter"; it is the wrong answer to
   * "are these two columns", which is what this one is for.
   */
  sideBySideShare(blocks, boundary, top, bottom) {
    const edges = new Set([top, bottom]);
    for (const b of blocks) {
      for (const y of [b.y0, b.y1]) if (y > bottom && y < top) edges.add(y);
    }
    const ys = [...edges].sort((a, b) => b - a);
    let paired = 0;
    for (let i = 0; i + 1 < ys.length; i++) {
      const hi = ys[i];
      const lo = ys[i + 1];
      if (hi - lo < 0.25) continue;
      if (this.channelWidth(blocks, boundary, hi, lo) !== null) paired += hi - lo;
    }
    const height = top - bottom;
    return height > 0 ? paired / height : 0;
  }

  columnClearance(blocks, boundary, top, bottom, straddleCloses = false) {
    const edges = new Set([top, bottom]);
    for (const b of blocks) {
      for (const y of [b.y0, b.y1]) if (y > bottom && y < top) edges.add(y);
    }
    const ys = [...edges].sort((a, b) => b - a);

    let clear = 0;
    let decided = 0;
    for (let i = 0; i + 1 < ys.length; i++) {
      const hi = ys[i];
      const lo = ys[i + 1];
      if (hi - lo < 0.25) continue;
      const width = this.channelWidth(blocks, boundary, hi, lo, straddleCloses);
      if (width === null) continue;      // nothing on one side: says nothing
      decided += hi - lo;
      if (width >= COLUMN_CHANNEL) clear += hi - lo;
    }
    return decided > 0 ? clear / decided : 1;
  }

  /**
   * Column bands for one strip. A column marked for absorption widens the cell to
   * its left instead of being a cell of its own. Every other column keeps its own
   * cell, even one that has not opened yet: it carries no label, so it becomes
   * the continuation of whatever flow precedes it -- or, if nothing does, stays
   * unowned, the way the space above the first label of a page does.
   */
  cellsFor(columns, active, absorb) {
    const cells = [];
    let current = null;
    for (let i = 0; i < columns.length; i++) {
      if (current && !active[i] && absorb[i - 1]) {
        current.x1 = columns[i].x1;          // one wide region, not two columns
        continue;
      }
      current = { x0: columns[i].x0, x1: columns[i].x1, column: i };
      cells.push(current);
    }
    return cells;
  }

  /** The cell a label opens: the one holding its baseline, nearest in x. */
  cellFor(cells, marker, contentTop, contentBottom) {
    const y = clamp(marker.y, contentBottom + 0.1, contentTop);
    const cx = (marker.x0 + marker.x1) / 2;
    let best = null;
    let bestDistance = Infinity;
    for (const cell of cells) {
      if (y > cell.top || y <= cell.bottom) continue;
      if (cx >= cell.x0 && cx <= cell.x1) return cell;
      const d = cx < cell.x0 ? cell.x0 - cx : cx - cell.x1;
      if (d < bestDistance) {
        bestDistance = d;
        best = cell;
      }
    }
    return best;
  }

  /**
   * Keep the largest subset of candidate labels that still reads as a valid
   * sequence. The previous greedy scan required every label to be exactly one
   * letter after the last one it kept, so a single label lost to a decorative
   * element deleted every label after it on the page as well. A longest
   * increasing subsequence tolerates the gap: letters must still ascend in
   * reading order, a run may restart at `a`, and a chain with no missing letters
   * outscores an equally long one with holes.
   */
  longestLabelRun(ordered) {
    const n = ordered.length;
    if (!n) return [];

    // Two alphabets in one column are two levels of one list set flush with each
    // other -- "1." holding "a) b) c)" at the same indent. Neither interrupts the
    // other, so each is validated on its own and the page keeps both runs; one
    // chain through the lot would drop whichever level it did not follow.
    const kinds = [...new Set(ordered.map((e) => e.marker.label.kind))];
    if (kinds.length > 1) {
      const runs = kinds
        .map((kind) =>
          this.longestLabelRun(ordered.filter((e) => e.marker.label.kind === kind))
        )
        .filter((run) => run.length > 1);
      if (runs.length) {
        const kept = new Set(runs.flat());
        return ordered.filter((e) => kept.has(e));
      }
    }

    const best = new Array(n).fill(1);
    const from = new Array(n).fill(-1);
    for (let i = 0; i < n; i++) {
      const label = ordered[i].marker.label;
      const restart = label.value === 1;
      for (let j = 0; j < i; j++) {
        const prev = ordered[j].marker.label;
        const ascends = prev.kind === label.kind && prev.value < label.value;
        if (!ascends && !restart) continue;
        const penalty = ascends ? (label.value - prev.value - 1) * 0.01 : 0;
        const score = best[j] + 1 - penalty;
        if (score > best[i]) {
          best[i] = score;
          from[i] = j;
        }
      }
    }
    let tail = 0;
    for (let i = 1; i < n; i++) if (best[i] > best[tail]) tail = i;
    const out = [];
    for (let i = tail; i !== -1; i = from[i]) out.push(ordered[i]);
    return out.reverse();
  }

  /**
   * Rejoin pieces of one activity that a strip boundary cut apart -- typically an
   * instruction line in a column cell with the rest of its own text set full
   * width directly underneath. Only vertically touching pieces of the same column
   * whose x-ranges nest are merged, so an activity continuing in the *next
   * column* still keeps one independent region per piece, and a merge is
   * abandoned outright if the union would reach over any other activity.
   */
  mergeStackedParts(parts) {
    for (let merged = true; merged; ) {
      merged = false;
      for (let i = 0; i < parts.length && !merged; i++) {
        for (let j = i + 1; j < parts.length && !merged; j++) {
          if (parts[i].owner !== parts[j].owner) continue;
          if (parts[i].column !== parts[j].column) continue;
          const a = parts[i].rect;
          const b = parts[j].rect;
          if (Math.max(a.y0, b.y0) - Math.min(a.y1, b.y1) > STACK_GAP) continue;
          const shared = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0);
          const narrower = Math.min(a.x1 - a.x0, b.x1 - b.x0);
          if (shared <= 0 || shared < STACK_OVERLAP * narrower) continue;
          const union = unionRect(a, b);
          // A hair of overlap is not a reason to leave one region in two
          // pieces: separateRects() pushes touching hotspots apart afterwards,
          // and it is only reaching *over* a neighbour that the merge has to be
          // abandoned for. So the union is judged inset by the gap that pass
          // would open anyway.
          const room = {
            x0: union.x0 + HOTSPOT_GAP, x1: union.x1 - HOTSPOT_GAP,
            y0: union.y0 + HOTSPOT_GAP, y1: union.y1 - HOTSPOT_GAP,
          };
          const clash = parts.some(
            (other) => other.owner !== parts[i].owner && intersects(room, other.rect)
          );
          if (clash) continue;
          parts[i].rect = union;
          parts[i].anchor = parts[i].anchor || parts[j].anchor;
          parts.splice(j, 1);
          merged = true;
        }
      }
    }

    // A wide region can already cover a narrower sibling whole -- an activity
    // whose full-width text reaches over the column piece above it. The
    // narrower one then adds nothing but an ambiguous hit target, and would have
    // its questions counted twice, so drop it.
    for (let i = parts.length - 1; i >= 0; i--) {
      for (let j = 0; j < parts.length; j++) {
        if (i === j || parts[i].owner !== parts[j].owner) continue;
        const inner = parts[i].rect;
        const outer = parts[j].rect;
        const covered =
          Math.max(0, Math.min(inner.x1, outer.x1) - Math.max(inner.x0, outer.x0)) *
          Math.max(0, Math.min(inner.y1, outer.y1) - Math.max(inner.y0, outer.y0));
        if (rectArea(inner) <= rectArea(outer) && covered >= 0.8 * rectArea(inner)) {
          parts.splice(i, 1);
          break;
        }
      }
    }

    return parts;
  }

  /**
   * The part of a band that is the question itself.
   *
   * A band is a slice of the page below a label, so it holds two kinds of thing:
   * the question -- its label, the instruction that runs on from it, the worked
   * example under that, its numbered items -- and whatever else the designer
   * happened to print in the same slice, which on this kind of page is a great
   * deal: a photo strip, the dialogue the question refers to, a coloured box, a
   * section banner. A region must cover the first and none of the second, so the
   * band is filtered by the two things that separate a question from its
   * surroundings on the page:
   *
   *   1. it is set to one measure. The question's opening line -- the
   *      instruction that runs on from the label -- shows how wide the text it
   *      is set in is, and the rest of the question is set to no more than the
   *      wider of that and the column the label opens. A line that breaks out of
   *      that measure is a full-width object the question merely sits above,
   *      however close underneath it falls. Reading the measure off the page
   *      rather than off the column grid is what keeps a genuinely full-width
   *      section whole while a two-line instruction above a full-width dialogue
   *      keeps only its two lines.
   *   2. it is set continuously. A question's lines follow one another at the
   *      leading, so reading down from the label, the flow ends at the first
   *      blank appreciably wider than that -- or at the first line set larger
   *      than the label, which is a heading and so the start of something else.
   *
   * A band that is not the label's own is a continuation -- the tail of a
   * question at the top of the next column -- so it has no label to read down
   * from and starts at its first line instead.
   */
  flowContent(act, band, column, obstacles, panels = [], images = []) {
    const lines = groupLines(band.items);
    if (!lines.length) return { rect: null, items: [] };

    const opener = lines[0];
    const left = Math.min(column.x0, opener.x0) - COLUMN_EDGE;
    const right = Math.max(column.x1, opener.x1) + COLUMN_EDGE;
    const measure = lines.filter((line) => line.x0 >= left && line.x1 <= right);
    if (!measure.length) return { rect: null, items: [] };

    const leading = this.lineLeading(measure.flatMap((line) => line.items), act.marker.size);

    // Every label on the line counts, not just its leftmost. A line of a
    // question set beside a figure shares its row with the figure's own
    // captions, which are drawn first because they stand further left on the
    // page; and a list of short answers is routinely set several to the row, so
    // one line of it can carry "1" and "2", or "3" and "4", at once.
    const labelsOn = (line) => {
      const out = [];
      for (const it of line.items) {
        if (!it.label || !it.clearLeft) continue;
        if (!this.markerFonts.has(it.font) || it.size > this.markerSize + 0.5) continue;
        out.push(it.label);
      }
      return out;
    };

    // What separates two lines is not the distance between them but how much
    // blank paper it is: a question set around the photograph it refers to has
    // its own last paragraph a long way below its first, with no break in it at
    // all. So anything drawn between two lines -- a picture, a table, a figure,
    // a line of the other column's text that reaches into this measure -- is
    // taken out of the distance before it is judged.
    const between = obstacles.filter((o) => {
      const overlap = Math.min(o.x1, right) - Math.max(o.x0, left);
      return overlap > 0.5 * (o.x1 - o.x0);
    });
    const clearGap = (from, to) => {
      let gap = from - to;
      for (const [lo, hi] of mergeSpans(between, to, from)) gap -= hi - lo;
      return Math.max(gap, 0);
    };

    // How much air stands above a question's own list says nothing about whether
    // the list belongs to it: an instruction is routinely set clear of the list
    // under it, and a workbook leaves half a page between items for the answers
    // to be written in. So it is not the leading that carries the flow across a
    // list -- it is the list itself, item by item, and the flow ends where that
    // succession does. Each alphabet counts on its own: a book nests one list
    // inside another by changing alphabet ("a) b) c)" under "1."), and a table
    // of "1 2 3 ..." set across a column consumes the digits without saying
    // anything about the lettered list underneath it, so a single running count
    // would leave that list unable to start.
    // A question is set in the type it opens in. A line set wholly in *other*
    // type -- a bigger size, or a face the question has not used once -- is not
    // the next thing the question says: it is the page's own furniture, the
    // heading of the section printed below, the banner of the rubric that
    // follows. Reading on past it is how a region ends up covering a heading, a
    // passage of prose and a table that belong to nobody's question, which is
    // the same defect as ending inside a drawn panel, seen from the other side.
    //
    // Only a whole line counts: a bold term or an italic aside inside a sentence
    // is set in another face too, and is plainly the question's own. Nor does a
    // line that carries the next item of a list the question has opened -- a
    // book is free to set its list in whatever face it likes, and the succession
    // says the item is the question's however it is dressed.
    //
    // Type alone cannot say it, though, because a book sets the question itself
    // in two faces as often as not -- the instruction in roman, the question it
    // asks in bold under it -- and that second line is the question's own. What
    // separates them is where the line starts. A question hangs its runover off
    // the label, indented clear of it; a heading is set to the column, flush
    // with the label or further left. So a change of face only ends the flow at
    // a line that has come back out to the margin, while a change of size ends
    // it wherever it falls: display type that size is a heading even indented.
    // The question's own type is the type it opens in, and only that: the bold
    // is measured against the roman the instruction is set in, not against every
    // face the question has used by the time the heading arrives, or a book that
    // sets its headings in the same bold it emphasises a question in would never
    // reach one.
    // Only the band the label stands in can be read that way. A band that is a
    // question's overflow into the next column has no label for a line to hang
    // off, so every line in it is flush with the column and "flush" stops
    // meaning anything; there, only the size of the type can say a heading.
    //
    // An anchored activity has no label either, but it does have a first line,
    // and its marker is the first word of it -- so the margin is still a real
    // edge to measure against. Excusing anchored bands from this test, on the
    // grounds that their runover is flush with the marker, mistook what the
    // test does: being out at the margin is necessary for a heading, never
    // sufficient. A runover line is flush too, and is kept because it fills the
    // measure and is set in the question's own face. Turning the test off left
    // nothing but a full-width rule to stop an anchored region, and it ran on
    // through the headings below it into the next section -- which is where its
    // panel cuts and its page-high rects came from.
    const marginLeft = band.own ? act.marker.x0 + 1 : -Infinity;
    // Which type that is has to be read off the lines already taken, not off the
    // whole band: the band runs down to the next label, so it contains the very
    // section the region must be kept out of, and measuring against that is how
    // a 12pt heading came to look ordinary beside the 10.5pt section under it.
    //
    // A line or two does not settle it, though. What comes first in a band is
    // routinely not the question -- a caption on a figure, a credit set up the
    // side of a photograph, the small print of a rubric -- and read off one of
    // those, every ordinary line afterwards measures like display type. So the
    // body type is the size the region has most text in, and it is not settled
    // at all until there is a line's worth of text to settle it with.
    const width = new Map();
    let bodySize = 0;
    const faces = new Set();
    const readType = (line) => {
      // Read once and kept: the faces are the ones the question opens in, not
      // every face it has used by the time a heading arrives, or a book that
      // sets its headings in the same bold it emphasises a question in would
      // never reach one.
      if (bodySize) return;
      for (const it of line.items) {
        if (!isProse(it)) continue;
        const key = it.size.toFixed(1);
        width.set(key, (width.get(key) || 0) + (it.x1 - it.x0));
      }
      let widest = 0;
      let size = 0;
      for (const [key, w] of width) {
        if (w > widest) {
          widest = w;
          size = parseFloat(key);
        }
      }
      if (widest < BODY_EVIDENCE) return;
      bodySize = size;
      for (const it of line.items) {
        if (isProse(it) && Math.abs(it.size - bodySize) < 0.6) faces.add(it.face);
      }
    };

    // The other thing a question is not set in is the small print. A caption
    // under a figure, a credit up the side of a photograph, the rotated tab a
    // book runs down the edge of a section: these stand in the space a question
    // leaves below it without being any part of it, and a flow that takes them
    // walks down the page a step at a time on furniture, arriving in the middle
    // of the next exercise having never crossed a gap wide enough to stop it.
    // They are skipped rather than read as the end, since the question's own
    // list may well resume under them.
    const isAside = (line) => {
      if (!bodySize) return false;
      const prose = line.items.filter(isProse);
      if (!prose.length) return false;
      if (line.items.some((it) => it.dx * act.marker.dx + it.dy * act.marker.dy < SAME_DIRECTION)) {
        return true;
      }
      return prose.every((it) => it.size * HEADING_RATIO < bodySize);
    };

    // Display type opens a section wherever it is set, indented or not, inside
    // the question's measure or across the page. That is the reading that can be
    // applied to a line the question would never have taken anyway, and it is
    // how a region is stopped at a heading set wider or further left than the
    // question it follows.
    //
    // Except inside a shape the page draws. A reading card, a tinted exercise
    // box and a speech bubble all carry a title of their own, set as large as
    // any section heading, and it belongs to the block the shape encloses --
    // whether the question takes that block is a question about the block,
    // answered whole by panelFor(). A panel the label itself stands in is not
    // that: it is the section the question lives in, and the headings printed
    // inside it divide that section like any others.
    const opensSection = (line) => {
      if (!bodySize) return false;
      if (line.items.every((it) => !isProse(it))) return false;
      if (panels.some((p) => encloses(p, line, 2) && !encloses(p, act.marker, 2))) return false;
      // A placed picture is the same case. A reading card printed on an image of
      // torn paper, a title set over a photograph: the words standing on it
      // belong to it, whatever size they are set at, and the picture is the
      // block -- so the line says nothing about where the page's sections
      // divide. This is the reading textPanels() already takes of text over a
      // photograph, applied to the one question asked here.
      if (images.some((im) => encloses(im, line, 2) && !encloses(im, act.marker, 2))) return false;
      return Math.max(...line.items.map((it) => it.size)) > bodySize * HEADING_RATIO;
    };

    // A change of face is the weaker reading, so it is only applied to a line
    // the question might otherwise have taken -- one set to its own measure --
    // and only when the line reads as a heading in every other way too: out at
    // the margin the label stands on rather than indented off it, and short
    // enough to be naming what follows rather than saying it. A block that
    // merely opens in another face -- a bank of words to choose from, a table of
    // answers, a figure's labels -- fills the measure the way ordinary text
    // does, and cutting the question off from it would lose the material the
    // question is about.
    const isHeading = (line) => {
      if (!bodySize || !faces.size) return false;
      if (line.items.every((it) => !isProse(it))) return false;
      if (opensSection(line)) return true;
      if (panels.some((p) => encloses(p, line, 2) && !encloses(p, act.marker, 2))) return false;
      return (
        line.x0 <= marginLeft &&
        line.x1 - line.x0 < HEADING_MEASURE * (right - left) &&
        line.items.every((it) => !faces.has(it.face))
      );
    };

    const items = [];
    let rect = null;
    let baseline = band.own ? act.marker.y : null;
    let bottom = band.own ? act.marker.y0 : null;
    const lists = new Map();
    for (const [i, line] of measure.entries()) {
      const occupied =
        baseline === null ? 0 : Math.max(0, bottom - line.y1) - clearGap(bottom, line.y1);
      const gap = baseline === null ? 0 : baseline - line.y - occupied;
      // The label's own line opens the *activity*, not an item inside it, so it
      // never starts the list this question is counting through.
      const runs = new Map();
      for (const l of band.own && i === 0 ? [] : labelsOn(line)) {
        const run = runs.get(l.kind) || { min: Infinity, max: -Infinity };
        run.min = Math.min(run.min, l.value);
        run.max = Math.max(run.max, l.value);
        runs.set(l.kind, run);
      }
      let carries = false;
      for (const [kind, run] of runs) {
        const open = lists.get(kind);
        // The line carries the list when its first label is the one that follows
        // what the list has taken so far -- the successor of its last item,
        // which for letters may be a fractional step (ç after c).
        run.taken = open
          ? (LABEL_SUCCESSORS.get(open.max) || []).includes(run.min)
          : run.min === 1;
        carries = carries || run.taken;
      }
      // Before the list opens, the question is still saying what it is about,
      // and a book sets that preamble around whatever it refers to: a figure, a
      // table, a formula on a line of its own. The blanks those leave behind say
      // nothing, so up here the flow ends only at a blank wide enough to be a
      // break in the page itself. Once the list has opened the rhythm is the
      // list's own and the leading rules again -- which is what stops the last
      // item of a list from running on into the passage printed below it.
      const maxGap = lists.size ? FLOW_LEADING * leading : FLOW_BREAK;
      // A line that fails both tests is skipped rather than ending the read: a
      // list is still the question's own even when an answer grid, a graph or a
      // caption is set between two of its items, and only the succession of the
      // labels can say so. Nothing skipped is ever taken into the region -- it
      // only stops being a wall the rest of the question cannot be seen over.
      if (gap > maxGap && !carries) continue;
      if (!carries && isAside(line)) continue;
      // A heading is not skipped but read as the end: everything printed under
      // the heading belongs to what the heading opens, not to the question above
      // it, so there is nothing further down for the flow to come back for.
      //
      // And a heading is looked for over every line of the band, not only the
      // ones that fit the question's measure. A heading is set to the page, not
      // to the question: it is routinely wider than the measure or further left
      // than it, which is what put it out of this read in the first place. Only
      // its being *between* the question's last line and the next one it would
      // take matters, since that is a section boundary the flow would be
      // crossing.
      if (!carries && (isHeading(line) || (baseline !== null &&
          lines.some((l) => l.y < baseline && l.y > line.y && opensSection(l))))) break;
      {
        for (const [kind, run] of runs) if (run.taken) lists.set(kind, { max: run.max });
      }
      rect = unionRect(rect, line);
      items.push(...line.items);
      readType(line);
      baseline = line.y;
      bottom = line.y0;
    }
    return { rect, items };
  }

  /**
   * The panels of text the page draws: a dialogue in a speech bubble, an
   * exercise set in a tinted box, a worked example on a card.
   *
   * These are the one thing on the page that a region may not be allowed to
   * *half* cover. A question's own lines are held to one measure
   * (flowContent()), and a dialogue is not: it is set to the full width of the
   * page, so its short turns fall inside the measure while its long ones break
   * out of it. Read line by line, that leaves a region covering a ragged half of
   * the dialogue -- the lines that happened to be short -- which is neither of
   * the two answers the page admits. A shape the designer drew around a block of
   * text says where that block begins and ends, and it is taken whole or not at
   * all.
   *
   * Only drawn paths are read, never images: a photograph is something a
   * question points at, and text standing over one is a caption on it rather
   * than a block the page has boxed. A shape counts as a panel once it holds
   * PANEL_LINES lines of prose and is bigger than the rules, chips and banners
   * that are drawn at the same time.
   */
  textPanels(paths, body) {
    const prose = body.filter(isProse);
    const panels = [];
    for (const r of paths) {
      if (r.x1 - r.x0 < PANEL_MIN_W || r.y1 - r.y0 < PANEL_MIN_H) continue;
      const rows = new Set();
      for (const it of prose) if (encloses(r, it, 2)) rows.add(Math.round(it.y));
      if (rows.size < PANEL_LINES) continue;
      panels.push({ x0: r.x0, y0: r.y0, x1: r.x1, y1: r.y1 });
    }
    // One shape reaches the operator list several times over -- its fill, its
    // stroke, a shadow a point or two outside both -- and a panel is often lined
    // with a second panel just inside it. What the reader sees is the outermost
    // outline, so a shape held by another is not a panel of its own.
    panels.sort((a, b) => rectArea(b) - rectArea(a));
    const out = [];
    for (const panel of panels) {
      if (!out.some((held) => encloses(held, panel, 2))) out.push(panel);
    }
    return out;
  }

  /**
   * The panel a question's flow runs into, taken whole.
   *
   * A question's own lines are held to one measure, and a dialogue is not: it
   * is set across the page, so its short turns fall inside that measure while
   * its long ones break out of it. Read line by line that leaves a region
   * covering a ragged half of the dialogue -- whichever speeches happened to be
   * short -- which is not an answer the page admits. So the rule is
   * all-or-nothing: once the flow has reached inside a panel, the region covers
   * the panel whole, out to the frame the reader can see.
   *
   * Reaching inside means having read words out of it -- some of the panel's
   * own text is among the lines flowContent() kept as this question's. Merely
   * standing over a panel, or ending a few points inside its frame, is not
   * reaching into it: a question printed above an information box or across the
   * top of an answer grid points at it at most, and points at it exactly as it
   * always has. A panel holding another activity's label is never taken either:
   * that is a boxed exercise, several questions set in one frame, and each of
   * them keeps its own region inside it.
   */
  panelFor(act, band, content, panels, markers, members) {
    if (!content || !panels.length || !members.length) return null;
    let grown = null;
    for (const panel of panels) {
      // A label the panel merely grazes -- the rounded foot of a bubble reaching
      // a point past the line under it -- is not a label set inside it. What
      // disqualifies the panel is a label the frame genuinely encloses.
      if (markers.some((m) => m !== act.marker && encloses(panel, m, -2))) continue;
      // Inside the band: a band ends 2 pt above the next label, so a panel
      // hanging below that belongs to the question underneath.
      const held = Math.min(panel.y1, band.top) - Math.max(panel.y0, band.bottom);
      if (held < 0.5 * (panel.y1 - panel.y0)) continue;
      if (band.shared) {
        const inCell = Math.min(panel.x1, band.x1 + PAD) - Math.max(panel.x0, band.x0 - PAD);
        if (inCell < 0.5 * (panel.x1 - panel.x0)) continue;
      }
      if (!members.some((it) => contains(panel, it))) continue;
      // And running the length of it. A question set *inside* a much larger
      // drawn object -- one line printed on a tinted background that carries a
      // whole page of other matter -- has not read that object, it is merely
      // standing on it, and snapping out to its frame would hand the question
      // everything else printed there.
      const along = Math.min(content.y1, panel.y1) - Math.max(content.y0, panel.y0);
      if (along < 0.5 * (panel.y1 - panel.y0)) continue;
      grown = unionRect(grown, panel);
    }
    return grown;
  }

  /**
   * The space the page leaves for answers, read off its vector drawing.
   *
   * A workbook rules the space it expects to be written in: writing lines under
   * a question, the grid of a table whose right-hand columns are left empty,
   * the panel an answer box is set in. None of that is text, so the only trace
   * of it is the path geometry recovered by drawnMatter(); and it is the answer
   * to a question the text alone cannot settle, which is where a region ends.
   *
   * Rules that run together are read as one grid: its horizontal rules give the
   * rows, its vertical rules the columns, and every cell of it that carries no
   * prose is somewhere to write. That one model covers both shapes the book
   * uses -- a table whose left column asks and whose other columns are blank,
   * and a bare stack of writing lines, which is a grid one column wide.
   *
   * A panel that encloses a whole grid and says nothing itself is that grid's
   * answer box, and is returned alongside its cells: the card an answer is set
   * in belongs to the question with everything drawn inside it, including the
   * illustration printed around the lines. A panel with prose in it is a
   * dialogue, a banner or a worked example, and is nothing of the sort.
   */
  solutionBlocks(paths, body) {
    const horizontal = [];
    const vertical = [];
    const panels = [];
    for (const p of paths) {
      const w = p.x1 - p.x0;
      const h = p.y1 - p.y0;
      if (h <= RULE_THICK && w >= RULE_MIN) horizontal.push(p);
      else if (w <= RULE_THICK && h >= RULE_MIN) vertical.push(p);
      if (w >= RULE_MIN && h >= MIN_CELL) panels.push(p);
    }
    if (!horizontal.length) return [];
    const prose = body.filter(isProse);
    const saysSomething = (r) =>
      prose.some((it) => (it.x0 + it.x1) / 2 > r.x0 && (it.x0 + it.x1) / 2 < r.x1 &&
                         (it.y0 + it.y1) / 2 > r.y0 && (it.y0 + it.y1) / 2 < r.y1);

    // Rules belong to one grid when they run into one another: touching along
    // their length, or stacked no further apart than a row is tall. Nothing but
    // the rules themselves joins two grids, so a ruled box in one column never
    // reaches across the gutter to the table beside it.
    const rules = [...horizontal, ...vertical];
    const near = (a, b) =>
      Math.min(a.x1, b.x1) + RULE_CLUSTER >= Math.max(a.x0, b.x0) &&
      Math.min(a.y1, b.y1) + RULE_ROW >= Math.max(a.y0, b.y0);
    const group = rules.map((_, i) => i);
    const root = (i) => (group[i] === i ? i : (group[i] = root(group[i])));
    for (let i = 0; i < rules.length; i++) {
      for (let j = i + 1; j < rules.length; j++) {
        if (near(rules[i], rules[j])) group[root(i)] = root(j);
      }
    }
    const clusters = new Map();
    rules.forEach((r, i) => {
      const key = root(i);
      if (!clusters.has(key)) clusters.set(key, []);
      clusters.get(key).push(r);
    });

    const blocks = [];
    for (const cluster of clusters.values()) {
      // Ruling is repetition: a single stroke on its own is an underline, a
      // divider or a flourish, and there is nothing to say how tall a row of it
      // would be. Two rules make a space to write in.
      const rows = cluster.filter((r) => r.y1 - r.y0 <= RULE_THICK);
      if (rows.length < 2) continue;
      const box = cluster.reduce((acc, r) => unionRect(acc, r), null);

      const ys = dedupe(rows.map((r) => (r.y0 + r.y1) / 2)).sort((a, b) => b - a);
      const cols = dedupe(
        cluster.filter((r) => r.x1 - r.x0 <= RULE_THICK).map((r) => (r.x0 + r.x1) / 2)
      ).sort((a, b) => a - b);
      // A stack of writing lines has no columns to speak of and no rule over the
      // topmost one: the space is written *on* each line, so the first row is
      // the pitch of the stack laid above it.
      const xs = cols.length >= 2 ? cols : [box.x0, box.x1];
      if (cols.length < 2) {
        const gaps = [];
        for (let i = 0; i + 1 < ys.length; i++) gaps.push(ys[i] - ys[i + 1]);
        gaps.sort((a, b) => a - b);
        const pitch = gaps.length ? gaps[Math.floor(gaps.length / 2)] : RULE_ROW / 2;
        ys.unshift(ys[0] + pitch);
      }

      const cells = [];
      for (let i = 0; i + 1 < ys.length; i++) {
        for (let j = 0; j + 1 < xs.length; j++) {
          const cell = { x0: xs[j], x1: xs[j + 1], y0: ys[i + 1], y1: ys[i] };
          if (cell.x1 - cell.x0 < MIN_CELL || cell.y1 - cell.y0 < MIN_CELL) continue;
          if (saysSomething(cell)) continue;
          cells.push(cell);
        }
      }
      if (!cells.length) continue;
      for (const cell of cells) blocks.push({ ...cell, kind: "cell" });

      // The box an answer is set in: the largest silent panel around this grid
      // that is not around any other. A panel over two grids is the background
      // of a section, not the frame of one answer.
      let card = null;
      for (const panel of panels) {
        if (!encloses(panel, box)) continue;
        if (saysSomething(panel)) continue;
        const others = [...clusters.values()].some(
          (other) => other !== cluster && other.some((r) => contains(panel, r))
        );
        if (others) continue;
        if (!card || rectArea(panel) > rectArea(card)) card = panel;
      }
      if (card) blocks.push({ x0: card.x0, y0: card.y0, x1: card.x1, y1: card.y1, kind: "panel" });
    }
    return blocks;
  }

  /**
   * How far a question's region reaches into the space it is answered in.
   *
   * A region is still the question and nothing else -- a photograph, a dialogue
   * or a banner printed near it belongs to no activity -- but the blank the
   * reader is asked to fill in is part of what was asked, so it is taken.
   *
   * What stops the reach is other people's words. Blank paper is crossed, and
   * so is a picture: an answer box is routinely drawn with an illustration
   * standing over its lines, and the picture is part of the box rather than
   * something the question points at. Prose that is not this question's own,
   * and any other activity's label, is a wall.
   *
   * `adjacent`, used for the numbered items inside an activity, additionally
   * requires the space to follow straight on from the question: a sub-question
   * takes the cells of its own table row, not the answer card printed at the
   * foot of the activity, which belongs to the activity as a whole.
   */
  solutionFor(act, band, column, content, blocks, body, markers, adjacent) {
    if (!content || !blocks.length) return null;
    // The space a question is answered in is set where the question is: in its
    // own column, or across the page where the question itself runs across the
    // page. That is the same measure flowContent() holds the question's own
    // lines to, and it keeps a region out of the answer blanks belonging to the
    // column beside it wherever the page is not formally in columns there.
    const left = Math.min(column.x0, content.x0) - COLUMN_EDGE;
    const right = Math.max(column.x1, content.x1) + COLUMN_EDGE;
    const walls = body.filter(
      (it) => (isProse(it) && !act.members.includes(it)) || (markers.includes(it) && it !== act.marker)
    );
    let grown = null;
    for (const block of blocks) {
      if (adjacent !== undefined && block.kind === "panel") continue;
      // Inside the band: a band ends 2 pt above the next label, so the space
      // below that one is already the next question's to claim.
      const overlap = Math.min(block.y1, band.top) - Math.max(block.y0, band.bottom);
      if (overlap < 0.5 * (block.y1 - block.y0)) continue;
      const across = Math.min(block.x1, right) - Math.max(block.x0, left);
      if (across < 0.5 * (block.x1 - block.x0)) continue;
      if (band.shared) {
        const inCell = Math.min(block.x1, band.x1 + PAD) - Math.max(block.x0, band.x0 - PAD);
        if (inCell < 0.5 * (block.x1 - block.x0)) continue;
      }
      // The paper between the question and the block, over the width the two
      // share. A block beside the question -- the empty cells of its own row --
      // leaves no such gap at all.
      const gap = { top: content.y0, bottom: block.y1 };
      if (adjacent !== undefined && gap.top - gap.bottom > adjacent) continue;
      const blocked = walls.some((it) => {
        const cy = (it.y0 + it.y1) / 2;
        return cy < gap.top && cy > gap.bottom &&
               it.x1 > Math.min(content.x0, block.x0) && it.x0 < Math.max(content.x1, block.x1);
      });
      if (blocked) continue;
      grown = unionRect(grown, { x0: block.x0, y0: block.y0, x1: block.x1, y1: block.y1 });
    }
    return grown;
  }

  /** Which activity band does this rect belong to? Most overlap wins. */
  bestBandFor(rect, allBands) {
    let best = null;
    let bestScore = 0;
    for (const candidate of allBands) {
      const band = candidate.band;
      const ov = bandOverlap(rect, band.bottom, band.top);
      const horiz = Math.min(rect.x1, band.x1) - Math.max(rect.x0, band.x0);
      if (ov <= 0 || horiz <= 0) continue;
      const score = ov * horiz;
      if (score > bestScore) {
        bestScore = score;
        best = candidate;
      }
    }
    if (best) return best;
    // Zero-area runs (empty glyph boxes, hairline rules) score nothing above, but
    // still belong somewhere: fall back to the band containing their centre.
    const cx = (rect.x0 + rect.x1) / 2;
    const cy = (rect.y0 + rect.y1) / 2;
    return (
      allBands.find(
        (c) => cx >= c.band.x0 && cx <= c.band.x1 && cy <= c.band.top && cy >= c.band.bottom
      ) || null
    );
  }

  /**
   * Do these labels read as one complete list -- the first n entries of a single
   * alphabet, met in that order going down each column and then on to the next?
   *
   * Both halves matter. Completeness rejects a caption numbered on its own ("2"
   * beside one figure). Reading order rejects numbers that happen to run 1..n
   * but are not laid out as a list at all -- a seating plan, a set of labelled
   * tiles -- because those arrive shuffled however the page is read, while a
   * real list is in order under one reading or the other.
   */
  readsAsList(labels) {
    const kind = labels[0].label.kind;
    if (labels.some((it) => it.label.kind !== kind)) return false;

    // Down each column in turn: how a list of questions is set.
    const columns = this.clusterColumns(labels);
    columns.sort((a, b) => Math.min(...a.map((d) => d.x)) - Math.min(...b.map((d) => d.x)));
    const downColumns = columns.flatMap((c) => c.slice().sort((a, b) => b.y - a.y));

    // Along each row in turn: how a grid of tick boxes or word cards is numbered.
    const rows = [];
    for (const it of labels.slice().sort((a, b) => b.y - a.y || a.x - b.x)) {
      const row = rows[rows.length - 1];
      if (row && Math.abs(row[0].y - it.y) < 3) row.push(it);
      else rows.push([it]);
    }
    const alongRows = rows.flatMap((r) => r.slice().sort((a, b) => a.x - b.x));

    return this.countsFromOne(downColumns) || this.countsFromOne(alongRows);
  }

  /** Are these labels, in this order, exactly 1, 2, ... n (or a, b, c, ...)? */
  countsFromOne(ordered) {
    for (let i = 0; i < ordered.length; i++) {
      const value = ordered[i].label.value;
      if (i === 0) {
        if (value !== 1) return false;
        continue;
      }
      if (!(LABEL_SUCCESSORS.get(ordered[i - 1].label.value) || []).includes(value)) return false;
    }
    return true;
  }

  /**
   * The questions inside an activity. There are two ways a book marks them and a
   * page may offer both: labels the geometry showed indented under this
   * activity's level, and standalone digits set in the label font a step down
   * from it. Either is accepted only if it reads as a complete list, so a lone
   * numbered caption or a numbered diagram is not mistaken for one.
   */
  detectSubItems(act, pageNum, nestedLabels, solutions, body, markers) {
    // Numbered items are set in the label font one step down from the label size.
    const digits = act.members.filter(
      (it) =>
        it.clearLeft &&
        NUMBER_RE.test(it.text) &&
        this.markerFonts.has(it.font) &&
        it.size <= this.markerSize + 0.5 &&
        it.size >= this.markerSize - 2.5
    );
    // Labels the page's own geometry showed indented under this activity's level
    // are its sub-items whatever alphabet they are drawn from: "a) b) c)" under
    // "1." is the same structure as "1 2 3" under "a", only lettered. But one
    // activity may carry two alphabets side by side at the same indent -- the
    // steps of its practical ("1. adım" ...) with the questions that follow them
    // ("a) b) c)") -- and a group mixing them reads as no list at all, so each
    // alphabet is judged on its own.
    const indented = new Map();
    for (const it of act.members) {
      if (!nestedLabels.has(it)) continue;
      const kind = it.label.kind;
      if (!indented.has(kind)) indented.set(kind, []);
      indented.get(kind).push(it);
    }

    // Whichever reading yields a complete list wins; the longer one breaks a tie,
    // since a list interrupted halfway is the weaker explanation of the page.
    const chosen = [...indented.values(), digits]
      .filter((group) => group.length >= 2 && this.readsAsList(group))
      .sort((a, b) => b.length - a.length)[0];
    if (!chosen) return [];

    // Each part of the activity is an independent region, so questions are
    // clustered inside the part that holds them and never grow across the gutter
    // into a sibling piece.
    const out = [];
    act.parts.forEach((part, partIndex) => {
      const inPart = chosen.filter((d) => contains(part, d));
      if (!inPart.length) return;
      const members = act.members.filter((it) => contains(part, it));

      const clusters = this.clusterColumns(inPart);
      clusters.sort((a, b) => Math.min(...a.map((d) => d.x)) - Math.min(...b.map((d) => d.x)));

      clusters.forEach((cluster, idx) => {
        const left = Math.min(...cluster.map((d) => d.x)) - 4;
        const right = idx + 1 < clusters.length
          ? Math.min(...clusters[idx + 1].map((d) => d.x)) - 8
          : part.x1;
        const sorted = cluster.slice().sort((a, b) => b.y - a.y);
        // The rhythm the column is set in. A question may run over several
        // lines, but its lines follow one another at the leading; a blank much
        // wider than that is the end of the question, not a line of it.
        const maxGap = FLOW_LEADING * this.lineLeading(members, sorted[0].size);
        // How tall a question of this list is set. The next question puts a
        // floor under every entry but the last one, whose band runs on to the
        // foot of the activity; the space to write in down there is the
        // activity's own -- the table a whole list is categorised into, the card
        // its answers are collected on -- so the last question is held to the
        // same row height as its siblings when it looks for somewhere to write.
        const steps = [];
        for (let k = 0; k + 1 < sorted.length; k++) steps.push(sorted[k].y1 - sorted[k + 1].y1);
        steps.sort((a, b) => a - b);
        const rowHeight = steps.length ? steps[Math.floor(steps.length / 2)] : maxGap;
        sorted.forEach((digit, j) => {
          const top = digit.y1 + 2;
          const bottom = j + 1 < sorted.length ? sorted[j + 1].y1 + 2 : part.y0;
          const floor = j + 1 < sorted.length ? bottom : top - rowHeight;
          const band = members.filter((it) => {
            const cy = (it.y0 + it.y1) / 2;
            return cy <= top && cy >= bottom && it.x1 >= left && it.x0 <= right;
          });
          let rect = { x0: digit.x0, y0: digit.y0, x1: digit.x1, y1: digit.y1 };
          const text = [];
          // Walk down the band line by line and stop at the first blank wider
          // than the leading. The next question puts a floor under every
          // entry but the last one, which would otherwise run to the foot of the
          // activity and swallow whatever the list is followed by -- a dialogue,
          // a worked example, a figure caption -- so that a one-word answer ends
          // up hundreds of points tall while its siblings are one line.
          let baseline = digit.y;
          for (const line of groupLines(band)) {
            if (baseline - line.y > maxGap) break;
            // A line set larger than the list is a heading, so the list -- and
            // with it this question -- has ended, even where the heading follows
            // close enough to pass the blank-space test.
            if (Math.max(...line.items.map((it) => it.size)) > digit.size + 1) break;
            rect = unionRect(rect, line);
            for (const it of line.items) text.push(it.text);
            baseline = line.y;
          }
          // The blank this question is answered in: the empty cells of its own
          // row, or the line ruled directly under it. Only what follows straight
          // on from the question, so the answer card at the foot of an activity
          // stays the activity's and is not swallowed by its last item.
          const space = this.solutionFor(
            act, { top, bottom: floor, x0: left, x1: right, shared: true }, { x0: left, x1: right },
            rect, solutions, body, markers, maxGap
          );
          // Held inside the question's own band, less the padding every entry
          // is given below, so that two questions answered in two rows of one
          // table stay two hotspots: the padded region ends exactly where the
          // next question's begins instead of overlapping it.
          if (space) {
            rect = unionRect(rect, {
              x0: space.x0, x1: space.x1,
              y0: Math.max(space.y0, bottom + 4), y1: Math.min(space.y1, top - 4),
            });
          }
          out.push({
            id: `${act.id}-${digit.label.value}`,
            label: digit.text,
            number: digit.label.value,
            partIndex,
            pageNum,
            rect: {
              x0: Math.max(rect.x0 - 4, part.x0),
              y0: Math.max(rect.y0 - 4, part.y0),
              x1: Math.min(rect.x1 + 4, part.x1),
              y1: Math.min(rect.y1 + 4, part.y1),
            },
            text: text.join(" ").slice(0, 200),
          });
        });
      });
    });

    out.sort((a, b) => a.number - b.number);
    return out;
  }

  /**
   * The leading of a block of text: the typical distance from one baseline to
   * the next. It is the rhythm a question's own lines are set in, so a blank
   * appreciably wider than it is a break in the flow rather than a line of the
   * same question. Falls back to the type size where there are too few lines to
   * measure, which is the case for a one-line answer.
   */
  lineLeading(items, fallbackSize) {
    const lines = groupLines(items);
    const gaps = [];
    for (let i = 0; i + 1 < lines.length; i++) {
      const gap = lines[i].y - lines[i + 1].y;
      if (gap > 1) gaps.push(gap);
    }
    if (!gaps.length) return 1.3 * fallbackSize;
    gaps.sort((a, b) => a - b);
    return gaps[Math.floor(gaps.length / 2)];
  }

  buildHeadline(act) {
    const baseline = act.marker.y;
    // Items sharing the label's baseline can include the *other* column's text,
    // so walk right from the label and stop at the first big horizontal gap.
    const onBaseline = act.members
      .filter((it) => Math.abs(it.y - baseline) < 2.5 && it.x0 >= act.marker.x1 - 1)
      .sort((a, b) => a.x0 - b.x0);
    const firstLine = [];
    let cursor = act.marker.x1;
    for (const it of onBaseline) {
      if (it.x0 - cursor > 40) break;
      firstLine.push(it.text);
      cursor = it.x1;
    }

    if (firstLine.length === 0) {
      const near = act.members
        .filter((it) => it.y <= baseline + 1 && it.y > baseline - 30)
        .sort((a, b) => b.y - a.y || a.x - b.x)
        .slice(0, 3)
        .map((it) => it.text);
      return near.join(" ").slice(0, 140);
    }

    // Continue with following lines until a paragraph gap, the end of the
    // instruction sentence, or the start of the numbered items. Only items in
    // the instruction's own column count — an activity that spans two columns
    // would otherwise splice the other column's text into the headline.
    const columnLeft = onBaseline.length ? onBaseline[0].x0 : act.marker.x1;
    const following = act.members
      .filter((it) => it.y < baseline - 2 && Math.abs(it.x0 - columnLeft) < 30)
      .sort((a, b) => b.y - a.y);
    const parts = [firstLine.join(" ")];
    let prevY = baseline;
    for (const it of following) {
      if (prevY - it.y > 20) break;
      if (NUMBER_RE.test(it.text) && this.markerFonts.has(it.font)) break;
      if (/[.!?:]$/.test(parts[parts.length - 1])) break;
      if (it.y < prevY - 2) {
        parts.push(it.text);
        prevY = it.y;
      } else if (Math.abs(it.y - prevY) < 2.5) {
        parts.push(it.text);
      }
      if (parts.join(" ").length > 160) break;
    }
    return parts.join(" ").replace(/\s+/g, " ").trim().slice(0, 160);
  }

  applyOverrides(pageNum, activities) {
    let ov = this.overrides ? this.overrides[String(pageNum)] : null;
    if (!ov && this.overrides) {
      const resolved = this.resolveDocumentOverrides(this.overrides);
      if (resolved && resolved !== this.overrides) {
        ov = resolved[String(pageNum)];
      }
    }
    if (!ov) return activities;
    let out = activities;
    // Keyed by label ("a") for the common case, or by the region's own id
    // ("p36-e-2") where a page repeats a label and only one of the two is to be
    // corrected.
    if (Array.isArray(ov.drop)) {
      out = out.filter((a) => !ov.drop.includes(a.label) && !ov.drop.includes(a.id));
    }
    for (const act of out) {
      const patch = ov[act.id] || ov[act.label];
      if (patch && Array.isArray(patch.rect) && patch.rect.length === 4) {
        const [x0, y0, x1, y1] = patch.rect;
        act.rect = { x0, y0, x1, y1 };
        act.parts = [{ x0, y0, x1, y1 }];
      }
      if (patch && typeof patch.headline === "string") act.headline = patch.headline;
    }
    return out;
  }

  /* ------------------------------------------------------------------ */
  /* Navigation helpers                                                  */
  /* ------------------------------------------------------------------ */

  async activitiesForPage(pageNum, pdfjsLib) {
    const res = await this.analyzePage(pageNum, pdfjsLib);
    return res.activities;
  }

  /** Next / previous activity across page boundaries. dir is +1 or -1. */
  async step(activity, dir, pdfjsLib) {
    const list = await this.activitiesForPage(activity.pageNum, pdfjsLib);
    const idx = list.findIndex((a) => a.id === activity.id);
    const target = idx + dir;
    if (idx !== -1 && target >= 0 && target < list.length) return list[target];

    let p = activity.pageNum + dir;
    while (p >= 1 && p <= this.pdfDoc.numPages) {
      const acts = await this.activitiesForPage(p, pdfjsLib);
      if (acts.length) return dir > 0 ? acts[0] : acts[acts.length - 1];
      p += dir;
    }
    return null;
  }

  /**
   * Hit test in PDF user space; returns the smallest containing region as
   * `{ activity, partIndex, rect }`. Parts of one activity are separate regions,
   * so the caller learns *which* piece was hit and can zoom to just that piece.
   *
   * The geometry itself lives in `interactive-links.js`: a baked book is read
   * without this module ever being loaded, and the two must agree exactly.
   */
  hitTest(pageActivities, x, y) {
    return hitTestRegion(pageActivities, x, y);
  }
}
