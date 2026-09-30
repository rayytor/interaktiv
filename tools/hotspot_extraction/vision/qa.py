#!/usr/bin/env python3
"""
Phase 2 of the vision plan: the label quality gate, and the agreement check.

For every teacher label file under `--labels` the page is judged with the
machinery the scorecard already uses, without opening a PDF: the bake's
`diagnostics.json.gz` gives the drawn panels, solution blocks and markers,
`regions.json` gives the folio map, `activities_meta/<id>.json` the
publisher's icons. Per page:

  cut       a box edge lies inside a drawn panel or solution block
            (`scanner.regions.cuts`, same tolerance as the scorecard).
            Recorded, not a defect: the teacher cannot see vectors and
            Phase 5's snapper moves the edge.
  sliver    a box narrower or shorter than MIN_HOTSPOT points.       defect
  tall      a box taller than TALL_REGION of the page.               recorded
  overlap   two boxes intersect. `minor` when the intersection is
            thinner than 6 pt in one direction (an edge touch that
            snapping fixes); `major` otherwise.                       major = defect
  miss      a publisher icon (posx/posy) on the page that lies in no
            box. The publisher hangs the icon in the margin beside the
            activity's label, about 30 pt outside the box, so a box is
            widened by `--xband` of the page width sideways and by
            `--band` pt vertically before the test.                  defect
  labels    the printed labels should be a plausible run (a, b, c or
            1, 2, 3); a duplicate on non-touching boxes is allowed
            and flagged.                                              recorded

Verdict: `trusted` (parsed, no slivers, no major overlaps, no misses),
`re-ask` (misses or major overlaps, or the request failed), `rejected`
(the page has a re-ask label under `--reask` and still fails). A page
with no boxes and no publisher icon is `trusted` and empty.

Writes `data/vision/labels/verdicts.jsonl` (one line per page), prints a
per-book table, and with `--write-reask` a selection file in the format of
`select.py` carrying `hint_count` for `teacher.py --hint`.

`--agree A B` instead compares two label folders page by page: greedy IoU
matching at 0.7, agreement per page = 2·matches / (boxes in A + boxes in B)
(1.0 when both are empty), and the mean over the pages both folders hold.

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/qa.py --labels data/vision/labels/api/v1
    .venv-vision/bin/python tools/hotspot_extraction/vision/qa.py --labels data/vision/labels/api/v1 --reask data/vision/labels/api/v1-reask --summary tools/hotspot_extraction/vision/runs/phase2-qa.json
    .venv-vision/bin/python tools/hotspot_extraction/vision/qa.py --agree data/vision/labels/api/v1 data/vision/labels/api/v1-agree
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.scanner.regions import cuts, rect_overlap  # noqa: E402
from tools.hotspot_extraction.scanner.score import (  # noqa: E402
    MIN_HOTSPOT, TALL_REGION, WHOLE_TOL, interactive_oges, takeable,
)

LABELS_DIR = PROJECT_ROOT / "data" / "vision" / "labels"
BAKES_DIR = PROJECT_ROOT / "activities" / "books"
DEFAULT_VERDICTS = LABELS_DIR / "verdicts.jsonl"
DEFAULT_REASK = PROJECT_ROOT / "data" / "vision" / "select" / "reask.json"
DEFAULT_BAND = 16.0          # pt above or below a box an icon may still sit
DEFAULT_XBAND = 0.10         # of the page width, sideways: the icon hangs in the margin beside the label
MINOR_OVERLAP = 6.0          # pt; thinner intersections are edge touches, not stolen clicks
AGREE_IOU = 0.7

Rect = Tuple[float, float, float, float]


# --------------------------------------------------------------------------- #
# Book context: bake geometry, folio, publisher icons                         #
# --------------------------------------------------------------------------- #

class BookContext:
    """Everything one book's pages are judged against, loaded once per book."""

    def __init__(self, book_id: str, root: Path = PROJECT_ROOT, bakes_dir: Path = BAKES_DIR):
        self.book_id = book_id
        self.diag: Dict[str, Any] = {}
        self.folio: Dict[str, Any] = {}
        self.oges_by_page: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        self.baked = False
        regions_path = bakes_dir / book_id / "regions.json"
        if regions_path.is_file():
            with open(regions_path, encoding="utf-8") as fh:
                bake = json.load(fh)
            self.baked = True
            self.folio = bake.get("folio", {}).get("byPage") or {}
            diag_path = bakes_dir / book_id / "diagnostics.json.gz"
            if diag_path.is_file():
                with gzip.open(diag_path, "rb") as fh:
                    diag_all = json.loads(fh.read().decode("utf-8"))
                if diag_all.get("fingerprint") == bake.get("fingerprint"):
                    self.diag = diag_all.get("pages") or {}
            printed_to_pdf: Dict[int, List[int]] = defaultdict(list)
            for pdf_page, printed in self.folio.items():
                if printed is not None:
                    printed_to_pdf[int(printed)].append(int(pdf_page))
            for o in interactive_oges(book_id, str(root)):
                if o.get("posx") == 0 and o.get("posy") == 0:
                    continue
                for p in printed_to_pdf.get(int(o["sayfano"]), []):
                    self.oges_by_page[p].append(o)

    def page_diag(self, page: int) -> Optional[Dict[str, List[Rect]]]:
        dp = self.diag.get(str(page))
        if not dp:
            return None
        return {k: [tuple(r) for r in dp.get(k) or []] for k in ("panels", "solutions", "markers")}

    def icons(self, page: int, page_w: float, page_h: float) -> List[Dict[str, Any]]:
        """Publisher icons on the page as PDF points (y up), like `scanner.anchors`."""
        out = []
        for o in self.oges_by_page.get(page, []):
            out.append({"id": o["id"], "title": o.get("baslik") or "",
                        "x": float(o["posx"]) / 100.0 * page_w,
                        "y": page_h - float(o["posy"]) / 100.0 * page_h})
        return out


