#!/usr/bin/env python3
"""
Phase 3 of the vision plan: turn trusted teacher labels into a training set.

Reads one or more `verdicts.jsonl` files written by `qa.py`, keeps the pages
whose verdict is `trusted` (a page with no activity is kept: the student has
to learn "nothing here"), and writes

    data/vision/dataset/{train,valid,heldout}/<book-id>_<page:04d>.jpg   symlink to the rendered page
    data/vision/dataset/{train,valid,heldout}/_annotations.coco.json     COCO, one class: 0 = activity,
                                                                         boxes in pixels as [x, y, w, h]
    data/vision/dataset/dataset.json                                     what went in, per split and per book

The split is **by book**, never by page: pages of one book look alike and a
page split would lie. Held-out books are the ones the selection tags
`heldout` (train/splits.json); of the training books two, one ELT and one
subject, chosen by seed among the books that have labels, become `valid`,
and stay `valid` in later builds (`--reshuffle` chooses again). When a page appears in several
verdict files the first file listed wins.

`--check` validates a written dataset and exits non-zero naming the first
offending page: every image opens and has the recorded size, every box is
wider and taller than 8 px and lies inside its image, no image is in two
splits, no book is in two splits, every class id is 0.

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/dataset.py --verdicts data/vision/labels/verdicts-cli-g38.jsonl data/vision/labels/verdicts-api-v1.jsonl
    .venv-vision/bin/python tools/hotspot_extraction/vision/dataset.py --check
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PAGES_DIR = PROJECT_ROOT / "data" / "vision" / "pages"
DATASET_DIR = PROJECT_ROOT / "data" / "vision" / "dataset"
SELECTION = PROJECT_ROOT / "data" / "vision" / "select" / "round1.json"
SPLITS_JSON = PROJECT_ROOT / "tools" / "hotspot_extraction" / "train" / "splits.json"
ANNOTATIONS = "_annotations.coco.json"
SPLITS = ("train", "valid", "heldout")
CATEGORY = {"id": 0, "name": "activity", "supercategory": "none"}
MIN_BOX_PX = 8.0
DEFAULT_SEED = 0


# --------------------------------------------------------------------------- #
# Which book goes where                                                       #
# --------------------------------------------------------------------------- #

def assign_splits(selection: Dict[str, Any], templates: Dict[str, str], seed: int = DEFAULT_SEED,
                  labelled: Optional[Sequence[str]] = None) -> Dict[str, str]:
    """book id -> train / valid / heldout. Two training books (one per template) become valid.

    With `labelled`, the validation books are drawn only from books that have
    labels, and a template with a single labelled training book keeps it in train.
    """
    out: Dict[str, str] = {}
    by_template: Dict[str, List[str]] = {}
    for book_id, b in sorted(selection["books"].items()):
        if b.get("split") == "heldout":
            out[book_id] = "heldout"
        else:
            out[book_id] = "train"
            if labelled is None or book_id in labelled:
                by_template.setdefault(templates.get(book_id, "subject"), []).append(book_id)
    rng = random.Random(seed)
    for template in sorted(by_template):
        if len(by_template[template]) > 1:
            out[rng.choice(by_template[template])] = "valid"
    return out


def load_templates(path: Path = SPLITS_JSON) -> Dict[str, str]:
    with open(path, encoding="utf-8") as fh:
        return {book_id: b.get("template", "subject") for book_id, b in json.load(fh)["books"].items()}


# --------------------------------------------------------------------------- #
# Trusted pages -> COCO                                                       #
# --------------------------------------------------------------------------- #

def hinted(label_file: Path) -> bool:
    """Whether the teacher was given the `--hint` line ("make sure each has a box") for this label."""
    with open(label_file, encoding="utf-8") as fh:
        return bool(json.load(fh).get("hint"))


def trusted_labels(verdict_files: Sequence[Path]) -> List[Tuple[str, int, Path]]:
    """(book, page, label file) of every trusted page; the first verdict file to name a page wins.

    A label asked with the hint line is never trained on: the hint makes the teacher box whatever
    a publisher icon marks, against the prompt and against the decision that such an icon is a pin.
    """
    seen: Dict[Tuple[str, int], Path] = {}
    for vf in verdict_files:
        with open(vf, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                v = json.loads(line)
                key = (v["book"], int(v["page"]))
                if v["verdict"] != "trusted" or key in seen:
                    continue
                src = v.get("reask_file") if v.get("source") == "reask" else v.get("label_file")
                if src and not hinted(Path(src)):
                    seen[key] = Path(src)
    return [(b, p, f) for (b, p), f in sorted(seen.items())]


def coco_boxes(label: Dict[str, Any]) -> List[List[float]]:
    """The label's pixel boxes as COCO [x, y, w, h], clamped to the image."""
    w_px, h_px = float(label["index"]["width_px"]), float(label["index"]["height_px"])
    out = []
    for b in label.get("boxes") or []:
        x0, y0, x1, y1 = (float(v) for v in b["px"])
        x0, x1 = max(0.0, min(x0, x1)), min(w_px, max(x0, x1))
        y0, y1 = max(0.0, min(y0, y1)), min(h_px, max(y0, y1))
        out.append([round(x0, 1), round(y0, 1), round(x1 - x0, 1), round(y1 - y0, 1)])
    return out


