/**
 * Headless activity detection dump — the before/after diff tool.
 *
 * Runs the real ActivityDetector over a PDF with the vendored PDF.js build and
 * prints one line per activity and per question, so a change to the detection
 * algorithm can be diffed page by page across a whole book:
 *
 *   node converter/tests/detect.mjs pdf_parts/matematik.pdf > /tmp/after.txt
 *   diff /tmp/before.txt /tmp/after.txt
 *
 * Optional second argument limits the run to a page list or range: "16", "16,23",
 * "10-20".
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// The vendored pdf.min.mjs is the browser build and expects a 2024-era baseline;
// Node 20 has no Promise.withResolvers.
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

const defaultFile = [
  path.join(here, "../../../books/full_pdf.pdf"),
  path.join(here, "../../../books/0e966773-5012-4f57-8be5-d892e8c75f22.pdf"),
  path.join(here, "../../../pdf_parts/full_pdf.pdf"),
].find((p) => fs.existsSync(p)) || path.join(here, "../../../books/full_pdf.pdf");

const file = process.argv[2] || defaultFile;
const doc = await pdfjsLib.getDocument({ data: new Uint8Array(fs.readFileSync(file)) }).promise;

function pageList(spec, total) {
  if (!spec) return Array.from({ length: total }, (_, i) => i + 1);
  const out = [];
  for (const part of spec.split(",")) {
    const m = /^(\d+)-(\d+)$/.exec(part.trim());
    if (m) for (let p = +m[1]; p <= +m[2]; p++) out.push(p);
    else if (part.trim()) out.push(Number(part.trim()));
  }
  return out.filter((p) => p >= 1 && p <= total);
}

const det = new ActivityDetector(doc, path.basename(file));
det.loadOverrides({});          // detection only: no hand corrections in the diff
await det.calibrate();

const f = (r) => `[${r.x0.toFixed(1)} ${r.y0.toFixed(1)} ${r.x1.toFixed(1)} ${r.y1.toFixed(1)}]`;
let activities = 0;
let questions = 0;
for (const p of pageList(process.argv[3], doc.numPages)) {
  const res = await det.analyzePage(p, pdfjsLib);
  if (!res.activities.length) continue;
  console.log(`page ${p} columns=${res.columns.map((c) => `${c.x0.toFixed(0)}..${c.x1.toFixed(0)}`).join(" ")}`);
  for (const act of res.activities) {
    activities++;
    console.log(`  ${act.id} "${act.label}" col=${act.column} ${act.parts.map(f).join(" ")}`);
    for (const item of act.items) {
      questions++;
      console.log(`    ${item.id} part=${item.partIndex} ${f(item.rect)}`);
    }
  }
}
console.log(`total ${activities} activities, ${questions} questions in ${path.basename(file)}`);
