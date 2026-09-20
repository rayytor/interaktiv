/**
 * Linking the publisher's interactive activities to the regions on the page.
 *
 * The reader knows two things about an activity and they come from different
 * places. The regions are detected from the sheet itself (`activities.js`): a
 * letter, a column, a rectangle in PDF user space. The interactive versions come
 * from the publisher's manifest (`activities_meta/<book>.json`), one entry per
 * activity, carrying a hand-typed title, the *printed* page it belongs to, and
 * the position of the icon the publisher's own reader draws for it, as a
 * percentage of the sheet.
 *
 * Neither identifies the activity on its own:
 *
 *   - the title carries the label, which is what tells `b` from `c`, but a page
 *     routinely runs two lists and prints `a` twice -- the left column running
 *     `d e f g` while the right column restarts -- so the label alone aliases
 *     two separate activities into one;
 *   - the icon position says which of the two it is, but on its own it is only
 *     a point in a margin, and two activities can start within a few percent of
 *     each other.
 *
 * So candidates are drawn by label and settled by position, and the result is an
 * assignment: one region to one manifest entry, never one entry claimed by two
 * regions.
 */

/** A title's printed page number and revision marks stripped off, or null. */
export function ogeLabel(baslik, printedPage) {
  let s = String(baslik || "").toLowerCase().trim();
  // The title usually repeats the printed page the activity is on -- "42/a",
  // and, typed without the separator, "56b-ed2". Only the page this entry
  // actually belongs to is stripped, so a book whose activities are numbered
  // rather than lettered keeps its number ("42/1" -> "1", not "").
  if (printedPage != null && Number.isFinite(printedPage)) {
    s = s.replace(new RegExp(`^0*${printedPage}\\s*[/\\-.:]?\\s*`), "").trim();
    // In Turkish books, the title often carries the 0-indexed or PDF page number
    // (printedPage - 1), e.g. "18" for page 19 or "41_5.Soru" for page 42.
    s = s.replace(new RegExp(`^0*${printedPage - 1}\\s*[/\\-.:_]?\\s*`), "").trim();
  }
  s = s.replace(/^\s*\d{1,4}\s*[/:]\s*/, "").trim();
  // Editorial revision marks the house appends: "-ed", " .ed", "ed2", and
  // "b ed ed" where a title was revised twice.
  let prev = null;
  while (prev !== s) {
    prev = s;
    s = s.replace(/[\s._\-]*\bed\s*\d*$/, "").trim();
    s = s.replace(/[\s._\-]+$/, "").trim();
  }
  const soru = /^(\d{1,2})\s*\.?\s*soru/i.exec(s);
  if (soru) return soru[1];
  if (/^[a-zçğıöşü]$/u.test(s) || /^\d{1,2}$/.test(s)) return s;
  // A page number typed into the label without a separator ("247n .ed" for n on
  // page 24) leaves digits in front of the label once the page strip misses.
  const tail = /^\d{1,4}\s*[/\-.:]?\s*([a-zçğıöşü])$/u.exec(s);
  return tail ? tail[1] : null;
}

/** Where the manifest's icon sits, as a percentage of the sheet. */
function ogeTop(oge) {
  return typeof oge.posy === "number" ? oge.posy : null;
}

function ogeOnLeft(oge) {
  return typeof oge.posx === "number" ? oge.posx < 50 : null;
}

/**
 * Hit test a point in PDF user space against a page's activities.
 *
 * It lives here, next to the rest of the region geometry, because the reader
 * needs it for a *baked* book too -- and a baked book is read without the
 * detector module ever being loaded. Returns the smallest containing region, so
 * that of two overlapping pieces (an activity that continues in the next
 * column, a question inside its activity) the one actually pointed at wins.
 *
 * @param {object} pageActivities  a page as `analyzePage` or a bake returns it
 * @param {number} x  PDF user space x
 * @param {number} y  PDF user space y
 * @returns {{activity: object, partIndex: number, rect: object}|null}
 */
export function hitTestRegion(pageActivities, x, y) {
  if (!pageActivities || !pageActivities.activities) return null;
  let best = null;
  let bestArea = Infinity;
  for (const act of pageActivities.activities) {
    const parts = act.parts && act.parts.length ? act.parts : [act.rect];
    parts.forEach((r, partIndex) => {
      if (x < r.x0 || x > r.x1 || y < r.y0 || y > r.y1) return;
      const area = (r.x1 - r.x0) * (r.y1 - r.y0);
      if (area < bestArea) {
        bestArea = area;
        best = { activity: act, partIndex, rect: r };
      }
    });
  }
  return best;
}

/**
 * How far from its region an icon may sit and still belong to it.
 *
 * The icon is set beside the activity it opens, near its first line, so the two
 * tops agree closely. A label match tolerates the wider drift, because the
 * letter is already strong evidence and the icon is sometimes hung beside the
 * second line of a long instruction; an entry with no label to match has only
 * its position to argue with and is held to the tight one.
 */
const LABELLED_DRIFT = 10;   // % of sheet height
const UNLABELLED_DRIFT = 5;  // % of sheet height
const WRONG_SIDE = 15;       // % penalty for an icon in the other margin

/**
 * Why an entry did not link, when a caller asks.
 *
 * The scorecard has to attribute every unmatched entry to one cause, and the
 * only place that knows the cause is the test that rejected the pair. Rebuilding
 * these conditions in the harness would mean two copies of the join rule drifting
 * apart, so the rule reports on itself instead. `explain` is left undefined by
 * the reader, which is what makes it free there.
 *
 * The reasons are ordered by how far the entry got, nearest-miss first, so a
 * bucket names the last gate an entry actually reached rather than whichever
 * region happened to be tested first.
 */
