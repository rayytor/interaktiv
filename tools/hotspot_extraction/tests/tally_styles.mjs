/**
 * Full-book calibration tally: every label style's consecutive-run score over
 * ALL pages (not the calibration sample), with example pages. Mirrors
 * calibrate()'s scoring so thresholds can be reasoned about.
 *
 *   node converter/tests/tally_styles.mjs <pdf>
 */
import fs from "node:fs";
import path from "node:path";
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
const doc = await pdfjsLib.getDocument({ data: new Uint8Array(fs.readFileSync(file)) }).promise;
const det = new ActivityDetector(doc, path.basename(file));
det.loadOverrides({});

const buckets = new Map(); // key -> Map(pageNum -> entries[])
for (let p = 1; p <= doc.numPages; p++) {
  let items;
  try { items = await det.textItems(p); } catch { continue; }
  for (const it of items) {
    if (!it.label) continue;
    if (!det.hasHangingIndent(it, items)) continue;
    const key = `${it.font}|${it.size.toFixed(1)}`;
    if (!buckets.has(key)) buckets.set(key, new Map());
    const byPage = buckets.get(key);
    if (!byPage.has(p)) byPage.set(p, []);
    byPage.get(p).push(it);
  }
}

const out = [];
for (const [key, byPage] of buckets) {
  let score = 0;
  for (const entries of byPage.values()) score += det.sequenceScore(entries);
  const cut = key.lastIndexOf("|");
  out.push({ key, score, font: key.slice(0, cut), size: parseFloat(key.slice(cut + 1)), pages: byPage.size, byPage });
}
out.sort((a, b) => b.score - a.score);
for (const s of out.slice(0, 15)) {
  const pages = [...s.byPage.keys()].sort((a, b) => a - b).slice(0, 8);
  const example = [...s.byPage.values()][0]?.map((e) => e.text).slice(0, 6).join(" ");
  console.log(`score=${String(s.score).padStart(4)} pages=${String(s.pages).padStart(3)} size=${s.size.toFixed(1)} ${s.font}`);
  console.log(`        pages e.g. ${pages.join(",")}  labels e.g. ${JSON.stringify(example)}`);
}
