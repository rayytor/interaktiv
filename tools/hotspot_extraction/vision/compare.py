#!/usr/bin/env python3
"""
Phase 5 of the vision plan: the two engines side by side.

Scores the rules bake (`activities/books/`) and the vision bake
(`data/vision/bake/activities/books/`, written by
`scan.py --engine vision --out data/vision/bake/activities/books`) with the
scorecard's own `score_baked`, and prints one row per book and engine, then
the totals per split:

    regions  cuts  cuts/region  overlaps  slivers  tall  match  coverage

Violations are read first (a page with no hotspot is better than a page with
a wrong one); match rate and answer-space coverage second. Each book is
scored in its own short-lived process.

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/compare.py
    .venv-vision/bin/python tools/hotspot_extraction/vision/compare.py --only 0a3fbb41 --json tools/hotspot_extraction/vision/runs/phase5-compare.json
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

VISION_ROOT = PROJECT_ROOT / "data" / "vision" / "bake"
SELECTION = PROJECT_ROOT / "data" / "vision" / "select" / "round1.json"
ENGINES = ("rules", "vision")
COUNTS = ("regions", "cuts", "overlaps", "slivers", "tall", "oges", "matched")


def prepare_root(root: Path) -> None:
    """A bake root the scorer can read: its own bakes, the project's publisher manifests."""
    (root / "activities" / "books").mkdir(parents=True, exist_ok=True)
    link = root / "activities_meta"
    if not link.exists():
        os.symlink(PROJECT_ROOT / "activities_meta", link)


def _score(job: Tuple[str, str]) -> Dict[str, Any]:
    from tools.hotspot_extraction.scanner.score import score_baked
    book_id, root = job
    try:
        r = score_baked(book_id, root)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return {"book": book_id, "error": str(exc)}
    v, j = r["violations"], r["join"]
    return {"book": book_id, "regions": r["yield"]["regions"], "cuts": v["panelsCut"] + v["solutionsCut"],
            "overlaps": v["overlaps"], "slivers": v["slivers"], "tall": v["tall"],
            "oges": j["ogesOnReadPages"], "matched": j["matched"],
            "coverage": r["stats"]["solutionCoverage"], "blank": r["yield"]["blankPageShare"]}


def add_rates(row: Dict[str, Any]) -> Dict[str, Any]:
    row["cuts_per_region"] = round(row["cuts"] / row["regions"], 4) if row.get("regions") else None
    row["match"] = round(row["matched"] / row["oges"], 3) if row.get("oges") else None
    return row


def total(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in rows if "error" not in r]
    out: Dict[str, Any] = {k: sum(r[k] for r in ok) for k in COUNTS}
    out["books"] = len(ok)
    out["coverage"] = round(sum(r["coverage"] or 0.0 for r in ok) / len(ok), 3) if ok else None
    return add_rates(out)


def line(name: str, engine: str, r: Dict[str, Any]) -> str:
    if "error" in r:
        return f"{name:<18}{engine:<8}{r['error']}"

    def f(v: Any, spec: str) -> str:
        return format(v, spec) if v is not None else format("-", ">" + spec.split(".")[0].lstrip(">"))
    return (f"{name:<18}{engine:<8}{r['regions']:>8}{r['cuts']:>6}{f(r['cuts_per_region'], '>9.3f')}{r['overlaps']:>6}"
            f"{r['slivers']:>6}{r['tall']:>6}{f(r['match'], '>8.3f')}{f(r['coverage'], '>8.3f')}")


