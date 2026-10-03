#!/usr/bin/env python3
"""
How well a vision bake's regions agree with the teacher's boxes.

The scorecard (`compare.py`) counts violations and joins publisher icons; it
does not say whether a region is the activity. `evaluate.py` scores the
student's raw boxes, before snapping. This scores what is actually baked: for
every teacher box on the labelled pages of a split, the baked region that fits
it best, measured two ways:

    IoU       the two rectangles' overlap. Harsh on a one-line question, where
              two points of padding are a fifth of the box.
    content   which lines of type and drawn blocks each rectangle holds (by
              their centres), as a Jaccard share. 1.0 means the region holds
              exactly what the teacher's box holds, whatever the padding.

The student's raw boxes are scored beside the regions, so the difference is
what snapping and baking did to them. The other direction is counted too: the
regions on these pages that answer no teacher box (best IoU under 0.5), and the
pages the teacher left empty that carry a region -- hotspots on nothing. Point
`--bake-root` at the project root to score the rules bake the same way. Teacher boxes taller than
`TALL_REGION` are counted apart, as they were while no region was allowed to be that tall (until 2026-10-03).

One short-lived process per book. Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/accuracy.py
    .venv-vision/bin/python tools/hotspot_extraction/vision/accuracy.py --split train --only 0e966773,d13a75d3 --bake-root data/vision/bake
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import sys
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.vision.qa import iou  # noqa: E402

DATASET_DIR = PROJECT_ROOT / "data" / "vision" / "dataset"
BAKE_ROOT = PROJECT_ROOT / "data" / "vision" / "bake"
PRED_DIR = PROJECT_ROOT / "data" / "vision" / "pred"
IOU_LEVELS = (0.5, 0.85)
SAME_CONTENT = 0.999
EXTRA_IOU = 0.5            # a region overlapping no teacher box this much answers none of them


def jaccard(a: FrozenSet[int], b: FrozenSet[int]) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


def best(target: Sequence[float], held: FrozenSet[int], boxes: Sequence[Tuple[Sequence[float], FrozenSet[int]]]) -> Tuple[float, float]:
    """(best IoU, best content share) of `boxes` against one teacher box."""
    return (max((iou(target, b) for b, _ in boxes), default=0.0),
            max((jaccard(held, h) for _, h in boxes), default=0.0))


def _book(job: Tuple[str, str, str, str]) -> Dict[str, Any]:
    import pymupdf
    from tools.hotspot_extraction.scanner.anchors import _page_geometry
    from tools.hotspot_extraction.scanner.layout import detect_layout
    from tools.hotspot_extraction.scanner.primitives import extract_page_primitives
    from tools.hotspot_extraction.scanner.prompts import detect_activity_markers
    from tools.hotspot_extraction.scanner.regions import TALL_REGION, GrowthTrace

    book_id, split, bake_root, pred_dir = job
    with open(DATASET_DIR / split / "_annotations.coco.json", encoding="utf-8") as fh:
        coco = json.load(fh)
    images = {im["id"]: im for im in coco["images"] if im["book"] == book_id}
    teacher: Dict[int, List[List[float]]] = {}
    for a in coco["annotations"]:
        if a["image_id"] in images:
            x, y, w, h = a["bbox"]
            teacher.setdefault(images[a["image_id"]]["page"], []).append([x, y, x + w, y + h])
    try:
        with open(Path(bake_root) / "activities" / "books" / book_id / "regions.json", encoding="utf-8") as fh:
            bake = json.load(fh)
        with open(Path(pred_dir) / book_id / "boxes.json", encoding="utf-8") as fh:
            pred = json.load(fh)
    except FileNotFoundError as exc:
        return {"book": book_id, "error": str(exc)}
    conf = bake.get("conf") if bake.get("conf") is not None else float(pred.get("conf") or 0.0)

    doc = pymupdf.open(PROJECT_ROOT / "books" / f"{book_id}.pdf")
    rows: List[Dict[str, Any]] = []
    pages: List[Dict[str, Any]] = []
    for im in images.values():
        page = im["page"]
        boxes = teacher.get(page) or []
        baked = (bake.get("pages") or {}).get(str(page)) or {}
        # The teacher boxed exercises; a content region (scanner/content.py) answers none by design.
        baked = {**baked, "activities": [a for a in baked.get("activities") or [] if a.get("kind", "activity") == "activity"]}
        if not boxes:
            # A page the teacher found no activity on: every region here is a hotspot on nothing.
            pages.append({"page": page, "teacher": 0, "regions": len(baked.get("activities") or []),
                          "extra": len(baked.get("activities") or [])})
            continue
        prim = extract_page_primitives(doc, page)
        layout = detect_layout(prim)
        geom = _page_geometry(prim, layout, detect_activity_markers(prim, layout=layout, trace=GrowthTrace()))
        sx, sy = im["width"] / prim.width, im["height"] / prim.height
        centres = [((ln["x0"] + ln["x1"]) / 2.0 * sx, (prim.height - (ln["y0"] + ln["y1"]) / 2.0) * sy) for ln in geom.lines]
        centres += [((b["rect"][0] + b["rect"][2]) / 2.0 * sx, (prim.height - (b["rect"][1] + b["rect"][3]) / 2.0) * sy)
                    for b in geom.blocks]

        def held(r: Sequence[float]) -> FrozenSet[int]:
            return frozenset(i for i, (cx, cy) in enumerate(centres) if r[0] <= cx <= r[2] and r[1] <= cy <= r[3])

        regions = [[a["rect"][0] * sx, (prim.height - a["rect"][3]) * sy, a["rect"][2] * sx, (prim.height - a["rect"][1]) * sy]
                   for a in baked.get("activities") or []]
        raw = [b["px"] for b in ((pred.get("pages") or {}).get(str(page)) or {}).get("boxes") or [] if b["score"] >= conf]
        regions_h, raw_h = [(r, held(r)) for r in regions], [(r, held(r)) for r in raw]
        pages.append({"page": page, "teacher": len(boxes), "regions": len(regions),
                      "extra": sum(1 for r in regions if max(iou(r, t) for t in boxes) < EXTRA_IOU)})
        for t in boxes:
            th = held(t)
            (ri, rc), (si, sc) = best(t, th, raw_h), best(t, th, regions_h)
            rows.append({"page": page, "tall": (t[3] - t[1]) > (TALL_REGION - 0.005) * im["height"],
                         "raw_iou": round(ri, 3), "raw_content": round(rc, 3), "iou": round(si, 3), "content": round(sc, 3)})
    return {"book": book_id, "boxes": rows, "pages": pages}


def summary(rows: List[Dict[str, Any]], pages: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    fair = [r for r in rows if not r["tall"]]
    n = len(fair)
    out: Dict[str, Any] = {"teacher_boxes": len(rows), "taller_than_a_hotspot": len(rows) - n,
                           "regions": sum(p["regions"] for p in pages),
                           "regions_answering_no_teacher_box": sum(p["extra"] for p in pages),
                           "empty_pages": sum(1 for p in pages if not p["teacher"]),
                           "empty_pages_with_a_region": sum(1 for p in pages if not p["teacher"] and p["regions"])}
    if not n:
        return out
    for who, i_key, c_key in (("student", "raw_iou", "raw_content"), ("baked", "iou", "content")):
        for level in IOU_LEVELS:
            out[f"{who}_iou_{level:g}"] = round(sum(r[i_key] >= level for r in fair) / n, 3)
        out[f"{who}_same_content"] = round(sum(r[c_key] >= SAME_CONTENT for r in fair) / n, 3)
    out["worse_than_the_student_drew"] = sum(r["content"] < r["raw_content"] - 0.05 for r in fair)
    out["better_than_the_student_drew"] = sum(r["content"] > r["raw_content"] + 0.05 for r in fair)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=("valid", "train", "heldout"), default="valid",
                    help="heldout is scored once per phase, at the end (PLAN.md §0.3 rule 7)")
    ap.add_argument("--bake-root", default=str(BAKE_ROOT))
    ap.add_argument("--pred", default=str(PRED_DIR))
    ap.add_argument("--only", default=None, help="comma-separated book id prefixes")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    with open(DATASET_DIR / args.split / "_annotations.coco.json", encoding="utf-8") as fh:
        books = sorted({im["book"] for im in json.load(fh)["images"]})
    if args.only:
        prefixes = args.only.split(",")
        books = [b for b in books if any(b.startswith(p) for p in prefixes)]
    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1) as pool:
        results = list(pool.map(_book, [(b, args.split, args.bake_root, args.pred) for b in books]))

    head = f"{'book':<10}{'boxes':>6}{'tall':>6} | student: {'IoU.5':>6}{'IoU.85':>7}{'same':>6} | baked: {'IoU.5':>6}{'IoU.85':>7}{'same':>6} | {'worse':>5}{'better':>7}"
    print(f"{args.split}, bake {args.bake_root}\n{head}")

    def line(name: str, s: Dict[str, Any]) -> str:
        if "student_same_content" not in s:
            return f"{name:<10}{s['teacher_boxes']:>6}{s['taller_than_a_hotspot']:>6} |"
        return (f"{name:<10}{s['teacher_boxes']:>6}{s['taller_than_a_hotspot']:>6} | student: {s['student_iou_0.5']:>6.3f}"
                f"{s['student_iou_0.85']:>7.3f}{s['student_same_content']:>6.3f} | baked: {s['baked_iou_0.5']:>6.3f}"
                f"{s['baked_iou_0.85']:>7.3f}{s['baked_same_content']:>6.3f} | {s['worse_than_the_student_drew']:>5}"
                f"{s['better_than_the_student_drew']:>7}")

    out: Dict[str, Any] = {"split": args.split, "bake_root": args.bake_root, "books": {}}
    every: List[Dict[str, Any]] = []
    every_pages: List[Dict[str, Any]] = []
    for r in results:
        if "error" in r:
            print(f"{r['book'][:8]:<10}{r['error']}")
            continue
        s = summary(r["boxes"], r["pages"])
        out["books"][r["book"]] = s
        every += r["boxes"]
        every_pages += r["pages"]
        print(line(r["book"][:8], s))
    out["total"] = t = summary(every, every_pages)
    print(line("TOTAL", t))
    if t["regions"]:
        print(f"regions on these pages: {t['regions']}, of which {t['regions_answering_no_teacher_box']} "
              f"({t['regions_answering_no_teacher_box'] / t['regions']:.1%}) answer no teacher box; "
              f"{t['empty_pages_with_a_region']} of {t['empty_pages']} pages the teacher left empty carry a region")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        print("wrote", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
