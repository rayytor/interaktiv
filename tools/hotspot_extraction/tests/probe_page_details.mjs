import path from "node:path";
import { root, ActivityDetector, openDocument, pdfjsLib, interactiveOges, unplacedAnchors } from "./_harness.mjs";

const file = "books/1cc573f6-324a-470d-8452-bbf6d3fd0a23.pdf";
const doc = await openDocument(file);
const det = new ActivityDetector(doc, "1cc573f6-324a-470d-8452-bbf6d3fd0a23.pdf");
det.loadOverrides({});
det.diagnostics = new Map();
await det.calibrate();
for (let p = 1; p <= doc.numPages; p++) { await det.textItems(p); det.releasePage(p); }

const byPrinted = new Map();
for (const o of interactiveOges("1cc573f6-324a-470d-8452-bbf6d3fd0a23")) {
  if (!byPrinted.has(o.sayfano)) byPrinted.set(o.sayfano, []);
  byPrinted.get(o.sayfano).push(o);
}
det.anchorsByPage = new Map();

for (const p of [32, 52, 64, 72, 102]) {
  const printed = det.printedPageFor(p);
  const list = byPrinted.get(printed) || [];
  let res = await det.analyzePage(p, pdfjsLib);
  const left = unplacedAnchors(res, list);
  if (left.length) {
    det.anchorsByPage.set(p, left.map(o => ({id:o.id, posx:o.posx, posy:o.posy})));
    det.releasePage(p);
    res = await det.analyzePage(p, pdfjsLib);
  }
  const diag = det.diagnostics.get(p);
  console.log("=== Page " + p + " (printed " + printed + ") ===");
  for (const act of res.activities) {
    if (!act.anchored) continue;
    console.log("Act " + act.id + ": rect=", act.rect, "parts=", act.parts);
  }
  console.log("Panels on page " + p + ":", diag?.panels);
  det.releasePage(p);
}
