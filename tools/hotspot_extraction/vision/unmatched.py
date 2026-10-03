#!/usr/bin/env python3
"""
Why publisher entries go unmatched in the vision bake.

For every interactive publisher entry (`activities_meta`), the scorecard's own
join (`score_baked`) files it in one bucket per engine. This lists the buckets
per split and per book for both bakes, and for each entry the vision bake fails
to match it looks at the page itself:

    no box on page    the student drew nothing on that page
    icon in a box     the icon stands inside or level with a vision region, but
                      the join gave that region to nothing (its top is further
                      from the icon than the join allows, or another entry won it)
    box elsewhere     the page has regions, none of them at the icon

and, where the rules bake matched the same entry, how much of the rules region
a vision region covers (the student found the activity or it did not).

One short-lived process per book. Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/unmatched.py --json tools/hotspot_extraction/vision/runs/phase6-unmatched-student-6.json
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

VISION_ROOT = PROJECT_ROOT / "data" / "vision" / "bake"
SPLITS = PROJECT_ROOT / "tools" / "hotspot_extraction" / "train" / "splits.json"
LEVEL_PCT = 5.0          # an icon within this much of a region's top or bottom is "at" it (the join's unlabelled drift)


def _buckets(book_id: str, root: str) -> Dict[str, str]:
    """entry id -> bucket, read off the scorer's own tally."""
    from tools.hotspot_extraction.scanner import score
    kept: Dict[str, str] = {}
    finish = score.finish_row

    def spy(t: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
        row = finish(t, ctx)
        kept.update(t.bucket_of)
        return row
    score.finish_row = spy
    try:
        score.score_baked(book_id, root)
    finally:
        score.finish_row = finish
    return kept


def _pages(book_id: str, root: Path) -> Dict[str, Any]:
    with open(root / "activities" / "books" / book_id / "regions.json", encoding="utf-8") as fh:
        return json.load(fh)


def _pct(rect: List[float], w: float, h: float) -> Tuple[float, float, float, float]:
    """A y-up point rect as (left, top, right, bottom) in % of the sheet, top-down."""
    return rect[0] / w * 100, (h - rect[3]) / h * 100, rect[2] / w * 100, (h - rect[1]) / h * 100


def _book(job: Tuple[str, str]) -> Dict[str, Any]:
    from tools.hotspot_extraction.scanner.score import interactive_oges
    book_id, vision_root = job
    try:
        rules_b = _buckets(book_id, str(PROJECT_ROOT))
        vision_b = _buckets(book_id, vision_root)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return {"book": book_id, "error": str(exc)}
    rules, vision = _pages(book_id, PROJECT_ROOT), _pages(book_id, Path(vision_root))
    printed_to_pdf: Dict[int, List[int]] = {}
    for pdf_page, printed in (vision["folio"]["byPage"] or {}).items():
        if printed is not None:
            printed_to_pdf.setdefault(int(printed), []).append(int(pdf_page))
    rules_region: Dict[str, Tuple[int, List[float]]] = {}
    for p, page in (rules.get("pages") or {}).items():
        for a in page.get("activities") or []:
            oid = a.get("ogeId") or (a["id"].split("-oge-")[1] if "-oge-" in a.get("id", "") else None)
            if oid:
                rules_region[str(oid)] = (int(p), a["rect"])

    entries = []
    for o in interactive_oges(book_id, str(PROJECT_ROOT)):
        oid = str(o["id"])
        vb, rb = vision_b.get(oid, "unresolved-page"), rules_b.get(oid, "unresolved-page")
        row: Dict[str, Any] = {"id": oid, "printed": o.get("sayfano"), "rules": rb, "vision": vb}
        if vb != "ok":
            where, gap, page_no = "no box on page", None, None
            for p in printed_to_pdf.get(int(o.get("sayfano") or -1), []):
                page = (vision.get("pages") or {}).get(str(p)) or {}
                acts = page.get("activities") or []
                if not acts:
                    continue
                page_no, where = p, "box elsewhere"
                w, h = page["pageWidth"], page["pageHeight"]
                x, y = float(o.get("posx") or 0), float(o.get("posy") or 0)
                for a in acts:
                    left, top, right, bottom = _pct(a["rect"], w, h)
                    d = 0.0 if top <= y <= bottom else min(abs(y - top), abs(y - bottom))
                    if gap is None or d < gap:
                        gap = d
                    if top - LEVEL_PCT <= y <= bottom + LEVEL_PCT:
                        where = "icon in a box" if left <= x <= right and top <= y <= bottom else \
                                ("icon beside a box" if where != "icon in a box" else where)
                break
            row.update(where=where, page=page_no, gap=None if gap is None else round(gap, 1))
            if oid in rules_region and rb == "ok":
                rp, rr = rules_region[oid]
                area = max(1e-6, (rr[2] - rr[0]) * (rr[3] - rr[1]))
                best = 0.0
                for a in ((vision.get("pages") or {}).get(str(rp)) or {}).get("activities") or []:
                    v = a["rect"]
                    iw, ih = min(rr[2], v[2]) - max(rr[0], v[0]), min(rr[3], v[3]) - max(rr[1], v[1])
                    if iw > 0 and ih > 0:
                        best = max(best, iw * ih / area)
                row["rules_region_covered"] = round(best, 2)
        entries.append(row)
    return {"book": book_id, "entries": entries}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vision-root", default=str(VISION_ROOT))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    with open(SPLITS) as fh:
        held = set(json.load(fh).get("heldOut") or [])
    books = sorted(p.name for p in (Path(args.vision_root) / "activities" / "books").iterdir() if (p / "regions.json").is_file())
    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1) as pool:
        rows = list(pool.map(_book, [(b, args.vision_root) for b in books]))

    out: Dict[str, Any] = {"books": {}, "splits": {}}
    for split in ("heldout", "train"):
        ents = [(r["book"], e) for r in rows if "entries" in r and (r["book"] in held) == (split == "heldout") for e in r["entries"]]
        n = len(ents)
        vb = Counter(e["vision"] for _, e in ents)
        rb = Counter(e["rules"] for _, e in ents)
        miss = [e for _, e in ents if e["vision"] != "ok" and e["vision"] != "unresolved-page"]
        where = Counter(e["where"] for e in miss)
        cross = Counter((e["vision"], e["where"]) for e in miss)
        cov = [e["rules_region_covered"] for e in miss if "rules_region_covered" in e]
        found = sum(1 for c in cov if c >= 0.5)
        print(f"\n== {split}: {n} publisher entries")
        print("  bucket            rules  vision")
        for b in sorted(set(vb) | set(rb), key=lambda b: -vb[b]):
            print(f"  {b:<17}{rb[b]:>6}{vb[b]:>8}")
        print(f"  unmatched by vision, on a read page: {len(miss)}")
        for k, v in where.most_common():
            print(f"    {k:<18}{v:>5}   " + ", ".join(f"{b} {c}" for (b, w), c in cross.most_common() if w == k))
        print(f"  of {len(cov)} the rules bake matched: a vision region covers half or more of the rules region for {found}, "
              f"less for {len(cov) - found}")
        out["splits"][split] = {"entries": n, "vision": dict(vb), "rules": dict(rb), "where": dict(where),
                                "cross": {f"{b} | {w}": c for (b, w), c in cross.items()},
                                "rules_matched": len(cov), "rules_region_half_covered": found}
    print(f"\n{'book':<10}{'split':<9}{'entries':>8}{'rules ok':>9}{'vision ok':>10}{'no box':>8}{'in box':>8}{'beside':>8}{'elsewh.':>8}")
    for r in rows:
        if "error" in r:
            print(f"{r['book'][:8]:<10}{r['error']}")
            continue
        es = r["entries"]
        if not es:
            continue
        w = Counter(e.get("where") for e in es if e["vision"] != "ok")
        print(f"{r['book'][:8]:<10}{'heldout' if r['book'] in held else 'train':<9}{len(es):>8}"
              f"{sum(e['rules'] == 'ok' for e in es):>9}{sum(e['vision'] == 'ok' for e in es):>10}"
              f"{w['no box on page']:>8}{w['icon in a box']:>8}{w['icon beside a box']:>8}{w['box elsewhere']:>8}")
        out["books"][r["book"]] = es
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, ensure_ascii=False)
        print("wrote", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