def engine_sheets(book_ids: List[str], roots: Dict[str, str], n: int, seed: int, out_dir: Path) -> List[Path]:
    """`n` seeded pages that carry a region under either engine, each drawn twice: rules, then vision."""
    import random
    from tools.hotspot_extraction.vision.contact_sheet import make_sheets

    bakes = {}
    for b in book_ids:
        for e in ENGINES:
            with open(Path(roots[e]) / "activities" / "books" / b / "regions.json", encoding="utf-8") as fh:
                bakes[(b, e)] = json.load(fh)["pages"]
    candidates = sorted((b, int(p)) for b in book_ids
                        for p in set(bakes[(b, "rules")]) | set(bakes[(b, "vision")])
                        if any((bakes[(b, e)].get(p) or {}).get("activities") for e in ENGINES))
    picked = sorted(random.Random(seed).sample(candidates, min(n, len(candidates))))
    items = []
    for b, p in picked:
        for e in ENGINES:
            page = bakes[(b, e)].get(str(p)) or {}
            boxes = []
            if page.get("activities"):
                with open(PROJECT_ROOT / "data" / "vision" / "pages" / b / "index.jsonl", encoding="utf-8") as fh:
                    index = next(r for r in map(json.loads, fh) if int(r["page"]) == p)
                sx, sy = index["width_px"] / page["pageWidth"], index["height_px"] / page["pageHeight"]
                for a in page["activities"]:
                    for r in a.get("parts") or [a["rect"]]:
                        boxes.append({"label": a.get("label"),
                                      "px": [r[0] * sx, (page["pageHeight"] - r[3]) * sy, r[2] * sx, (page["pageHeight"] - r[1]) * sy]})
            items.append({"label": {"book": b, "page": p, "boxes": boxes}, "caption": f"{b[:8]} p{p} {e} | {len(boxes)} region part(s)"})
    return make_sheets(items, out_dir, per_sheet=8, scale=0.45, prefix="engines")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vision-root", default=str(VISION_ROOT))
    ap.add_argument("--only", default=None, help="comma-separated book id prefixes")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--json", default=None, help="write the comparison here")
    ap.add_argument("--sheets", type=int, default=0, help="also draw this many pages, rules beside vision, for looking at")
    ap.add_argument("--sheets-out", default=str(PROJECT_ROOT / "data" / "vision" / "sheets" / "engines"))
    ap.add_argument("--seed", type=int, default=20260928)
    args = ap.parse_args(argv)

    roots = {"rules": str(PROJECT_ROOT), "vision": str(Path(args.vision_root))}
    prepare_root(Path(args.vision_root))
    books_dir = Path(args.vision_root) / "activities" / "books"
    book_ids = sorted(p.name for p in books_dir.iterdir() if (p / "regions.json").is_file())
    if args.only:
        prefixes = args.only.split(",")
        book_ids = [b for b in book_ids if any(b.startswith(p) for p in prefixes)]
    if not book_ids:
        print(f"no vision bakes under {books_dir}", file=sys.stderr)
        return 1
    split_of: Dict[str, str] = {}
    if SELECTION.is_file():
        with open(SELECTION, encoding="utf-8") as fh:
            split_of = {b: v.get("split", "train") for b, v in json.load(fh)["books"].items()}

    pairs = [(b, e) for b in book_ids for e in ENGINES]
    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1) as pool:
        scored = list(pool.map(_score, [(b, roots[e]) for b, e in pairs]))
    rows: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for (book_id, engine), r in zip(pairs, scored):
        rows.setdefault(book_id, {})[engine] = r if "error" in r else add_rates(r)

    print(f"{'book':<18}{'engine':<8}{'regions':>8}{'cuts':>6}{'cuts/reg':>9}{'ovl':>6}{'sliv':>6}{'tall':>6}{'match':>8}{'cover':>8}")
    for book_id in book_ids:
        for engine in ENGINES:
            print(line(f"{book_id[:8]} {split_of.get(book_id, '?')[:7]}", engine, rows[book_id][engine]))
    totals: Dict[str, Dict[str, Any]] = {}
    for split in sorted(set(split_of.get(b, "?") for b in book_ids)):
        totals[split] = {e: total([rows[b][e] for b in book_ids if split_of.get(b, "?") == split]) for e in ENGINES}
        for engine in ENGINES:
            print(line(f"TOTAL {split} ({totals[split][engine]['books']})", engine, totals[split][engine]))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"books": rows, "totals": totals, "splits": {b: split_of.get(b) for b in book_ids}}, fh, indent=1)
    if args.sheets:
        for path in engine_sheets(book_ids, roots, args.sheets, args.seed, Path(args.sheets_out)):
            print(f"  {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