# --------------------------------------------------------------------------- #
# Judging one page                                                            #
# --------------------------------------------------------------------------- #

def _inside(x: float, y: float, r: Rect, band: float, xband: float) -> bool:
    return r[0] - xband <= x <= r[2] + xband and r[1] - band <= y <= r[3] + band


def label_run_ok(labels: Sequence[Optional[str]]) -> Tuple[bool, int]:
    """(plausible sequence?, duplicate count). Letters a.. or digits 1.. in order, duplicates allowed."""
    got = [str(l).strip().lower().rstrip(".)") for l in labels if l is not None and str(l).strip()]
    dups = sum(c - 1 for c in Counter(got).values() if c > 1)
    if not got:
        return True, 0
    letters = [l for l in got if re.fullmatch(r"[a-zğışçöü]", l)]
    digits = [l for l in got if re.fullmatch(r"\d{1,2}", l)]
    if len(letters) + len(digits) != len(got):
        return False, dups
    if letters:
        order = sorted(set(letters))
        alphabet = "abcdefghijklmnopqrstuvwxyz"
        idx = [alphabet.find(l) for l in order if l in alphabet]
        if idx != list(range(len(idx))) and idx != list(range(idx[0] if idx else 0, (idx[0] if idx else 0) + len(idx))):
            return False, dups
    if digits:
        nums = sorted(set(int(d) for d in digits))
        if nums != list(range(nums[0], nums[0] + len(nums))):
            return False, dups
    return True, dups


def judge_page(label: Dict[str, Any], ctx: BookContext, band: float = DEFAULT_BAND,
               xband: float = DEFAULT_XBAND) -> Dict[str, Any]:
    page = int(label["page"])
    index = label["index"]
    page_w, page_h = float(index["width_pt"]), float(index["height_pt"])
    boxes = label.get("boxes") or []
    rects: List[Rect] = [tuple(float(v) for v in b["rect"]) for b in boxes]
    diag = ctx.page_diag(page)

    cut = sliver = tall = 0
    for r in rects:
        w, h = r[2] - r[0], r[3] - r[1]
        if w < MIN_HOTSPOT or h < MIN_HOTSPOT:
            sliver += 1
        if h > TALL_REGION * page_h:
            tall += 1
        if diag is not None:
            panels = diag["panels"]
            markers = diag["markers"]
            if markers:
                from tools.hotspot_extraction.scanner.regions import encloses
                panels = [p for p in panels if sum(1 for m in markers if encloses(p, m, tol=-2.0)) <= 1]
            for block in takeable(panels, r, page_w, page_h, True):
                if cuts(r, block, whole_tol=WHOLE_TOL):
                    cut += 1
            for block in takeable(diag["solutions"], r, page_w, page_h, False):
                if cuts(r, block, whole_tol=WHOLE_TOL):
                    cut += 1

    overlap_major = overlap_minor = 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            if rect_overlap(a, b) <= 1.0:
                continue
            iw = min(a[2], b[2]) - max(a[0], b[0])
            ih = min(a[3], b[3]) - max(a[1], b[1])
            if min(iw, ih) < MINOR_OVERLAP:
                overlap_minor += 1
            else:
                overlap_major += 1

    icons = ctx.icons(page, page_w, page_h)
    xb = max(band, xband * page_w)
    missed = [ic for ic in icons if not any(_inside(ic["x"], ic["y"], r, band, xb) for r in rects)]
    labels_ok, dups = label_run_ok([b.get("label") for b in boxes])

    defects = []
    if sliver:
        defects.append("sliver")
    if overlap_major:
        defects.append("overlap")
    if missed:
        defects.append("miss")
    return {
        "book": label["book"], "page": page, "boxes": len(boxes), "cut": cut, "sliver": sliver, "tall": tall,
        "overlap_major": overlap_major, "overlap_minor": overlap_minor, "icons": len(icons), "miss": len(missed),
        "missed_icons": [ic["title"] for ic in missed], "labels_ok": labels_ok, "duplicate_labels": dups,
        "labels": [b.get("label") for b in boxes], "has_diag": diag is not None, "defects": defects,
        "passes": not defects,
    }


