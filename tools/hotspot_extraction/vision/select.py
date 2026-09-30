#!/usr/bin/env python3
"""
Phase 2 of the vision plan: choose the pages the teacher will label.

A stratified, seeded sample of about 45 pages per book from the rendered
corpus (`data/vision/pages/<book-id>/index.jsonl`). Within a book:

    15 %  `manifest`  the publisher lists an interactive activity on the page
                      (an `activities_meta` entry placed by the bake's folio
                      map); these are the pages the quality gate can join
    70 %  `baked`     no manifest entry, but the old rules bake found at least
                      one region on the page
    15 %  `empty`     neither: front matter, prose, so the student learns
                      "nothing here"

The three strata partition a book's pages. When a stratum is short the
shortfall is filled from `baked`, then `empty`. (PLAN.md described the
second stratum as "manifest entry but the bake found nothing"; that set is
empty in every book, because the bake grows a region from every publisher
icon, so the stratum was redefined to "carries a manifest entry" instead.) Two-page spreads (the cover,
width over 700 pt) are never selected. Held-out books (train/splits.json) are
sampled the same way and tagged `heldout`; their labels only ever score.

Writes `data/vision/select/round1.json`:

    {"seed": 20260928, "per_book": 45, "shares": [0.7, 0.15, 0.15],
     "books": {"<book-id>": {"title", "split", "page_count", "strata": {...},
                             "pages": [{"page": 31, "stratum": "baked", "why": "3 regions, 1 manifest entry"}]}},
     "total": 1188}

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/select.py
    .venv-vision/bin/python tools/hotspot_extraction/vision/select.py --per-book 45 --seed 20260928 --out data/vision/select/round1.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.scanner.score import interactive_oges  # noqa: E402

PAGES_DIR = PROJECT_ROOT / "data" / "vision" / "pages"
BAKES_DIR = PROJECT_ROOT / "activities" / "books"
SPLITS = PROJECT_ROOT / "tools" / "hotspot_extraction" / "train" / "splits.json"
DEFAULT_OUT = PROJECT_ROOT / "data" / "vision" / "select" / "round1.json"
DEFAULT_SEED = 20260928
DEFAULT_PER_BOOK = 45
SHARES = (0.70, 0.15, 0.15)          # baked, manifest, empty
STRATA = ("baked", "manifest", "empty")
SPREAD_WIDTH_PT = 700.0              # wider than this is a two-page spread


def read_index(book_id: str, pages_dir: Path = PAGES_DIR) -> List[Dict[str, Any]]:
    path = pages_dir / book_id / "index.jsonl"
    out: List[Dict[str, Any]] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def rendered_books(pages_dir: Path = PAGES_DIR) -> List[str]:
    return sorted(d for d in os.listdir(pages_dir) if (pages_dir / d / "index.jsonl").is_file())


def load_bake(book_id: str, bakes_dir: Path = BAKES_DIR) -> Optional[Dict[str, Any]]:
    path = bakes_dir / book_id / "regions.json"
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def manifest_pages(book_id: str, bake: Optional[Dict[str, Any]], root: Path = PROJECT_ROOT) -> Dict[int, int]:
    """PDF page -> number of interactive manifest entries the folio map puts there."""
    if not bake:
        return {}
    by_page = bake.get("folio", {}).get("byPage") or {}
    printed_to_pdf: Dict[int, List[int]] = {}
    for pdf_page, printed in by_page.items():
        if printed is not None:
            printed_to_pdf.setdefault(int(printed), []).append(int(pdf_page))
    counts: Dict[int, int] = {}
    for o in interactive_oges(book_id, str(root)):
        if o.get("posx") == 0 and o.get("posy") == 0:
            continue                      # belongs to no point on any sheet
        for p in printed_to_pdf.get(int(o["sayfano"]), []):
            counts[p] = counts.get(p, 0) + 1
    return counts


def stratify(index: Sequence[Dict[str, Any]], bake: Optional[Dict[str, Any]],
             manifest: Dict[int, int]) -> Dict[str, List[Tuple[int, str]]]:
    baked_pages = bake.get("pages", {}) if bake else {}
    strata: Dict[str, List[Tuple[int, str]]] = {s: [] for s in STRATA}
    for rec in index:
        p = int(rec["page"])
        if float(rec["width_pt"]) > SPREAD_WIDTH_PT:
            continue
        n_regions = len((baked_pages.get(str(p)) or {}).get("activities") or [])
        n_manifest = manifest.get(p, 0)
        why = f"{n_regions} region(s), {n_manifest} manifest entr{'y' if n_manifest == 1 else 'ies'}"
        if n_manifest:
            strata["manifest"].append((p, why))
        elif n_regions:
            strata["baked"].append((p, why))
        else:
            strata["empty"].append((p, why))
    return strata


def sample_book(book_id: str, strata: Dict[str, List[Tuple[int, str]]], per_book: int,
                seed: int, shares: Sequence[float] = SHARES) -> List[Dict[str, Any]]:
    """Seeded per book, so adding or removing a book never reshuffles the others."""
    rng = random.Random(f"{seed}:{book_id}")
    want = {s: int(round(per_book * sh)) for s, sh in zip(STRATA, shares)}
    # Rounding may leave the total one short or one over; settle it on `baked`.
    want["baked"] += per_book - sum(want.values())
    pools = {s: sorted(strata[s]) for s in STRATA}
    for s in STRATA:
        rng.shuffle(pools[s])
    picked: List[Dict[str, Any]] = []
    short = 0
    for s in STRATA:
        take = pools[s][:want[s]]
        pools[s] = pools[s][len(take):]
        short += want[s] - len(take)
        picked.extend({"page": p, "stratum": s, "why": why} for p, why in take)
    for s in ("baked", "empty", "manifest"):     # top up from what is left
        while short > 0 and pools[s]:
            p, why = pools[s].pop(0)
            picked.append({"page": p, "stratum": s, "why": why + " (top-up)"})
            short -= 1
    picked.sort(key=lambda r: r["page"])
    return picked


def build_selection(per_book: int = DEFAULT_PER_BOOK, seed: int = DEFAULT_SEED,
                    only: Optional[Sequence[str]] = None, pages_dir: Path = PAGES_DIR,
                    bakes_dir: Path = BAKES_DIR) -> Dict[str, Any]:
    with open(SPLITS) as fh:
        splits = json.load(fh)
    held = set(splits.get("heldOut") or [])
    titles = {k: v.get("title", k) for k, v in (splits.get("books") or {}).items()}
    books: Dict[str, Any] = {}
    for book_id in rendered_books(pages_dir):
        if only and not any(w and w.lower() in book_id.lower() for w in only):
            continue
        index = read_index(book_id, pages_dir)
        bake = load_bake(book_id, bakes_dir)
        manifest = manifest_pages(book_id, bake)
        strata = stratify(index, bake, manifest)
        pages = sample_book(book_id, strata, per_book, seed)
        books[book_id] = {
            "title": titles.get(book_id, book_id),
            "split": "heldout" if book_id in held else "train",
            "page_count": len(index),
            "baked": bake is not None,
            "strata": {s: len(v) for s, v in strata.items()},
            "picked": {s: sum(1 for r in pages if r["stratum"] == s) for s in STRATA},
            "pages": pages,
        }
    return {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seed": seed,
        "per_book": per_book,
        "shares": list(SHARES),
        "books": books,
        "total": sum(len(b["pages"]) for b in books.values()),
    }


def iter_pages(selection: Dict[str, Any]):
    """(book_id, page record) for every selected page, in book then page order."""
    for book_id in sorted(selection["books"]):
        for rec in selection["books"][book_id]["pages"]:
            yield book_id, rec


def print_table(sel: Dict[str, Any]) -> None:
    print(f"{'book':<10}{'split':<9}{'title':<36}{'pages':>6} | {'baked':>6}{'manif':>6}{'empty':>6} | picked b/m/e")
    for book_id, b in sorted(sel["books"].items()):
        s, p = b["strata"], b["picked"]
        print(f"{book_id[:8]:<10}{b['split']:<9}{b['title'][:34]:<36}{b['page_count']:>6} | "
              f"{s['baked']:>6}{s['manifest']:>6}{s['empty']:>6} | {p['baked']}/{p['manifest']}/{p['empty']}")
    print(f"total {sel['total']} pages over {len(sel['books'])} books, seed {sel['seed']}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-book", type=int, default=DEFAULT_PER_BOOK)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--only", default=None, help="comma-separated book id prefixes")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    sel = build_selection(args.per_book, args.seed, args.only.split(",") if args.only else None)
    print_table(sel)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as fh:
        json.dump(sel, fh, indent=1, ensure_ascii=False)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