export const LINK_REASONS = [
  "wrong-side",        // label and distance agreed; the icon sits in the other margin
  "drift",             // the label agreed, or was absent; the icon is too far away
  "label-mismatch",    // both carry a label and they are different activities
  "confidence-gated",  // the marker font was never learned, so letters are not trusted
  "no-position",       // the entry carries no posy to argue with at all
];

/**
 * @param {object} pageData  the `analyzePage` result for one sheet
 * @param {object[]} oges    the manifest entries whose printed page is this sheet
 * @param {Map<string, object>} [explain]  optional: entry id -> why it did not link
 * @returns {Map<string, object>} activity id -> manifest entry
 */
export function linkInteractiveOges(pageData, oges, explain) {
  const links = new Map();
  // Every entry starts out having reached nothing; the walk below records the
  // furthest gate each one got to, and the settle marks the winners.
  if (explain) {
    for (const o of oges) {
      explain.set(String(o.id), { reasons: new Set(), candidates: 0, matched: false });
    }
  }
  const note = (oge, reason) => {
    if (explain) explain.get(String(oge.id)).reasons.add(reason);
  };
  if (!pageData || !pageData.activities || !oges || !oges.length) return links;

  const { pageWidth, pageHeight, confidence } = pageData;
  if (!pageWidth || !pageHeight) return links;

  const regions = pageData.activities.map((act) => ({
    act,
    label: String(act.label || "").toLowerCase(),
    // The top of the label-bearing piece: the icon is set against the start of
    // the activity, not against the middle of everything it covers.
    top: ((pageHeight - act.rect.y1) / pageHeight) * 100,
    // The margin the icon would be hung in is the one the activity's label
    // stands in, so the label's own edge decides the side, not the region's
    // middle -- a full-width region is still opened from the left.
    onLeft: act.rect.x0 < pageWidth / 2,
    fullWidth: act.rect.x0 < pageWidth * 0.35 && act.rect.x1 > pageWidth * 0.65,
    anchored: !!act.anchored,
  }));

  const pairs = [];
  oges.forEach((oge, oi) => {
    const label = ogeLabel(oge.baslik, oge.sayfano);
    const top = ogeTop(oge);
    if (top === null) { note(oge, "no-position"); return; }
    const onLeft = ogeOnLeft(oge);
    regions.forEach((region, ri) => {
      // If confidence is "none", do not link non-anchored (detected lettered) regions
      if (confidence === "none" && !region.anchored) return note(oge, "confidence-gated");
      // If confidence is "weak" and this manifest entry has no label, prefer anchors over dubious lettered regions
      if (confidence === "weak" && !label && !region.anchored) return note(oge, "confidence-gated");

      // A label on both sides that disagrees is a different activity, whatever
      // the geometry says.
      if (label && region.label && label !== region.label) return note(oge, "label-mismatch");
      const drift = Math.abs(top - region.top);
      const limit = label && region.label ? LABELLED_DRIFT : UNLABELLED_DRIFT;
      if (drift > limit) return note(oge, "drift");
      const sideMiss = !region.fullWidth && onLeft !== null && onLeft !== region.onLeft;
      if (sideMiss && !(label && region.label)) return note(oge, "wrong-side");
      if (explain) explain.get(String(oge.id)).candidates++;
      pairs.push({ oi, ri, cost: drift + (sideMiss ? WRONG_SIDE : 0) });
    });
  });

  // One region to one entry: the closest pair is settled first, and neither of
  // its two halves is offered again. Two activities that share a letter are two
  // activities, so the second `a` on the page cannot inherit the first one's
  // interactive version by being tested second.
  pairs.sort((a, b) => a.cost - b.cost);
  const usedOge = new Set();
  const usedRegion = new Set();
  for (const pair of pairs) {
    if (usedOge.has(pair.oi) || usedRegion.has(pair.ri)) continue;
    usedOge.add(pair.oi);
    usedRegion.add(pair.ri);
    links.set(regions[pair.ri].act.id, oges[pair.oi]);
    if (explain) explain.get(String(oges[pair.oi].id)).matched = true;
  }
  return links;
}

/**
 * The entries on a sheet that the label/drift assignment could not place.
 *
 * These are the activities a book titles descriptively rather than by label --
 * the whole of a Turkish subject book, and a handful in every ELT one. They are
 * handed to the detector as anchors, which grows a real region around the point
 * the publisher's icon marks (see `ActivityDetector.anchoredActivities`). Only
 * an entry with no usable position is left as a pin.
 *
 * @param {object} pageData  the page's regions, as analysed without anchors
 * @param {object[]} oges    the manifest entries whose printed page is this sheet
 * @returns {object[]} `{ id, posx, posy, oge }`, one per unplaced entry
 */
export function unplacedAnchors(pageData, oges) {
  if (!oges || !oges.length) return [];
  const claimed = new Set(
    [...linkInteractiveOges(pageData, oges).values()].map((o) => String(o.id))
  );
  return oges
    .filter(
      (o) =>
        !claimed.has(String(o.id)) &&
        typeof o.posx === "number" &&
        typeof o.posy === "number" &&
        // (0, 0) is what an entry that belongs to no point on the sheet carries;
        // anchoring there would put every one of them in the top-left corner.
        (o.posx !== 0 || o.posy !== 0)
    )
    .map((o) => ({ id: o.id, posx: o.posx, posy: o.posy, oge: o }));
}
