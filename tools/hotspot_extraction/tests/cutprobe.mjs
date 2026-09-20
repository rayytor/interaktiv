/** Where exactly does an anchored region cut a drawn block? */
import path from "node:path";
import {
  root, ActivityDetector, openDocument, pdfjsLib,
  bookIdForFile, interactiveOges, unplacedAnchors,
} from "./_harness.mjs";

const file = path.resolve(process.argv[2]);
const WHOLE_TOL = 0.05;
const area = (r) => Math.max(0, r.x1 - r.x0) * Math.max(0, r.y1 - r.y0);
const ov = (a, b) => Math.max(0, Math.min(a.x1,b.x1)-Math.max(a.x0,b.x0)) * Math.max(0, Math.min(a.y1,b.y1)-Math.max(a.y0,b.y0));
const cuts = (r, b) => { const A = area(b); if (A<=0) return false; const s = ov(r,b)/A; return s>WHOLE_TOL && s<1-WHOLE_TOL; };

const bookId = bookIdForFile(file);
const doc = await openDocument(file);
const det = new ActivityDetector(doc, path.basename(file));
det.loadOverrides({});
det.diagnostics = new Map();
await det.calibrate();
for (let p = 1; p <= doc.numPages; p++) { await det.textItems(p); det.releasePage(p); }

const byPrinted = new Map();
for (const o of interactiveOges(bookId)) {
  if (!byPrinted.has(o.sayfano)) byPrinted.set(o.sayfano, []);
  byPrinted.get(o.sayfano).push(o);
}
det.anchorsByPage = new Map();
let found = 0;
for (let p = 1; p <= doc.numPages && found < 40; p++) {
  const printed = det.printedPageFor(p);
  const list = (printed !== null && byPrinted.get(printed)) || [];
  if (!list.length) { det.releasePage(p); continue; }
  let res = await det.analyzePage(p, pdfjsLib);
  const left = unplacedAnchors(res, list);
  if (left.length) {
    det.anchorsByPage.set(p, left.map(o => ({id:o.id, posx:o.posx, posy:o.posy})));
    det.releasePage(p);
    res = await det.analyzePage(p, pdfjsLib);
  }
  const diag = det.diagnostics.get(p);
  const W = res.pageWidth, H = res.pageHeight;
  const onSheet = (b) => b.x0 >= -2 && b.y0 >= -2 && b.x1 <= W+2 && b.y1 <= H+2;
  for (const act of res.activities) {
    if (!act.anchored) continue;
    const own = act.parts && act.parts.length ? act.parts : [act.rect];
    for (const rect of own) {
      const bad = (diag?.panels || []).filter((b) => {
        if (!onSheet(b)) return false;
        const along = Math.min(rect.y1,b.y1)-Math.max(rect.y0,b.y0);
        return along >= 0.5*(b.y1-b.y0) && cuts(rect, b);
      });
      if (!bad.length) continue;
      found += bad.length;
      console.log(`p${p} printed ${printed} ${act.id} rect=[${rect.x0.toFixed(0)},${rect.y0.toFixed(0)},${rect.x1.toFixed(0)},${rect.y1.toFixed(0)}] page ${W.toFixed(0)}x${H.toFixed(0)} tall=${((rect.y1-rect.y0)/H*100).toFixed(0)}%`);
      for (const b of bad) {
        console.log(`   panel [${b.x0.toFixed(0)},${b.y0.toFixed(0)},${b.x1.toFixed(0)},${b.y1.toFixed(0)}] share=${(ov(rect,b)/area(b)).toFixed(2)}`);
      }
    }
  }
  det.releasePage(p);
}
console.log(`total ${found}`);
