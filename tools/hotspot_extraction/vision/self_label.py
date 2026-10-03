#!/usr/bin/env python3
"""
Phase 6 of the vision plan: the student labels pages for itself.

A checkpoint predicts the pages of the training books that no teacher has
labelled, and a page becomes a label only when the student is sure of all of
it:

  * every box it sees on the page at all (score >= `--floor`, default 0.2)
    scores at least `--conf` (default 0.9), so a page with one doubtful box is
    left out whole rather than taught with a hole in it;
  * a page with no box above the floor is an empty page, and at most
    `--empty-share` of a book's picks are empty pages.

The picks are written as label files in the teacher's format under
`data/vision/labels/self/<run>/<book-id>/<page:04d>.json`, so `qa.py` judges
them with the same gate (no overlap, no sliver, no missed publisher icon)
and `dataset.py` takes the trusted ones beside the teacher's.

Held-out and validation books are never labelled this way: their pages must
stay unseen by training. Two-page spreads are skipped, as in `select.py`.

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/self_label.py --run student-2
    .venv-vision/bin/python tools/hotspot_extraction/vision/qa.py --labels data/vision/labels/self/student-2 --out data/vision/labels/verdicts-self-student-2.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.vision.dataset import DATASET_DIR, SELECTION  # noqa: E402
from tools.hotspot_extraction.vision.infer import PAGES_DIR, file_sha256, suppress  # noqa: E402
from tools.hotspot_extraction.vision.qa import LABELS_DIR, label_files  # noqa: E402
from tools.hotspot_extraction.vision.select import read_index  # noqa: E402
from tools.hotspot_extraction.vision.snap import px_to_points  # noqa: E402
from tools.hotspot_extraction.vision.train import BEST_NAME, CHECKPOINTS_DIR, SIZES, model_class  # noqa: E402

SPREAD_WIDTH_PT = 700.0
DEFAULT_CONF = 0.9
DEFAULT_FLOOR = 0.2
DEFAULT_PER_BOOK = 60
DEFAULT_EMPTY_SHARE = 0.3


def sure_boxes(scored: Sequence[Tuple[float, Sequence[float]]], conf: float) -> Optional[List[Tuple[float, Sequence[float]]]]:
    """The page's boxes when the student is sure of every one of them, else None. An empty list is an empty page."""
    kept = suppress(scored)
    if any(score < conf for score, _ in kept):
        return None
    return kept


def pick_pages(candidates: Sequence[Tuple[int, int]], per_book: int, empty_share: float, seed: int) -> List[int]:
    """From (page, box count) pairs: up to `per_book` pages with boxes, plus empty pages up to `empty_share` of the picks, seeded."""
    rng = random.Random(seed)
    full = sorted(p for p, n in candidates if n > 0)
    empty = sorted(p for p, n in candidates if n == 0)
    rng.shuffle(full)
    rng.shuffle(empty)
    full = full[:per_book]
    room = int(len(full) * empty_share / (1.0 - empty_share)) if empty_share < 1.0 else len(empty)
    return sorted(full + empty[:room])


def labelled_pages(label_dirs: Sequence[Path]) -> Set[Tuple[str, int]]:
    out: Set[Tuple[str, int]] = set()
    for d in label_dirs:
        if d.is_dir():
            out.update((b, p) for b, p, _ in label_files(d))
    return out


def training_books(dataset_dir: Path = DATASET_DIR, selection_path: Path = SELECTION) -> List[str]:
    """Every book that is neither held out nor a validation book of the current dataset."""
    with open(selection_path, encoding="utf-8") as fh:
        books = json.load(fh)["books"]
    with open(dataset_dir / "dataset.json", encoding="utf-8") as fh:
        valid = set(json.load(fh).get("valid_books", []))
    return sorted(b for b, row in books.items() if row.get("split") != "heldout" and b not in valid)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="student-1", help="the checkpoint that labels")
    ap.add_argument("--checkpoint", default=None)
    ap.add_argument("--size", choices=SIZES, default="medium")
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF, help="every box on a picked page scores at least this")
    ap.add_argument("--floor", type=float, default=DEFAULT_FLOOR, help="a box scoring less than this was not seen")
    ap.add_argument("--per-book", type=int, default=DEFAULT_PER_BOOK)
    ap.add_argument("--empty-share", type=float, default=DEFAULT_EMPTY_SHARE)
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--teacher-labels", nargs="*", default=[str(LABELS_DIR / "agy" / "v1-g38"), str(LABELS_DIR / "api" / "v1")],
                    help="label folders whose pages are already labelled and are skipped")
    ap.add_argument("--out", default=None, help="default: data/vision/labels/self/<run>")
    args = ap.parse_args(argv)

    from PIL import Image

    checkpoint = Path(args.checkpoint) if args.checkpoint else CHECKPOINTS_DIR / args.run / BEST_NAME
    if not checkpoint.is_file():
        print(f"no checkpoint at {checkpoint}", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else LABELS_DIR / "self" / args.run
    have = labelled_pages([Path(d) for d in args.teacher_labels])
    books = training_books()
    sha = file_sha256(checkpoint)
    model = model_class(args.size)(pretrain_weights=str(checkpoint))
    print(f"checkpoint {checkpoint.name} ({sha[:12]}), every box >= {args.conf:g}, {len(books)} training book(s) -> {out_dir}")

    total = 0
    for book_id in books:
        t0 = time.time()
        index = {int(r["page"]): r for r in read_index(book_id, PAGES_DIR)}
        sure: Dict[int, List[Tuple[float, Sequence[float]]]] = {}
        seen = 0
        for page, row in sorted(index.items()):
            if (book_id, page) in have or float(row["width_pt"]) > SPREAD_WIDTH_PT:
                continue
            seen += 1
            with Image.open(PAGES_DIR / book_id / f"{page:04d}.jpg") as pic:
                det = model.predict(pic.convert("RGB"), threshold=args.floor)
            boxes = sure_boxes([(float(s), [float(v) for v in box]) for s, box in zip(det.confidence, det.xyxy)], args.conf)
            if boxes is not None:
                sure[page] = boxes
        picked = pick_pages([(p, len(b)) for p, b in sure.items()], args.per_book, args.empty_share, args.seed)
        (out_dir / book_id).mkdir(parents=True, exist_ok=True)
        for old in (out_dir / book_id).glob("*.json"):
            old.unlink()                    # this run's picks replace an earlier run's for the same checkpoint name
        for page in picked:
            row = index[page]
            label = {
                "book": book_id, "page": page, "route": "self", "model": f"{args.run}:{sha[:12]}",
                "created": time.strftime("%Y-%m-%d %H:%M:%S"), "index": row,
                "boxes": [{"label": None, "score": round(score, 4), "px": [round(v, 1) for v in px],
                           "rect": list(px_to_points(px, row["width_px"], row["height_px"], row["width_pt"], row["height_pt"]))}
                          for score, px in sure[page]],
            }
            with open(out_dir / book_id / f"{page:04d}.json", "w", encoding="utf-8") as fh:
                json.dump(label, fh)
        total += len(picked)
        print(f"  {book_id[:8]} {seen:4d} unlabelled pages | {len(sure):4d} sure | {len(picked):3d} picked "
              f"({sum(1 for p in picked if not sure[p])} empty) | {time.time() - t0:.0f} s", flush=True)
    print(f"{total} page(s) labelled by the student under {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
