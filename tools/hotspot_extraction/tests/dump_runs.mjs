/**
 * Dump text runs of a page in reading order with font/size/position, plus the
 * labels the detector currently accepts, to see what is being missed.
 *
 *   node converter/tests/dump_runs.mjs <pdf> <page>
 */
import fs from "node:fs";
import path from "path";
import { fileURLToPath } from "node:url";

if (!Promise.withResolvers) {
  Promise.withResolvers = function () {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  };
}

const here = path.dirname(fileURLToPath(import.meta.url));
const pdfjsLib = await import(path.join(here, "../../../js/pdf.mjs"));
const { ActivityDetector } = await import(path.join(here, "../../../js/activities.js"));

const file = process.argv[2];
const pageNum = Number(process.argv[3]);
const doc = await pdfjsLib.getDocument({ data: new Uint8Array(fs.readFileSync(file)) }).promise;
const det = new ActivityDetector(doc, path.basename(file));
det.loadOverrides({});
await det.calibrate();

const items = await det.textItems(pageNum);
const res = await det.analyzePage(pageNum, pdfjsLib);
const detected = new Set();
for (const act of res.activities) {
  detected.add(act.id);
  for (const it of act.items || []) detected.add(it.id);
}

// group by baseline for readability
const rows = [];
for (const it of items.slice().sort((a, b) => b.y - a.y || a.x0 - b.x0)) {
  const row = rows[rows.length - 1];
  if (row && Math.abs(row.y - it.y) < 2.5) row.items.push(it);
  else rows.push({ y: it.y, items: [it] });
}
const isOpen = (it) => it.opensItem;
const isMarker = (it) => det.markerStyles && det.markerStyles.has(`${it.font}|${it.size.toFixed(1)}`);

for (const row of rows) {
  const parts = row.items
    .slice()
    .sort((a, b) => a.x0 - b.x0)
    .map((it) => {
      const flag = it.label ? (isOpen(it) ? "*" : isMarker(it) ? "+" : "l") : " ";
      return `${flag}[x${it.x0.toFixed(0)} ${it.size.toFixed(1)} ${it.face}] ${JSON.stringify(it.text.slice(0, 60))}`;
    });
  console.log(`y=${row.y.toFixed(1)}  ${parts.join("  ||  ")}`);
}
console.log(`\n--- detected on page ${pageNum}:`);
for (const act of res.activities) {
  console.log(`  ${act.id} "${act.label}" parts=${JSON.stringify(act.parts)}`);
  for (const item of act.items || []) console.log(`    ${item.id} "${item.label}" ${JSON.stringify(item.rect)}`);
}
