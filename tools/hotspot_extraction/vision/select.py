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

Round 2 (`--round 2`, Phase 6 of the plan) is active learning: instead of a
random sample it takes, from the training books only, the unlabelled pages the
student is least sure of. It reads the student's boxes down to a low floor
(`infer.py --conf 0.2 --out data/vision/pred-doubt`) and ranks a page by

    doubt     its most doubtful box: a box scoring 0.5 is as doubtful as a box
              gets, one at 0.9 or more is not doubtful at all
    missing   the publisher lists more activities on the page than the student
              found at 0.5; such a page goes ahead of every page that is only
              doubtful

Held-out and validation books are never picked (their labels only score, and
the validation set must stay the same between students), nor is a page any
teacher has already been asked about. Writes `data/vision/select/round2.json`
in the same format, with `hint_count` for `teacher.py --hint`.

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/select.py
    .venv-vision/bin/python tools/hotspot_extraction/vision/select.py --round 2 --per-book 38
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

PRED_DOUBT_DIR = PROJECT_ROOT / "data" / "vision" / "pred-doubt"
LABELS_DIR = PROJECT_ROOT / "data" / "vision" / "labels"
DATASET_JSON = PROJECT_ROOT / "data" / "vision" / "dataset" / "dataset.json"
ROUND2_OUT = PROJECT_ROOT / "data" / "vision" / "select" / "round2.json"
ROUND2_PER_BOOK = 38                 # 16 training books, about 600 pages
KEEP_CONF = 0.5                      # the confidence the bake keeps a box at
SURE_CONF = 0.9                      # at or above this the student is sure of a box


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


def box_doubt(score: float, keep: float = KEEP_CONF, sure: float = SURE_CONF) -> float:
    """0..1: 1 for a box right at the keep/drop line, falling to 0 at `sure` and towards 0 as the score nears 0."""
    if score >= sure:
        return 0.0
    if score >= keep:
        return (sure - score) / (sure - keep)
    return max(0.0, score / keep)


def page_doubt(scores: Sequence[float]) -> float:
    """A page is as doubtful as its most doubtful box; a page with no box at all is not doubtful."""
    return max((box_doubt(s) for s in scores), default=0.0)


def asked_pages(labels_dir: Path = LABELS_DIR) -> Dict[str, set]:
    """book id -> pages some teacher was already asked about (any route, any prompt version; not the student's own)."""
    out: Dict[str, set] = {}
    for route in sorted(p for p in labels_dir.iterdir() if p.is_dir() and p.name != "self") if labels_dir.is_dir() else []:
        for f in route.glob("*/*/*.json"):
            if f.stem.isdigit():
                out.setdefault(f.parent.name, set()).add(int(f.stem))
    return out


def rank_round2(index: Sequence[Dict[str, Any]], pred_pages: Dict[str, Any], manifest: Dict[int, int],
                asked: set) -> List[Dict[str, Any]]:
    """Every unlabelled single page of a book the student is unsure of, most worth asking first."""
    ranked: List[Dict[str, Any]] = []
    for rec in index:
        p = int(rec["page"])
        if p in asked or float(rec["width_pt"]) > SPREAD_WIDTH_PT or str(p) not in pred_pages:
            continue
        scores = [float(b["score"]) for b in pred_pages[str(p)].get("boxes", [])]
        kept = sum(1 for s in scores if s >= KEEP_CONF)
        n_manifest = manifest.get(p, 0)
        missing = max(0, n_manifest - kept)
        doubt = page_doubt(scores)
        if not missing and doubt <= 0.0:
            continue
        why = []
        if missing:
            why.append(f"{n_manifest} manifest entr{'y' if n_manifest == 1 else 'ies'}, {kept} box(es)")
        if doubt > 0.0:
            worst = min(scores, key=lambda s: abs(s - KEEP_CONF))
            why.append(f"box at {worst:.2f}")
        ranked.append({"page": p, "stratum": "manifest" if n_manifest else "doubt", "why": "; ".join(why),
                       "hint_count": n_manifest, "doubt": round(doubt, 3), "missing": missing})
    ranked.sort(key=lambda r: (-min(r["missing"], 1), -r["doubt"], r["page"]))
    return ranked