# --------------------------------------------------------------------------- #
# Walking a label folder                                                      #
# --------------------------------------------------------------------------- #

def label_files(labels_dir: Path) -> List[Tuple[str, int, Path]]:
    out = []
    for book_dir in sorted(p for p in labels_dir.iterdir() if p.is_dir() and p.name != "batches"):
        for f in sorted(book_dir.glob("*.json")):
            if f.stem.isdigit():
                out.append((book_dir.name, int(f.stem), f))
    return out


def failed_files(labels_dir: Path) -> List[Tuple[str, int]]:
    out = []
    for book_dir in sorted(p for p in labels_dir.iterdir() if p.is_dir()):
        for f in book_dir.glob("*.failed"):
            if f.stem.isdigit():
                out.append((book_dir.name, int(f.stem)))
    return out


def load_label(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run_gate(labels_dir: Path, reask_dir: Optional[Path], selection: Optional[Dict[str, Any]],
             band: float = DEFAULT_BAND, root: Path = PROJECT_ROOT, bakes_dir: Path = BAKES_DIR,
             xband: float = DEFAULT_XBAND) -> List[Dict[str, Any]]:
    ctxs: Dict[str, BookContext] = {}
    sel_info: Dict[Tuple[str, int], Dict[str, Any]] = {}
    splits: Dict[str, str] = {}
    if selection:
        for book_id, b in selection["books"].items():
            splits[book_id] = b.get("split", "train")
            for rec in b["pages"]:
                sel_info[(book_id, int(rec["page"]))] = rec

    def ctx_for(book_id: str) -> BookContext:
        if book_id not in ctxs:
            ctxs[book_id] = BookContext(book_id, root, bakes_dir)
        return ctxs[book_id]

    verdicts: List[Dict[str, Any]] = []
    seen = set()
    primary = {(b, p): f for b, p, f in label_files(labels_dir)}
    failed = set(failed_files(labels_dir))
    reask = {(b, p): f for b, p, f in label_files(reask_dir)} if reask_dir and reask_dir.is_dir() else {}
    reask_failed = set(failed_files(reask_dir)) if reask_dir and reask_dir.is_dir() else set()

    for key in sorted(set(primary) | failed):
        book_id, page = key
        seen.add(key)
        ctx = ctx_for(book_id)
        first = judge_page(load_label(primary[key]), ctx, band, xband) if key in primary else None
        second = judge_page(load_label(reask[key]), ctx, band, xband) if key in reask else None
        if first and first["passes"]:
            verdict, source, judged = "trusted", "primary", first
        elif second and second["passes"]:
            verdict, source, judged = "trusted", "reask", second
        elif second or key in reask_failed:
            verdict, source, judged = "rejected", "reask", second or first
        else:
            verdict, source, judged = "re-ask", "primary", first
        row = {
            "book": book_id, "page": page, "verdict": verdict, "source": source,
            "split": splits.get(book_id, "train"), "stratum": sel_info.get(key, {}).get("stratum"),
            "request_failed": key in failed and key not in primary,
            "hint_count": len(ctx.oges_by_page.get(page, [])),
            "label_file": str(primary[key]) if key in primary else None,
            "reask_file": str(reask[key]) if key in reask else None,
        }
        if judged:
            row.update({k: v for k, v in judged.items() if k not in ("book", "page")})
        else:
            row.update({"boxes": 0, "cut": 0, "sliver": 0, "tall": 0, "overlap_major": 0, "overlap_minor": 0,
                        "icons": row["hint_count"], "miss": row["hint_count"], "missed_icons": [], "labels_ok": True,
                        "duplicate_labels": 0, "labels": [], "has_diag": False, "defects": ["failed"], "passes": False})
        verdicts.append(row)
    return verdicts


def summarize(verdicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_book: Dict[str, Dict[str, Any]] = {}
    for v in verdicts:
        b = by_book.setdefault(v["book"], {"split": v["split"], "pages": 0, "trusted": 0, "re-ask": 0, "rejected": 0,
                                           "empty": 0, "boxes": 0, "cut": 0, "sliver": 0, "tall": 0, "overlap_major": 0,
                                           "overlap_minor": 0, "miss": 0, "icons": 0, "manifest_pages": 0,
                                           "manifest_trusted": 0, "labels_bad": 0, "failed": 0})
        b["pages"] += 1
        b[v["verdict"]] += 1
        for k in ("boxes", "cut", "sliver", "tall", "overlap_major", "overlap_minor", "miss", "icons"):
            b[k] += v.get(k, 0)
        if v.get("boxes", 0) == 0 and v["verdict"] == "trusted":
            b["empty"] += 1
        if v.get("icons", 0):
            b["manifest_pages"] += 1
            if v["verdict"] == "trusted":
                b["manifest_trusted"] += 1
        if not v.get("labels_ok", True):
            b["labels_bad"] += 1
        if v.get("request_failed"):
            b["failed"] += 1
    total = {k: sum(b[k] for b in by_book.values()) for k in next(iter(by_book.values()), {}) if k != "split"}
    return {"books": by_book, "total": total, "pages": len(verdicts)}


def print_table(summary: Dict[str, Any]) -> None:
    print(f"{'book':<10}{'split':<9}{'pages':>6}{'trust':>6}{'reask':>6}{'rej':>5}{'empty':>6} | "
          f"{'boxes':>6}{'cut':>5}{'sliv':>5}{'tall':>5}{'ovM':>5}{'ovm':>5}{'miss':>5}{'icons':>6} | manifest trusted")
    for book_id, b in sorted(summary["books"].items()):
        mp = b["manifest_pages"]
        print(f"{book_id[:8]:<10}{b['split']:<9}{b['pages']:>6}{b['trusted']:>6}{b['re-ask']:>6}{b['rejected']:>5}{b['empty']:>6} | "
              f"{b['boxes']:>6}{b['cut']:>5}{b['sliver']:>5}{b['tall']:>5}{b['overlap_major']:>5}{b['overlap_minor']:>5}"
              f"{b['miss']:>5}{b['icons']:>6} | {b['manifest_trusted']}/{mp}" + (f" ({100 * b['manifest_trusted'] / mp:.0f}%)" if mp else ""))
    t = summary["total"]
    if t:
        mp = t["manifest_pages"]
        print(f"{'total':<19}{t['pages']:>6}{t['trusted']:>6}{t['re-ask']:>6}{t['rejected']:>5}{t['empty']:>6} | "
              f"{t['boxes']:>6}{t['cut']:>5}{t['sliver']:>5}{t['tall']:>5}{t['overlap_major']:>5}{t['overlap_minor']:>5}"
              f"{t['miss']:>5}{t['icons']:>6} | {t['manifest_trusted']}/{mp}" + (f" ({100 * t['manifest_trusted'] / mp:.0f}%)" if mp else ""))


def write_reask_selection(verdicts: List[Dict[str, Any]], selection: Optional[Dict[str, Any]], out: Path) -> int:
    books: Dict[str, Any] = {}
    n = 0
    for v in verdicts:
        if v["verdict"] != "re-ask":
            continue
        b = books.setdefault(v["book"], {"title": (selection or {}).get("books", {}).get(v["book"], {}).get("title", v["book"]),
                                         "split": v["split"], "pages": []})
        b["pages"].append({"page": v["page"], "stratum": v.get("stratum"), "why": ",".join(v["defects"]),
                           "hint_count": v.get("hint_count", 0)})
        n += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"created": time.strftime("%Y-%m-%d %H:%M:%S"), "from": "qa.py re-ask", "books": books, "total": n},
                  fh, indent=1, ensure_ascii=False)
    return n


