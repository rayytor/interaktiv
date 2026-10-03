#!/usr/bin/env python3
"""
Phase 4 of the vision plan: score a student checkpoint against the teacher's boxes.

For one split of the dataset written by `dataset.py` the checkpoint predicts
every page, and the predictions are compared with the teacher's boxes:

  mAP50, mAP50-95   average precision over all confidences, at IoU 0.5 and
                    averaged over IoU 0.50 .. 0.95
  precision, recall at `--conf` (default 0.5) and IoU 0.5: of the boxes the
                    student draws, how many are an activity; of the
                    activities, how many it finds
  boxes             predicted against teacher boxes per book, so a student
                    that systematically draws too many or too few shows up

per book and in total. Correctness comes first here: precision is the
number to read before recall.

`--split heldout` is the honest score and is to be run once per phase, on
the checkpoint already chosen on `valid`.

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/evaluate.py --run student-1 --split valid
    .venv-vision/bin/python tools/hotspot_extraction/vision/evaluate.py --run student-1 --split heldout
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.vision.dataset import ANNOTATIONS, DATASET_DIR  # noqa: E402
from tools.hotspot_extraction.vision.qa import iou  # noqa: E402
from tools.hotspot_extraction.vision.train import BEST_NAME, CHECKPOINTS_DIR, SIZES, model_class  # noqa: E402

RUNS_DIR = Path(__file__).resolve().parent / "runs"
IOU_STEPS = [round(0.5 + 0.05 * i, 2) for i in range(10)]
MIN_SCORE = 0.05            # predictions below this are noise and are not scored at all

Box = Sequence[float]       # x0, y0, x1, y1 in pixels
Page = Dict[str, Any]       # {"book", "gt": [Box], "pred": [(score, Box)]}


# --------------------------------------------------------------------------- #
# Scoring (no torch needed)                                                   #
# --------------------------------------------------------------------------- #

def match_page(gt: Sequence[Box], pred: Sequence[Tuple[float, Box]], thresh: float) -> List[Tuple[float, bool]]:
    """(score, is a true positive) per prediction, best score first; a teacher box is matched once."""
    used = set()
    out = []
    for score, box in sorted(pred, key=lambda p: -p[0]):
        best, best_j = 0.0, None
        for j, g in enumerate(gt):
            if j in used:
                continue
            v = iou(box, g)
            if v > best:
                best, best_j = v, j
        hit = best_j is not None and best >= thresh
        if hit:
            used.add(best_j)
        out.append((score, hit))
    return out


def average_precision(pages: Sequence[Page], thresh: float) -> float:
    """COCO-style AP (101-point interpolation) for the single class at one IoU threshold."""
    n_gt = sum(len(p["gt"]) for p in pages)
    if n_gt == 0:
        return float("nan")
    hits = sorted((m for p in pages for m in match_page(p["gt"], p["pred"], thresh)), key=lambda m: -m[0])
    precisions, recalls = [], []
    tp = 0
    for i, (_, hit) in enumerate(hits, 1):
        tp += 1 if hit else 0
        precisions.append(tp / i)
        recalls.append(tp / n_gt)
    for i in range(len(precisions) - 2, -1, -1):        # precision envelope
        precisions[i] = max(precisions[i], precisions[i + 1])
    total, k = 0.0, 0
    for step in range(101):
        r = step / 100.0
        while k < len(recalls) and recalls[k] < r:
            k += 1
        total += precisions[k] if k < len(recalls) else 0.0
    return total / 101.0


def score_pages(pages: Sequence[Page], conf: float) -> Dict[str, Any]:
    kept = [{"gt": p["gt"], "pred": [x for x in p["pred"] if x[0] >= conf]} for p in pages]
    n_gt = sum(len(p["gt"]) for p in kept)
    n_pred = sum(len(p["pred"]) for p in kept)
    tp = sum(1 for p in kept for _, hit in match_page(p["gt"], p["pred"], 0.5) if hit)
    aps = [average_precision(pages, t) for t in IOU_STEPS]
    empty = [p for p in kept if not p["gt"]]
    return {
        "pages": len(pages), "teacher_boxes": n_gt, "predicted_boxes": n_pred,
        "mAP50": round(aps[0], 4) if n_gt else None,
        "mAP50_95": round(sum(aps) / len(aps), 4) if n_gt else None,
        "precision": round(tp / n_pred, 4) if n_pred else None,
        "recall": round(tp / n_gt, 4) if n_gt else None,
        "empty_pages": len(empty),
        "empty_pages_with_a_box": sum(1 for p in empty if p["pred"]),
    }


def summarize(pages: Sequence[Page], conf: float) -> Dict[str, Any]:
    by_book: Dict[str, List[Page]] = defaultdict(list)
    for p in pages:
        by_book[p["book"]].append(p)
    return {"total": score_pages(pages, conf),
            "books": {b: score_pages(ps, conf) for b, ps in sorted(by_book.items())}}


def print_table(summary: Dict[str, Any]) -> None:
    def cell(v: Any) -> str:
        return f"{v:>8.3f}" if isinstance(v, float) else f"{'-' if v is None else v:>8}"
    cols = ("pages", "teacher_boxes", "predicted_boxes", "mAP50", "mAP50_95", "precision", "recall",
            "empty_pages", "empty_pages_with_a_box")
    print(f"{'book':<10}" + "".join(f"{c[:8]:>8}" for c in ("pages", "teacher", "student", "mAP50", "mAP50-95",
                                                           "precis.", "recall", "empty", "emp+box")))
    for book, s in list(summary["books"].items()) + [("total", summary["total"])]:
        print(f"{book[:8]:<10}" + "".join(cell(s[c]) for c in cols))


# --------------------------------------------------------------------------- #
# Predicting                                                                  #
# --------------------------------------------------------------------------- #

def load_split(dataset_dir: Path, split: str) -> List[Dict[str, Any]]:
    with open(dataset_dir / split / ANNOTATIONS, encoding="utf-8") as fh:
        coco = json.load(fh)
    gt: Dict[int, List[Box]] = defaultdict(list)
    for a in coco["annotations"]:
        x, y, w, h = a["bbox"]
        gt[a["image_id"]].append((x, y, x + w, y + h))
    return [{"book": im["book"], "page": im["page"], "path": dataset_dir / split / im["file_name"],
             "gt": gt.get(im["id"], [])} for im in coco["images"]]


def predict(model: Any, pages: List[Dict[str, Any]], min_score: float = MIN_SCORE) -> None:
    from PIL import Image
    for p in pages:
        with Image.open(p["path"]) as pic:
            det = model.predict(pic.convert("RGB"), threshold=min_score)
        p["pred"] = [(float(s), tuple(float(v) for v in box)) for s, box in zip(det.confidence, det.xyxy)]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="student-1", help="checkpoint folder under data/vision/checkpoints")
    ap.add_argument("--checkpoint", default=None, help="a checkpoint file (default: the run's best)")
    ap.add_argument("--split", choices=("valid", "heldout", "train"), default="valid")
    ap.add_argument("--dataset", default=str(DATASET_DIR))
    ap.add_argument("--size", choices=SIZES, default="medium")
    ap.add_argument("--resolution", type=int, default=None)
    ap.add_argument("--conf", type=float, default=0.5, help="confidence at which precision and recall are read")
    ap.add_argument("--out", default=None, help="summary JSON (default: vision/runs/eval-<run>-<split>.json)")
    args = ap.parse_args(argv)

    checkpoint = Path(args.checkpoint) if args.checkpoint else CHECKPOINTS_DIR / args.run / BEST_NAME
    if not checkpoint.is_file():
        print(f"no checkpoint at {checkpoint}", file=sys.stderr)
        return 2
    pages = load_split(Path(args.dataset), args.split)
    kwargs: Dict[str, Any] = {"pretrain_weights": str(checkpoint)}
    if args.resolution:
        kwargs["resolution"] = args.resolution
    model = model_class(args.size)(**kwargs)
    t0 = time.time()
    predict(model, pages)
    summary = summarize(pages, args.conf)
    summary.update({"run": args.run, "checkpoint": str(checkpoint), "split": args.split, "conf": args.conf,
                    "seconds": round(time.time() - t0), "created": time.strftime("%Y-%m-%d %H:%M:%S")})
    print(f"{args.split}: {len(pages)} page(s) in {summary['seconds']} s, precision and recall at confidence {args.conf:g}")
    print_table(summary)
    out = Path(args.out) if args.out else RUNS_DIR / f"eval-{args.run}-{args.split}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
