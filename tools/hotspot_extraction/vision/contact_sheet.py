#!/usr/bin/env python3
"""
Phase 2 of the vision plan: pages with their teacher boxes drawn, for a person to look at.

Nobody draws or corrects a box (PLAN.md §0.3 rule 5); the user only looks at
these sheets and says whether the teacher is trustworthy. Each sheet holds
`--per-sheet` pages at `--scale` of their 1024 px render, boxes in red with
the teacher's label, and a caption per page with the book, page, verdict and
its counts. Pages come from `verdicts.jsonl` (filtered by `--verdict`) or
straight from a label folder.

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/contact_sheet.py --verdicts data/vision/labels/verdicts.jsonl --verdict trusted --n 48
    .venv-vision/bin/python tools/hotspot_extraction/vision/contact_sheet.py --labels data/vision/labels/api/v1 --n 12 --out tools/hotspot_extraction/vision/runs/phase2-sheets
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.vision.qa import label_files, load_label  # noqa: E402

PAGES_DIR = PROJECT_ROOT / "data" / "vision" / "pages"
DEFAULT_OUT = Path(__file__).resolve().parent / "runs" / "phase2-sheets"
RED = (220, 30, 30)
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _font(size: int):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default()


def draw_page(label: Dict[str, Any], scale: float, caption: str) -> Image.Image:
    """One page with its boxes, scaled, with a caption strip underneath."""
    book, page = label["book"], int(label["page"])
    with Image.open(PAGES_DIR / book / f"{page:04d}.jpg") as im:
        img = im.convert("RGB")
    d = ImageDraw.Draw(img)
    font = _font(max(14, int(30 * scale * 1.6)))
    for b in label.get("boxes") or []:
        x0, y0, x1, y1 = b["px"]
        d.rectangle([x0, y0, x1, y1], outline=RED, width=max(3, int(5 / scale * 0.6)))
        d.text((x0 + 6, y0 + 4), str(b.get("label") if b.get("label") is not None else "?"), fill=RED, font=font)
    if scale != 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    strip = 26
    out = Image.new("RGB", (img.width, img.height + strip), (245, 245, 245))
    out.paste(img, (0, 0))
    ImageDraw.Draw(out).text((4, img.height + 5), caption, fill=(20, 20, 20), font=_font(15))
    return out


def make_sheets(items: List[Dict[str, Any]], out_dir: Path, per_sheet: int, scale: float, prefix: str) -> List[Path]:
    """`items`: dicts with `label` (a loaded label file) and `caption`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    cols = 4 if per_sheet >= 8 else max(1, min(per_sheet, 3))
    for s in range(0, len(items), per_sheet):
        chunk = items[s:s + per_sheet]
        tiles = [draw_page(it["label"], scale, it["caption"]) for it in chunk]
        tw = max(t.width for t in tiles)
        th = max(t.height for t in tiles)
        rows = (len(tiles) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw + (cols + 1) * 8, rows * th + (rows + 1) * 8), (200, 200, 200))
        for i, t in enumerate(tiles):
            r, c = divmod(i, cols)
            sheet.paste(t, (8 + c * (tw + 8), 8 + r * (th + 8)))
        path = out_dir / f"{prefix}-{s // per_sheet + 1:02d}.png"
        sheet.save(path, optimize=True)
        written.append(path)
    return written


def items_from_verdicts(verdicts_path: Path, verdict: Optional[str], n: int, seed: int) -> List[Dict[str, Any]]:
    rows = [json.loads(l) for l in open(verdicts_path, encoding="utf-8") if l.strip()]
    rows = [r for r in rows if (verdict is None or r["verdict"] == verdict) and (r.get("reask_file") or r.get("label_file"))]
    rng = random.Random(seed)
    rows.sort(key=lambda r: (r["book"], r["page"]))
    if n and n < len(rows):
        rows = rng.sample(rows, n)
        rows.sort(key=lambda r: (r["book"], r["page"]))
    items = []
    for r in rows:
        path = r.get("reask_file") if r.get("source") == "reask" and r.get("reask_file") else r["label_file"]
        cap = (f"{r['book'][:8]} p{r['page']} {r['verdict']} | {r.get('boxes', 0)} boxes, cut {r.get('cut', 0)}, "
               f"ovl {r.get('overlap_major', 0)}+{r.get('overlap_minor', 0)}, miss {r.get('miss', 0)}/{r.get('icons', 0)}")
        items.append({"label": load_label(Path(path)), "caption": cap})
    return items


def items_from_labels(labels_dir: Path, n: int, seed: int) -> List[Dict[str, Any]]:
    files = label_files(labels_dir)
    rng = random.Random(seed)
    if n and n < len(files):
        files = sorted(rng.sample(files, n))
    items = []
    for book, page, f in files:
        lab = load_label(f)
        items.append({"label": lab, "caption": f"{book[:8]} p{page} | {len(lab.get('boxes') or [])} boxes, {lab.get('model', '')}"})
    return items


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verdicts", default=None)
    ap.add_argument("--verdict", default=None, help="only pages with this verdict (trusted, re-ask, rejected)")
    ap.add_argument("--labels", default=None, help="draw straight from a label folder instead")
    ap.add_argument("--n", type=int, default=48)
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--per-sheet", type=int, default=8)
    ap.add_argument("--scale", type=float, default=0.45)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--prefix", default="sheet")
    args = ap.parse_args(argv)
    if args.verdicts:
        items = items_from_verdicts(Path(args.verdicts), args.verdict, args.n, args.seed)
    elif args.labels:
        items = items_from_labels(Path(args.labels), args.n, args.seed)
    else:
        ap.error("give --verdicts or --labels")
    if not items:
        print("nothing to draw")
        return 1
    paths = make_sheets(items, Path(args.out), args.per_sheet, args.scale, args.prefix)
    print(f"{len(items)} page(s) on {len(paths)} sheet(s):")
    for p in paths:
        print("  ", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
