#!/usr/bin/env python3
"""
The cut census: where a region's edge lies against the blocks it slices.

`score_baked` says how many drawn blocks the regions cut; this says *how*. A cut
is filed by the block's kind (answer space or panel) and by where the region's
horizontal edges fall relative to the block:

- `straddle`: both edges inside the block's vertical span -- the region lies
  within a block that runs past it at both ends, so the block spans several
  questions (a welded answer grid, a tinted background).
- `bottom-in-block` / `top-in-block`: one edge inside, so the region stops
  part-way down (or starts part-way into) a block it should take whole or clear.
- `side-only`: neither edge inside, so the slice is lateral -- a column or
  measure edge running through a block that reaches into the gutter.

Those four classes are what the Phase 3 briefs target, so every phase measures
them the same way, with this file. It reads bakes and their diagnostics sidecar
only and runs no detector. Each book is read in its own short-lived child, like
every other batch step in this project.

The census applies the scorer's `takeable` filter but not its "panel encloses
more than one marker" filter, so its panel count can differ slightly from
`score_baked`'s. Use `score_baked` (the scorecard) for the official number and
the census for the breakdown.

Usage:
    python3 tools/hotspot_extraction/train/census.py
    python3 tools/hotspot_extraction/train/census.py --only 79cbecfa --json out.json
    python3 tools/hotspot_extraction/train/census.py --compare runs/phase0-census.json
"""

import argparse
from concurrent.futures import ProcessPoolExecutor
import gzip
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.hotspot_extraction.scanner import score as S  # noqa: E402
from tools.hotspot_extraction.scanner.regions import cuts  # noqa: E402
from tools.hotspot_extraction.train import cache as C  # noqa: E402

KINDS = ("solutions", "panels")
WHERE = ("straddle", "side-only", "bottom-in-block", "top-in-block")


def classify(region, block) -> str:
    """Where a cutting region's horizontal edges fall against the block (y-up)."""
    bot_in = block[1] < region[1] < block[3]
    top_in = block[1] < region[3] < block[3]
    if bot_in and top_in:
        return "straddle"
    if bot_in:
        return "bottom-in-block"
    if top_in:
        return "top-in-block"
    return "side-only"


def census_book(book_id: str, root: str = _ROOT) -> Optional[Dict[str, Any]]:
    """One book's census, or None when it has no bake or no diagnostics sidecar."""
    d = os.path.join(root, "activities", "books", book_id)
    reg_path = os.path.join(d, "regions.json")
    diag_path = os.path.join(d, "diagnostics.json.gz")
    if not (os.path.isfile(reg_path) and os.path.isfile(diag_path)):
        return None
    with open(reg_path, encoding="utf-8") as f:
        reg = json.load(f)
    with gzip.open(diag_path, "rt", encoding="utf-8") as f:
        diag = json.load(f)
    dpages = diag.get("pages", diag)

    row: Dict[str, Any] = {
        "book": book_id,
        "regions": 0,
        "cuts": 0,
        **{k: 0 for k in KINDS},
        **{w: 0 for w in WHERE},
    }
    for pno, page in reg["pages"].items():
        row["regions"] += len(page["activities"])
        dp = dpages.get(pno)
        if not dp:
            continue
        W, H = page["pageWidth"], page["pageHeight"]
        blocks = {kind: [tuple(x) for x in dp.get(kind, [])] for kind in KINDS}
        for act in page["activities"]:
            for r in act.get("parts") or [act["rect"]]:
                r = tuple(r)
                for kind in KINDS:
                    for b in S.takeable(blocks[kind], r, W, H, kind == "panels"):
                        if not cuts(r, b, whole_tol=S.WHOLE_TOL):
                            continue
                        row["cuts"] += 1
                        row[kind] += 1
                        row[classify(r, b)] += 1
    return row


def run(book_ids: Sequence[str], workers: int = 2, root: str = _ROOT) -> Dict[str, Any]:
    """The census over `book_ids`, one child per book, with the corpus total."""
    with ProcessPoolExecutor(max_workers=max(1, workers), max_tasks_per_child=1) as pool:
        rows = [r for r in pool.map(census_book, book_ids, [root] * len(book_ids)) if r]
    total: Dict[str, Any] = {"book": "TOTAL"}
    for key in ("regions", "cuts", *KINDS, *WHERE):
        total[key] = sum(r[key] for r in rows)
    return {"books": rows, "total": total}


COLUMNS = ("regions", "cuts", "solutions", "panels", *WHERE)
HEADERS = ("regions", "cuts", "answer", "panel", "straddle", "side", "bottom", "top")


def print_table(result: Dict[str, Any], before: Optional[Dict[str, Any]] = None) -> None:
    """Per-book table; with `before`, each cell reads `before→after` where they differ."""
    prev = {r["book"]: r for r in (before or {}).get("books", [])}
    # A total is only comparable to a total over the same books.
    if before and set(prev) == {r["book"] for r in result["books"]}:
        prev["TOTAL"] = before["total"]

    def cell(row, key):
        old = prev.get(row["book"], {}).get(key)
        if before is None or old is None or old == row[key]:
            return str(row[key])
        return f"{old}→{row[key]}"

    print("| book | " + " | ".join(HEADERS) + " |")
    print("| --- |" + " ---: |" * len(HEADERS))
    for row in [*result["books"], result["total"]]:
        name = f"**{row['book']}**" if row["book"] == "TOTAL" else row["book"]
        print(f"| {name} | " + " | ".join(cell(row, k) for k in COLUMNS) + " |")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", help="book ids (default: every book with a cache shard)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--json", help="write the census here")
    ap.add_argument("--compare", help="an earlier --json census to print differences against")
    args = ap.parse_args(argv)

    cached = C.cached_books()
    books = [
        next((b for b in cached if b.startswith(p)), p) for p in args.only
    ] if args.only else cached
    result = run(books, workers=args.workers)
    before = None
    if args.compare:
        with open(args.compare, encoding="utf-8") as f:
            before = json.load(f)
    print_table(result, before)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