# --------------------------------------------------------------------------- #
# Agreement between two label folders                                         #
# --------------------------------------------------------------------------- #

def iou(a: Sequence[float], b: Sequence[float]) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def page_agreement(boxes_a: Sequence[Sequence[float]], boxes_b: Sequence[Sequence[float]],
                   thresh: float = AGREE_IOU) -> Tuple[float, int]:
    """Greedy IoU matching, best pair first. Returns (agreement, matches)."""
    if not boxes_a and not boxes_b:
        return 1.0, 0
    pairs = sorted(((iou(a, b), i, j) for i, a in enumerate(boxes_a) for j, b in enumerate(boxes_b)), reverse=True)
    used_a, used_b = set(), set()
    matches = 0
    for score, i, j in pairs:
        if score < thresh:
            break
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        matches += 1
    return 2.0 * matches / (len(boxes_a) + len(boxes_b)), matches


def run_agreement(dir_a: Path, dir_b: Path) -> Dict[str, Any]:
    a = {(b, p): f for b, p, f in label_files(dir_a)}
    b = {(bk, p): f for bk, p, f in label_files(dir_b)}
    common = sorted(set(a) & set(b))
    rows = []
    for key in common:
        la, lb = load_label(a[key]), load_label(b[key])
        agree, matches = page_agreement([x["px"] for x in la["boxes"]], [x["px"] for x in lb["boxes"]])
        rows.append({"book": key[0], "page": key[1], "boxes_a": len(la["boxes"]), "boxes_b": len(lb["boxes"]),
                     "matches": matches, "agreement": round(agree, 3)})
    mean = sum(r["agreement"] for r in rows) / len(rows) if rows else None
    return {"a": str(dir_a), "b": str(dir_b), "pages": len(rows), "only_a": len(set(a) - set(b)),
            "only_b": len(set(b) - set(a)), "mean_agreement": round(mean, 4) if mean is not None else None,
            "iou": AGREE_IOU, "rows": rows}


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", default=str(LABELS_DIR / "api" / "v1"))
    ap.add_argument("--reask", default=None, help="folder of re-ask labels for the same pages")
    ap.add_argument("--selection", default=str(PROJECT_ROOT / "data" / "vision" / "select" / "round1.json"))
    ap.add_argument("--band", type=float, default=DEFAULT_BAND, help="pt above/below a box an icon may sit")
    ap.add_argument("--xband", type=float, default=DEFAULT_XBAND, help="fraction of page width an icon may sit beside a box")
    ap.add_argument("--out", default=str(DEFAULT_VERDICTS))
    ap.add_argument("--summary", default=None, help="write the per-book summary JSON here")
    ap.add_argument("--write-reask", default=None, const=str(DEFAULT_REASK), nargs="?",
                    help="write a selection of the re-ask pages (default path if no value)")
    ap.add_argument("--agree", nargs=2, metavar=("DIR_A", "DIR_B"), default=None)
    args = ap.parse_args(argv)

    if args.agree:
        res = run_agreement(Path(args.agree[0]), Path(args.agree[1]))
        for r in res["rows"]:
            print(f"  {r['book'][:8]} p{r['page']:4d} | {r['boxes_a']:2d} vs {r['boxes_b']:2d} boxes, {r['matches']:2d} matched | {r['agreement']:.2f}")
        print(f"agreement over {res['pages']} page(s): mean {res['mean_agreement']} at IoU {res['iou']} "
              f"({res['only_a']} only in A, {res['only_b']} only in B)")
        if args.summary:
            Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
            with open(args.summary, "w") as fh:
                json.dump(res, fh, indent=1)
        return 0

    selection = None
    if args.selection and Path(args.selection).is_file():
        with open(args.selection, encoding="utf-8") as fh:
            selection = json.load(fh)
    verdicts = run_gate(Path(args.labels), Path(args.reask) if args.reask else None, selection, args.band, xband=args.xband)
    if not verdicts:
        print(f"no labels under {args.labels}")
        return 1
    summary = summarize(verdicts)
    print_table(summary)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for v in verdicts:
            fh.write(json.dumps(v, ensure_ascii=False) + "\n")
    print(f"wrote {len(verdicts)} verdict(s) to {out}")
    if args.write_reask:
        n = write_reask_selection(verdicts, selection, Path(args.write_reask))
        print(f"wrote {n} re-ask page(s) to {args.write_reask}")
    if args.summary:
        summary.update({"labels": str(args.labels), "reask": args.reask, "band": args.band, "xband": args.xband,
                        "created": time.strftime("%Y-%m-%d %H:%M:%S")})
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        with open(args.summary, "w") as fh:
            json.dump(summary, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
