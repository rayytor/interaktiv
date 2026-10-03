#!/usr/bin/env python3
"""
Phase 4 of the vision plan: train the student detector.

Fine-tunes RF-DETR (Apache-2.0, COCO-pretrained) on the dataset written by
`dataset.py`: one class, `activity`. Validation is the `valid` split;
the held-out books are never opened here (`evaluate.py --split heldout`
does that, once per phase).

  * `last.ckpt` is rewritten after every epoch under
    `data/vision/checkpoints/<run>/`, an archive is kept every tenth epoch,
    and the best epoch by validation mAP is `checkpoint_best_total.pth`;
  * `--resume` continues an interrupted run from its `last.ckpt` with the
    same command line;
  * `--smoke` trains one epoch on 64 pages and validates on 16, to surface
    anything that would crash a full run (a bad label, an out-of-memory at
    this batch size, a broken import) in a few minutes. Run it before every
    full run;
  * pages are never flipped or cropped: mirrored text is not a page, and a
    cropped page teaches half-activities.

One line per run is appended to `vision/runs/train-runs.jsonl` (settings,
wall clock, peak VRAM, best validation scores).

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/train.py --smoke
    .venv-vision/bin/python tools/hotspot_extraction/vision/train.py --run student-1
    .venv-vision/bin/python tools/hotspot_extraction/vision/train.py --run student-1 --resume
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.vision.dataset import ANNOTATIONS, DATASET_DIR  # noqa: E402

CHECKPOINTS_DIR = PROJECT_ROOT / "data" / "vision" / "checkpoints"
SMOKE_DIR = PROJECT_ROOT / "data" / "vision" / "dataset-smoke"
RUNS_LOG = Path(__file__).resolve().parent / "runs" / "train-runs.jsonl"
BEST_NAME = "checkpoint_best_total.pth"
SIZES = ("nano", "small", "medium", "large")
SMOKE_TRAIN, SMOKE_VALID = 64, 16


def model_class(size: str):
    import rfdetr
    return {"nano": rfdetr.RFDETRNano, "small": rfdetr.RFDETRSmall,
            "medium": rfdetr.RFDETRMedium, "large": rfdetr.RFDETRLarge}[size]


def write_subset(src: Path, dst: Path, split: str, n: int) -> int:
    """The first `n` images of a split (boxes included), as their own split folder under `dst`."""
    with open(src / split / ANNOTATIONS, encoding="utf-8") as fh:
        coco = json.load(fh)
    # Evenly spaced through the split, so every book and both empty and busy pages are in it.
    step = max(1, len(coco["images"]) // n)
    images = coco["images"][::step][:n]
    keep = {im["id"] for im in images}
    out = dst / split
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for im in images:
        os.symlink((src / split / im["file_name"]).resolve(), out / im["file_name"])
    coco["images"] = images
    coco["annotations"] = [a for a in coco["annotations"] if a["image_id"] in keep]
    with open(out / ANNOTATIONS, "w", encoding="utf-8") as fh:
        json.dump(coco, fh)
    return len(images)


def best_epoch(out_dir: Path) -> Dict[str, Any]:
    """The best validation epoch from RF-DETR's `metrics.csv` (EMA weights when it logs them)."""
    import csv
    path = out_dir / "metrics.csv"
    if not path.is_file():
        return {}
    best: Dict[str, Any] = {}
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for prefix in ("val/ema_", "val/"):
                score = row.get(prefix + "mAP_50_95")
                if score not in (None, ""):
                    if float(score) > best.get("val_mAP50_95", -1.0):
                        best = {"epoch": int(float(row.get("epoch") or 0)), "val_mAP50_95": round(float(score), 4),
                                "val_mAP50": round(float(row.get(prefix + "mAP_50") or 0), 4)}
                    break
    return best


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(DATASET_DIR))
    ap.add_argument("--run", default="student-1", help="checkpoint folder name")
    ap.add_argument("--size", choices=SIZES, default="medium")
    ap.add_argument("--resolution", type=int, default=None, help="square training size in px (default: the model's own)")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--accum", type=int, default=4, help="gradient accumulation steps (effective batch = batch x accum)")
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="continue this run from its last.ckpt")
    ap.add_argument("--init", default=None,
                    help="start from another run's best checkpoint (a run name or a file) instead of the "
                         "published weights, e.g. to carry a student on at a higher --resolution")
    ap.add_argument("--smoke", action="store_true", help=f"{SMOKE_TRAIN} train / {SMOKE_VALID} valid pages, 1 epoch, batch 2")
    args = ap.parse_args(argv)

    dataset_dir = Path(args.dataset)
    if args.smoke:
        n_train = write_subset(dataset_dir, SMOKE_DIR, "train", SMOKE_TRAIN)
        n_valid = write_subset(dataset_dir, SMOKE_DIR, "valid", SMOKE_VALID)
        print(f"smoke: {n_train} train / {n_valid} valid pages in {SMOKE_DIR}")
        dataset_dir, args.run, args.epochs, args.batch, args.accum = SMOKE_DIR, "smoke", 1, 2, 1
    out_dir = CHECKPOINTS_DIR / args.run
    if args.smoke and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    resume = None
    if args.resume:
        last = out_dir / "last.ckpt"
        if not last.is_file():
            print(f"nothing to resume: {last} does not exist", file=sys.stderr)
            return 2
        resume = str(last)

    import torch
    if not torch.cuda.is_available():
        print("no GPU visible to PyTorch; not training on the CPU", file=sys.stderr)
        return 2
    torch.cuda.reset_peak_memory_stats()

    model_kwargs: Dict[str, Any] = {}
    if args.resolution:
        model_kwargs["resolution"] = args.resolution
    if args.init:
        init = Path(args.init) if Path(args.init).is_file() else CHECKPOINTS_DIR / args.init / BEST_NAME
        if not init.is_file():
            print(f"no checkpoint to start from at {init}", file=sys.stderr)
            return 2
        model_kwargs["pretrain_weights"] = str(init)
    model = model_class(args.size)(**model_kwargs)
    t0 = time.time()
    model.train(
        dataset_dir=str(dataset_dir), output_dir=str(out_dir),
        epochs=args.epochs, batch_size=args.batch, grad_accum_steps=args.accum, lr=args.lr,
        num_workers=args.workers, seed=args.seed, resume=resume,
        checkpoint_interval=10, run_test=False,     # last.ckpt every epoch, an archive every tenth
        aug_config={}, scale_jitter=False,          # no flip, no crop
        tensorboard=False, wandb=False,
    )
    wall = time.time() - t0
    peak = torch.cuda.max_memory_allocated() / 2 ** 30

    row = {"run": args.run, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "size": args.size,
           "resolution": args.resolution, "epochs": args.epochs, "batch": args.batch, "accum": args.accum,
           "lr": args.lr, "seed": args.seed, "resumed": bool(resume), "init": args.init, "dataset": str(dataset_dir),
           "seconds": round(wall), "peak_vram_gb": round(peak, 2),
           "best": str(out_dir / BEST_NAME) if (out_dir / BEST_NAME).is_file() else None}
    row.update(best_epoch(out_dir))
    print(f"{args.run}: {wall:.0f} s, peak VRAM {peak:.1f} GB, best checkpoint {row['best']}")
    if not args.smoke:
        RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(RUNS_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    return 0 if row["best"] else 1


if __name__ == "__main__":
    sys.exit(main())
