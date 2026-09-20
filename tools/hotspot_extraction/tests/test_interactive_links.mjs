/**
 * Unit tests for linking the publisher's interactive activities to the regions
 * detected on a page (js/interactive-links.js), and for the printed page number
 * that decides which page an entry belongs to (ActivityDetector.readFolio).
 *
 * These are the two joints the link is made at, and each used to be wrong:
 *
 *   1. the manifest counts in printed page numbers and the reader was looking
 *      the entries up by the sheet's ordinal in the file, so every activity was
 *      hung on the page before the one it belongs to;
 *   2. entries were matched to regions by their title alone, which both missed
 *      every title the house had typed in another shape ("56b-ed2" for b on
 *      page 56) and aliased the two activities a page prints under one letter.
 *
 * Run: node converter/tests/test_interactive_links.mjs
 */
import assert from "node:assert/strict";
import { ogeLabel, linkInteractiveOges } from "../../../js/interactive-links.js";
import { ActivityDetector } from "../../../js/activities.js";

const PAGE_W = 570;
const PAGE_H = 800;

/** A detected region, in PDF user space (y up). */
const region = (id, label, x0, top, height = 100, width = 220) => ({
  id,
  label,
  rect: { x0, y0: PAGE_H - top - height, x1: x0 + width, y1: PAGE_H - top },
});

const page = (...activities) => ({
  pageNum: 1,
  pageWidth: PAGE_W,
  pageHeight: PAGE_H,
  columns: [],
  activities,
});

/** A manifest entry: the title, the printed page, and the icon's position. */
const oge = (id, baslik, sayfano, posx, posy) => ({
  id,
  baslik,
  sayfano,
  posx,
  posy,
  ogeturu: 1,
  data: `guid-${id}`,
});

/** Top of a region as the manifest states positions: % of the sheet, from the top. */
const topPct = (r) => ((PAGE_H - r.rect.y1) / PAGE_H) * 100;

let passed = 0;
const ok = (name) => {
  passed++;
  console.log(`✔ ${name}`);
};

/* -------------------------------------------------------------------- */
/* 1. Titles                                                             */
/* -------------------------------------------------------------------- */
{
  assert.equal(ogeLabel("42/a", 42), "a");
  assert.equal(ogeLabel("42/b", 42), "b");
  // typed without the separator, and with the editorial revision mark attached
  assert.equal(ogeLabel("56b-ed2", 56), "b");
  assert.equal(ogeLabel("56e-ed3", 56), "e");
  assert.equal(ogeLabel("29/e ed", 29), "e");
  assert.equal(ogeLabel("33/h .ed2", 33), "h");
  assert.equal(ogeLabel("92/c.ed", 92), "c");
  assert.equal(ogeLabel("30/b ed ed", 30), "b");
  // a page number mistyped into the label still yields the label
  assert.equal(ogeLabel("247n .ed", 24), "n");
  // a book that numbers its activities keeps the number
  assert.equal(ogeLabel("42/1", 42), "1");
  assert.equal(ogeLabel("42/12", 42), "12");
  // whole-section activities have no label at all
  assert.equal(ogeLabel("47/Gamification", 47), null);
  assert.equal(ogeLabel("95/Warm Up", 95), null);
  assert.equal(ogeLabel("In-theme Activity", 105), null);
  assert.equal(ogeLabel("61/LD1", 61), null);
  ok("Test 1: titles yield their label whatever shape they were typed in");
}

/* -------------------------------------------------------------------- */
/* 2. A region takes the entry that carries its letter                   */
/* -------------------------------------------------------------------- */
{
  const a = region("p1-a", "a", 45, 80);
  const b = region("p1-b", "b", 45, 200);
  const data = page(a, b);
  const links = linkInteractiveOges(data, [
    oge("o-b", "50b-ed", 50, 5, topPct(b)),
    oge("o-a", "50/a", 50, 5, topPct(a)),
  ]);
  assert.equal(links.get("p1-a").id, "o-a");
  assert.equal(links.get("p1-b").id, "o-b");
  ok("Test 2: each region takes the entry carrying its own letter");
}

