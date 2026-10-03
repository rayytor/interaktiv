#!/usr/bin/env python3
"""
Phase 5 of the vision plan: the student's boxes for whole books.

Loads one checkpoint once and walks the rendered pages of each book
(`data/vision/pages/<book-id>/`, from `render.py`), one book at a time, and
writes

    data/vision/pred/<book-id>/boxes.json
        {"checkpoint": "<sha256>", "conf": 0.5,
         "pages": {"31": {"width_px": 1024, "height_px": 1444,
                          "boxes": [{"px": [x0, y0, x1, y1], "score": 0.93}]}}}

Only boxes at or above `--conf` are kept (a missing box is better than a
wrong one), and of two boxes that overlap by IoU 0.5 or more the more
confident one stays. A book whose file already names this checkpoint and
confidence is skipped; `--force` redoes it.

`scan.py --engine vision` reads these files; it never loads a model.

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/infer.py --run student-1 --only 0a3fbb41
    .venv-vision/bin/python tools/hotspot_extraction/vision/infer.py --run student-1 --all
    .venv-vision/bin/python tools/hotspot_extraction/vision/infer.py --run student-4 --all --conf 0.2 --out data/vision/pred-doubt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.vision.qa import iou  # noqa: E402
from tools.hotspot_extraction.vision.train import BEST_NAME, CHECKPOINTS_DIR, SIZES, model_class  # noqa: E402

PAGES_DIR = PROJECT_ROOT / "data" / "vision" / "pages"
PRED_DIR = PROJECT_ROOT / "data" / "vision" / "pred"
BOXES_NAME = "boxes.json"
DEFAULT_CONF = 0.5
NMS_IOU = 0.5


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def suppress(boxes: Sequence[Tuple[float, Sequence[float]]], thresh: float = NMS_IOU) -> List[Tuple[float, Sequence[float]]]:
    """Of boxes overlapping by `thresh` IoU or more, keep the most confident."""
    kept: List[Tuple[float, Sequence[float]]] = []
    for score, box in sorted(boxes, key=lambda b: -b[0]):
        if all(iou(box, k) < thresh for _, k in kept):
            kept.append((score, box))
    return kept


def book_pages(book_dir: Path) -> List[Tuple[int, Path]]:
    return sorted((int(f.stem), f) for f in book_dir.glob("*.jpg") if f.stem.isdigit())


def infer_book(model: Any, book_id: str, sha: str, conf: float, pages_dir: Path = PAGES_DIR,
               pred_dir: Path = PRED_DIR, force: bool = False) -> Dict[str, Any]:
    from PIL import Image

    out = pred_dir / book_id / BOXES_NAME
    if out.is_file() and not force:
        with open(out, encoding="utf-8") as fh:
            have = json.load(fh)
        if have.get("checkpoint") == sha and have.get("conf") == conf:
            return {"book": book_id, "status": "skipped", "pages": len(have["pages"])}
    t0 = time.time()
    pages: Dict[str, Any] = {}
    n_boxes = 0
    for page, jpeg in book_pages(pages_dir / book_id):
        with Image.open(jpeg) as pic:
            w, h = pic.size
            det = model.predict(pic.convert("RGB"), threshold=conf)
        kept = suppress([(float(s), [float(v) for v in box]) for s, box in zip(det.confidence, det.xyxy)])
        pages[str(page)] = {"width_px": w, "height_px": h,
                            "boxes": [{"px": [round(v, 1) for v in box], "score": round(score, 4)} for score, box in kept]}
        n_boxes += len(kept)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"book": book_id, "checkpoint": sha, "conf": conf, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "pages": pages}, fh)
    os.replace(tmp, out)
    return {"book": book_id, "status": "done", "pages": len(pages), "boxes": n_boxes, "seconds": round(time.time() - t0, 1)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="student-1")
    ap.add_argument("--checkpoint", default=None, help="a checkpoint file (default: the run's best)")
    ap.add_argument("--size", choices=SIZES, default="medium")
    ap.add_argument("--resolution", type=int, default=None)
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF)
    ap.add_argument("--only", default=None, help="comma-separated book id prefixes")
    ap.add_argument("--all", action="store_true", help="every rendered book")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out", default=str(PRED_DIR),
                    help="prediction folder; give another one for a low --conf, so the bake's boxes stay as they are")
    args = ap.parse_args(argv)

    checkpoint = Path(args.checkpoint) if args.checkpoint else CHECKPOINTS_DIR / args.run / BEST_NAME
    if not checkpoint.is_file():
        print(f"no checkpoint at {checkpoint}", file=sys.stderr)
        return 2
    books = sorted(p.name for p in PAGES_DIR.iterdir() if p.is_dir())
    if args.only:
        prefixes = args.only.split(",")
        books = [b for b in books if any(b.startswith(p) for p in prefixes)]
    elif not args.all:
        ap.error("give --only <book id prefix> or --all")
    if not books:
        print("no rendered book matches", file=sys.stderr)
        return 2

    sha = file_sha256(checkpoint)
    kwargs: Dict[str, Any] = {"pretrain_weights": str(checkpoint)}
    if args.resolution:
        kwargs["resolution"] = args.resolution
    model = model_class(args.size)(**kwargs)
    print(f"checkpoint {checkpoint.name} ({sha[:12]}), confidence {args.conf:g}, {len(books)} book(s)")
    for book_id in books:
        res = infer_book(model, book_id, sha, args.conf, pred_dir=Path(args.out), force=args.force)
        extra = f"{res['boxes']} boxes, {res['seconds']} s" if res["status"] == "done" else "current"
        print(f"  {book_id[:8]} {res['pages']:4d} pages | {extra}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