def build_round2(per_book: int = ROUND2_PER_BOOK, only: Optional[Sequence[str]] = None,
                 pred_dir: Path = PRED_DOUBT_DIR, pages_dir: Path = PAGES_DIR, bakes_dir: Path = BAKES_DIR,
                 labels_dir: Path = LABELS_DIR, dataset_json: Path = DATASET_JSON) -> Dict[str, Any]:
    with open(SPLITS) as fh:
        splits = json.load(fh)
    held = set(splits.get("heldOut") or [])
    titles = {k: v.get("title", k) for k, v in (splits.get("books") or {}).items()}
    with open(dataset_json, encoding="utf-8") as fh:
        valid = set(json.load(fh).get("valid_books", []))
    asked = asked_pages(labels_dir)
    books: Dict[str, Any] = {}
    checkpoint = None
    for book_id in rendered_books(pages_dir):
        if book_id in held or book_id in valid:
            continue
        if only and not any(w and w.lower() in book_id.lower() for w in only):
            continue
        pred_file = pred_dir / book_id / "boxes.json"
        if not pred_file.is_file():
            print(f"  {book_id[:8]} has no boxes under {pred_dir}; run infer.py --conf 0.2 --out {pred_dir} first", file=sys.stderr)
            continue
        with open(pred_file, encoding="utf-8") as fh:
            pred = json.load(fh)
        checkpoint = pred.get("checkpoint")
        index = read_index(book_id, pages_dir)
        ranked = rank_round2(index, pred["pages"], manifest_pages(book_id, load_bake(book_id, bakes_dir)),
                             asked.get(book_id, set()))
        pages = sorted(ranked[:per_book], key=lambda r: r["page"])
        books[book_id] = {
            "title": titles.get(book_id, book_id), "split": "train", "page_count": len(index),
            "candidates": len(ranked), "missing": sum(1 for r in pages if r["missing"]),
            "pages": pages,
        }
    return {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"), "round": 2, "per_book": per_book, "checkpoint": checkpoint,
        "from": str(pred_dir), "books": books, "total": sum(len(b["pages"]) for b in books.values()),
    }


def print_round2(sel: Dict[str, Any]) -> None:
    print(f"{'book':<10}{'title':<36}{'pages':>6}{'unsure':>8}{'picked':>8}{'missing':>9}{'doubt':>7}")
    for book_id, b in sorted(sel["books"].items()):
        mean = sum(r["doubt"] for r in b["pages"]) / len(b["pages"]) if b["pages"] else 0.0
        print(f"{book_id[:8]:<10}{b['title'][:34]:<36}{b['page_count']:>6}{b['candidates']:>8}{len(b['pages']):>8}"
              f"{b['missing']:>9}{mean:>7.2f}")
    print(f"total {sel['total']} pages over {len(sel['books'])} training books")


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
    ap.add_argument("--per-book", type=int, default=None, help=f"default {DEFAULT_PER_BOOK}, or {ROUND2_PER_BOOK} in round 2")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--only", default=None, help="comma-separated book id prefixes")
    ap.add_argument("--out", default=None, help="default: data/vision/select/round<N>.json")
    ap.add_argument("--round", type=int, choices=(1, 2), default=1, help="2 = the pages the student is least sure of")
    ap.add_argument("--pred", default=str(PRED_DOUBT_DIR), help="round 2: the student's boxes down to a low floor")
    args = ap.parse_args(argv)
    only = args.only.split(",") if args.only else None
    if args.round == 2:
        sel = build_round2(args.per_book or ROUND2_PER_BOOK, only, Path(args.pred))
        print_round2(sel)
    else:
        sel = build_selection(args.per_book or DEFAULT_PER_BOOK, args.seed, only)
        print_table(sel)
    out = Path(args.out) if args.out else (ROUND2_OUT if args.round == 2 else DEFAULT_OUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as fh:
        json.dump(sel, fh, indent=1, ensure_ascii=False)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
