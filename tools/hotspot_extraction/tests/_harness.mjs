/**
 * Shared bootstrap for the headless harnesses.
 *
 * The vendored `pdf.min.mjs` is the browser build and expects a 2024-era
 * baseline, so anything running it under Node has to shim the same gap and
 * resolve the same paths. That was copied into every harness; it lives here
 * instead so a new one costs nothing.
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

export const here = path.dirname(fileURLToPath(import.meta.url));
// `tools/hotspot_extraction/tests/` is three levels below the app's own files.
export const root = path.join(here, "..", "..", "..");

let pdfjsLib = null;
try {
  pdfjsLib = await import(path.join(root, "js/pdf.mjs"));
} catch (_) {}

let ActivityDetector = null;
try {
  ({ ActivityDetector } = await import(path.join(root, "js/activities.js")));
} catch (_) {}

export { pdfjsLib, ActivityDetector };
export const { linkInteractiveOges, ogeLabel, unplacedAnchors } = await import(
  path.join(root, "js/interactive-links.js")
);

export function openDocument(file) {
  return pdfjsLib.getDocument({ data: new Uint8Array(fs.readFileSync(file)) }).promise;
}

/** The catalogue id of a PDF on disk: its filename, once symlinks are resolved. */
export function bookIdForFile(file) {
  const real = fs.realpathSync(file);
  const base = path.basename(real, ".pdf");
  return /^[a-f0-9-]{36}$/i.test(base) ? base : null;
}

/** The publisher's interactive entries for a book, or [] when none are cached. */
export function interactiveOges(bookId) {
  if (!bookId) return [];
  const metaPath = path.join(root, "activities_meta", `${bookId}.json`);
  if (!fs.existsSync(metaPath)) return [];
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
  return (meta.kitapogeList || []).filter(
    // `sayfaustuoge` is the publisher's own flag for an entry that sits on a
    // page. The ones without it carry sayfano 0 and posx/posy 0 -- book-level
    // activities that belong to no sheet, and would otherwise all be placed in
    // the top-left corner of the first one.
    (o) => o.ogeturu === 1 && o.data && o.sayfaustuoge && o.sayfano
  );
}

/** Parse "16", "16,23", "10-20" into a page list. */
export function pageList(spec, total) {
  if (!spec) return Array.from({ length: total }, (_, i) => i + 1);
  const out = [];
  for (const part of spec.split(",")) {
    const m = /^(\d+)-(\d+)$/.exec(part.trim());
    if (m) for (let p = +m[1]; p <= +m[2]; p++) out.push(p);
    else if (part.trim()) out.push(Number(part.trim()));
  }
  return out.filter((p) => p >= 1 && p <= total);
}
