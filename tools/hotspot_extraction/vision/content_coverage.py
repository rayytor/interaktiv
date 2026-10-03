#!/usr/bin/env python3
"""
How much of a book can be opened from its hotspots.

The aim of the content regions (`scanner/content.py`) is that a teacher can
walk a whole page by its hotspots alone. This measures it, per book, on the
sheets that are content (between the front matter and the back matter, chapter
openers left out):

  covered     share of the lines of type whose centre lies in some region;
  activities  the same share counting activity regions only: what the bake
              reached before there were content regions;
  bare        sheets that carry type and have no region at all;
  overlaps    pairs of regions sharing more than 1 pt^2 (must be 0);
  tall        activity regions taller than 70 % of the sheet: tasks and forms
              that fill their page, kept whole since 2026-10-03;
  tall content  content regions that tall: a full-page figure or table.

A line of type no region holds is a running head, a page number that slipped
the footer band, a lone heading, or a hole.

Each book is measured in its own short-lived process (the machine has been
taken down by a whole-catalogue sweep in one process before).

Usage:
    python3 tools/hotspot_extraction/vision/content_coverage.py --root activities/books --only 7a92f6d0,cb558332
    python3 tools/hotspot_extraction/vision/content_coverage.py --root data/vision/bake-9 --all --out runs/coverage.json
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def measure(task: Dict[str, str]) -> Dict[str, Any]:
    import pymupdf

    from tools.hotspot_extraction.scanner import extract_page_primitives
    from tools.hotspot_extraction.scanner.layout import _text_lines, detect_layout
    from tools.hotspot_extraction.scanner.pipeline import body_spans_of
    from tools.hotspot_extraction.scanner.regions import TALL_REGION, rect_overlap

    book_id = task["book"]
    with open(Path(task["root"]) / book_id / "regions.json", encoding="utf-8") as fh:
        bake = json.load(fh)
    front = int(bake.get("frontMatterEnd") or 0)
    back = int(bake.get("backMatterStart") or 0)
    openers = set(bake.get("openers") or [])
    doc = pymupdf.open(PROJECT_ROOT / "books" / f"{book_id}.pdf")

    lines = covered = by_activity = bare = overlaps = tall = tall_content = sheets = 0
    regions = content = 0
    for p in range(front + 1, back if back else doc.page_count + 1):
        if p in openers:
            continue
        prim = extract_page_primitives(doc, p)
        layout = detect_layout(prim)
        body = [ln for ln in _text_lines(body_spans_of(prim, layout)) if any(s.text.strip() for s in ln["spans"])]
        acts = (bake["pages"].get(str(p)) or {}).get("activities") or []
        rects = [(tuple(r), a.get("kind", "activity")) for a in acts for r in (a.get("parts") or [a["rect"]])]
        regions += len(acts)
        content += sum(1 for a in acts if a.get("kind", "activity") != "activity")
        if not body:
            continue
        sheets += 1
        bare += 0 if rects else 1
        for ln in body:
            cx, cy = (ln["x0"] + ln["x1"]) / 2.0, (ln["y0"] + ln["y1"]) / 2.0
            inside = [kind for r, kind in rects if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]]
            lines += 1
            covered += 1 if inside else 0
            by_activity += 1 if "activity" in inside else 0
        for i, (a, kind) in enumerate(rects):
            if a[3] - a[1] > TALL_REGION * prim.height:
                tall += 1 if kind == "activity" else 0
                tall_content += 0 if kind == "activity" else 1
            overlaps += sum(1 for b, _ in rects[i + 1:] if rect_overlap(a, b) > 1.0)
        del prim
    doc.close()
    return {
        "book": book_id, "sheets": sheets, "lines": lines, "regions": regions, "content": content,
        "covered": round(covered / lines, 4) if lines else None,
        "activities": round(by_activity / lines, 4) if lines else None,
        "bare": bare, "overlaps": overlaps, "tall": tall, "tallContent": tall_content, "frontMatterEnd": front, "openers": len(openers),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(PROJECT_ROOT / "activities" / "books"), help="folder of <book-id>/regions.json")
    ap.add_argument("--only", help="comma-separated book id prefixes")
    ap.add_argument("--all", action="store_true", help="every book baked under --root")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", help="write the rows here as JSON")
    args = ap.parse_args()

    root = Path(args.root)
    books = sorted(d.name for d in root.iterdir() if (d / "regions.json").is_file()
                   and (PROJECT_ROOT / "books" / f"{d.name}.pdf").is_file())
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        books = [b for b in books if any(b.startswith(w) for w in wanted)]
    elif not args.all:
        ap.error("give --only or --all")

    rows: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1) as pool:
        for row in pool.map(measure, [{"book": b, "root": str(root)} for b in books]):
            rows.append(row)
            print(f"{row['book'][:8]}  sheets {row['sheets']:4d}  covered {row['covered']}  "
                  f"by activities {row['activities']}  bare {row['bare']:3d}  content {row['content']:5d}  "
                  f"overlaps {row['overlaps']}  tall {row['tall']}  tall content {row['tallContent']}")
    total = sum(r["lines"] for r in rows) or 1
    print(f"all  covered {sum((r['covered'] or 0) * r['lines'] for r in rows) / total:.4f}  "
          f"by activities {sum((r['activities'] or 0) * r['lines'] for r in rows) / total:.4f}  "
          f"bare {sum(r['bare'] for r in rows)}  overlaps {sum(r['overlaps'] for r in rows)}  tall {sum(r['tall'] for r in rows)}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