def build(verdict_files: Sequence[Path], out_dir: Path = DATASET_DIR, pages_dir: Path = PAGES_DIR,
          selection_path: Path = SELECTION, templates: Optional[Dict[str, str]] = None,
          seed: int = DEFAULT_SEED, keep_valid: bool = True) -> Dict[str, Any]:
    with open(selection_path, encoding="utf-8") as fh:
        selection = json.load(fh)
    trusted = trusted_labels(verdict_files)
    split_of = assign_splits(selection, templates if templates is not None else load_templates(), seed,
                             labelled=sorted({b for b, _, _ in trusted}))
    # Once chosen, the validation books stay: a student trained on more labels
    # is only comparable with the last one when both are scored on the same books.
    previous = out_dir / "dataset.json"
    if keep_valid and previous.is_file():
        with open(previous, encoding="utf-8") as fh:
            kept = [b for b in json.load(fh).get("valid_books", []) if split_of.get(b) in ("train", "valid")]
        if kept:
            split_of = {b: ("valid" if b in kept else "train" if s == "valid" else s) for b, s in split_of.items()}

    cocos = {s: {"info": {"description": "Interaktiv activity boxes (teacher labels)"},
                 "categories": [CATEGORY], "images": [], "annotations": []} for s in SPLITS}
    books: Dict[str, Dict[str, Any]] = {}
    dropped: List[str] = []
    for split in SPLITS:
        if (out_dir / split).exists():
            shutil.rmtree(out_dir / split)       # symlinks and one JSON; rebuilt in seconds
        (out_dir / split).mkdir(parents=True)

    for book_id, page, label_path in trusted:
        split = split_of.get(book_id)
        image = pages_dir / book_id / f"{page:04d}.jpg"
        if split is None or not image.is_file():
            dropped.append(f"{book_id} p{page}: " + ("book not in the selection" if split is None else "no rendered page"))
            continue
        with open(label_path, encoding="utf-8") as fh:
            label = json.load(fh)
        boxes = coco_boxes(label)
        if any(b[2] <= MIN_BOX_PX or b[3] <= MIN_BOX_PX for b in boxes):
            dropped.append(f"{book_id} p{page}: a box of {MIN_BOX_PX:.0f} px or less")
            continue
        coco = cocos[split]
        name = f"{book_id}_{page:04d}.jpg"
        os.symlink(image, out_dir / split / name)
        image_id = len(coco["images"])
        coco["images"].append({"id": image_id, "file_name": name, "width": int(label["index"]["width_px"]),
                               "height": int(label["index"]["height_px"]), "book": book_id, "page": page})
        for b in boxes:
            coco["annotations"].append({"id": len(coco["annotations"]), "image_id": image_id,
                                        "category_id": CATEGORY["id"], "bbox": b, "area": round(b[2] * b[3], 1),
                                        "iscrowd": 0})
        row = books.setdefault(book_id, {"split": split, "pages": 0, "boxes": 0, "empty": 0})
        row["pages"] += 1
        row["boxes"] += len(boxes)
        row["empty"] += 0 if boxes else 1

    for split in SPLITS:
        with open(out_dir / split / ANNOTATIONS, "w", encoding="utf-8") as fh:
            json.dump(cocos[split], fh)
    summary = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "seed": seed,
               "verdicts": [str(v) for v in verdict_files], "splits": split_stats(cocos),
               "valid_books": sorted(b for b, s in split_of.items() if s == "valid"),
               "books": books, "dropped": dropped}
    with open(out_dir / "dataset.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False)
    return summary


def split_stats(cocos: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for split, coco in cocos.items():
        per_image: Dict[int, int] = {im["id"]: 0 for im in coco["images"]}
        for a in coco["annotations"]:
            per_image[a["image_id"]] += 1
        n = len(per_image)
        out[split] = {"images": n, "boxes": len(coco["annotations"]),
                      "books": len({im["book"] for im in coco["images"]}),
                      "boxes_per_page_mean": round(len(coco["annotations"]) / n, 2) if n else 0.0,
                      "boxes_per_page_max": max(per_image.values(), default=0),
                      "empty_share": round(sum(1 for c in per_image.values() if c == 0) / n, 3) if n else 0.0}
    return out


def print_stats(stats: Dict[str, Dict[str, Any]]) -> None:
    print(f"{'split':<9}{'books':>6}{'images':>8}{'boxes':>7}{'mean':>7}{'max':>5}{'empty':>7}")
    for split in SPLITS:
        s = stats[split]
        print(f"{split:<9}{s['books']:>6}{s['images']:>8}{s['boxes']:>7}{s['boxes_per_page_mean']:>7.2f}"
              f"{s['boxes_per_page_max']:>5}{100 * s['empty_share']:>6.0f}%")


# --------------------------------------------------------------------------- #
# Fail-fast validation                                                        #
# --------------------------------------------------------------------------- #

def check(out_dir: Path = DATASET_DIR) -> List[str]:
    """Every problem found, each naming its page. Empty means the dataset is fit to train on."""
    from PIL import Image

    problems: List[str] = []
    cocos: Dict[str, Dict[str, Any]] = {}
    where_image: Dict[str, str] = {}
    where_book: Dict[str, str] = {}
    for split in SPLITS:
        path = out_dir / split / ANNOTATIONS
        if not path.is_file():
            problems.append(f"{split}: no {ANNOTATIONS}")
            continue
        with open(path, encoding="utf-8") as fh:
            coco = cocos[split] = json.load(fh)
        images = {im["id"]: im for im in coco["images"]}
        for im in coco["images"]:
            name = im["file_name"]
            if where_image.setdefault(name, split) != split:
                problems.append(f"{name}: in both {where_image[name]} and {split}")
            if where_book.setdefault(im["book"], split) != split:
                problems.append(f"{name}: book {im['book'][:8]} is in both {where_book[im['book']]} and {split}")
            try:
                with Image.open(out_dir / split / name) as pic:
                    if pic.size != (im["width"], im["height"]):
                        problems.append(f"{name}: image is {pic.size}, annotations say {(im['width'], im['height'])}")
            except OSError as exc:
                problems.append(f"{name}: cannot open ({exc})")
        for a in coco["annotations"]:
            im = images.get(a["image_id"])
            if im is None:
                problems.append(f"{split}: annotation {a['id']} names no image")
                continue
            x, y, w, h = a["bbox"]
            if a["category_id"] != CATEGORY["id"]:
                problems.append(f"{im['file_name']}: class id {a['category_id']}")
            if w <= MIN_BOX_PX or h <= MIN_BOX_PX:
                problems.append(f"{im['file_name']}: box {a['bbox']} is {MIN_BOX_PX:.0f} px or less")
            if x < 0 or y < 0 or x + w > im["width"] + 0.5 or y + h > im["height"] + 0.5:
                problems.append(f"{im['file_name']}: box {a['bbox']} leaves the image")
    if len(cocos) == len(SPLITS):
        if not cocos["train"]["images"]:
            problems.append("train: no images")
        print_stats(split_stats(cocos))
    return problems


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verdicts", nargs="+", default=None, help="verdicts.jsonl files from qa.py; the first to name a page wins")
    ap.add_argument("--out", default=str(DATASET_DIR))
    ap.add_argument("--selection", default=str(SELECTION))
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="picks the two validation books")
    ap.add_argument("--reshuffle", action="store_true", help="choose the validation books again instead of keeping the last ones")
    ap.add_argument("--check", action="store_true", help="validate the dataset under --out and exit")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    if not args.check:
        if not args.verdicts:
            ap.error("--verdicts is required to build a dataset")
        summary = build([Path(v) for v in args.verdicts], out_dir, selection_path=Path(args.selection), seed=args.seed,
                        keep_valid=not args.reshuffle)
        for d in summary["dropped"]:
            print(f"dropped {d}")
        print(f"wrote {out_dir} (valid books: {', '.join(b[:8] for b in summary['valid_books'])})")
    problems = check(out_dir)
    for p in problems[:40]:
        print(f"CHECK FAILED {p}", file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