/* -------------------------------------------------------------------- */
/* 3. Two activities under one letter stay two activities                */
/* -------------------------------------------------------------------- */
{
  // The left column runs d, the right column restarts and prints d again.
  const left = region("p1-d", "d", 45, 120);
  const right = region("p1-d-2", "d", 300, 520);
  const data = page(left, right);
  const links = linkInteractiveOges(data, [
    oge("o-right", "35/d", 35, 93, topPct(right)),
  ]);
  assert.equal(links.size, 1, "one entry links to one region");
  assert.equal(links.get("p1-d-2").id, "o-right");
  assert.equal(links.get("p1-d"), undefined, "the other d must stay plain");
  ok("Test 3: two activities sharing a letter are told apart by position");
}

/* -------------------------------------------------------------------- */
/* 4. One entry is never claimed by two regions                          */
/* -------------------------------------------------------------------- */
{
  const first = region("p1-a", "a", 45, 100);
  const second = region("p1-a-2", "a", 300, 130);
  const data = page(first, second);
  const links = linkInteractiveOges(data, [
    oge("o-a", "20/a", 20, 5, topPct(first)),
  ]);
  assert.equal(links.size, 1);
  assert.equal(links.get("p1-a").id, "o-a");
  ok("Test 4: an entry is spent once, on its nearest region");
}

/* -------------------------------------------------------------------- */
/* 5. A letter that disagrees is never linked, however close it sits     */
/* -------------------------------------------------------------------- */
{
  const c = region("p1-c", "c", 45, 300);
  const data = page(c);
  const links = linkInteractiveOges(data, [oge("o-f", "12/f", 12, 5, topPct(c))]);
  assert.equal(links.size, 0);
  ok("Test 5: a disagreeing letter is not linked on position alone");
}

/* -------------------------------------------------------------------- */
/* 6. A titled whole-section activity links only where it sits exactly   */
/* -------------------------------------------------------------------- */
{
  const d = region("p1-d", "d", 300, 600);
  const data = page(d);
  const near = linkInteractiveOges(data, [
    oge("o-in", "In-theme Activity", 49, 78, topPct(d) + 1),
  ]);
  assert.equal(near.get("p1-d").id, "o-in", "an icon set against the region links");

  const far = linkInteractiveOges(data, [
    oge("o-in", "In-theme Activity", 49, 78, topPct(d) + 20),
  ]);
  assert.equal(far.size, 0, "an icon well away from it does not");
  ok("Test 6: an entry with no label is held to the tight distance");
}

/* -------------------------------------------------------------------- */
/* 7. An unlabelled entry never crosses the gutter                       */
/* -------------------------------------------------------------------- */
{
  const leftRegion = region("p1-a", "a", 45, 600);
  const data = page(leftRegion);
  const links = linkInteractiveOges(data, [
    oge("o-warm", "Warm Up", 39, 93, topPct(leftRegion)),
  ]);
  assert.equal(links.size, 0);
  ok("Test 7: an unlabelled entry in the other margin is not linked");
}

/* -------------------------------------------------------------------- */
/* 8. The printed page number is read off the sheet                      */
/* -------------------------------------------------------------------- */
{
  const det = new ActivityDetector({ numPages: 10 }, "book.pdf");
  const item = (text, x, y, size = 10) => ({
    text,
    x,
    y,
    x0: x,
    x1: x + text.length * size * 0.5,
    y0: y - size * 0.25,
    y1: y + size * 0.85,
    size,
  });

  // The folio sits alone in the foot margin; the theme number in the head and
  // the numbered questions in the body are not it.
  const items = [
    item("41", 502, 40),
    item("2", 88, 745, 12),
    item("1", 303, 521),
    item("2", 303, 490),
  ];
  assert.equal(det.readFolio(items, PAGE_W, PAGE_H), 41);

  det.noteFolio(42, items, PAGE_W, PAGE_H);
  assert.equal(det.printedPageFor(42), 41, "a sheet that prints a folio uses it");
  assert.equal(
    det.printedPageFor(60),
    59,
    "a sheet that prints none is placed by the offset the rest of the book agrees on"
  );

  // A page with nothing in its margins leaves the mapping alone.
  const bare = new ActivityDetector({ numPages: 10 }, "book.pdf");
  assert.equal(bare.readFolio([item("7", 300, 400)], PAGE_W, PAGE_H), null);
  assert.equal(bare.printedPageFor(3), null, "unknown is not guessed at as 3");
  ok("Test 8: the printed page number is read from the margin, not assumed");
}

console.log(`\nAll ${passed} tests passed successfully! 🎉`);
