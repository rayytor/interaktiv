#!/usr/bin/env python3
"""
Phase 1 of the vision plan: render every page of every local textbook once.

For each book, one JPEG per page at a fixed width (1024 px, height follows the
page aspect) and an index that maps image pixels back to PDF points:

    data/vision/pages/<book-id>/0031.jpg
    data/vision/pages/<book-id>/index.jsonl     one line per page
    data/vision/pages/<book-id>/fingerprint.txt serializer.compute_fingerprint

Pages are numbered from 1, the same numbering as `regions.json` and the
reader. An index line:

    {"page": 31, "width_pt": 569.764, "height_pt": 796.535, "width_px": 1024, "height_px": 1432}

Pixel (px, py) with y down maps to PDF point (px / sx, height_pt - py / sy)
with sx = width_px / width_pt and sy = height_px / height_pt.

Memory rules (PLAN.md §0.3): one short-lived child process per book,
`max_tasks_per_child=1`, two workers by default, per-page state released
between pages, peak RSS reported per book and for the whole run. Resumable:
a page whose JPEG exists is skipped (`--force` redoes it), and a book whose
stored fingerprint no longer matches its PDF is re-rendered in full.

Usage:
    .venv-vision/bin/python tools/hotspot_extraction/vision/render.py
    .venv-vision/bin/python tools/hotspot_extraction/vision/render.py --only 0a3fbb41 --pages 30-32
    .venv-vision/bin/python tools/hotspot_extraction/vision/render.py --workers 2 --force
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import ctypes
import gc
import json
import os
from pathlib import Path
import re
import resource
import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psutil
import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.scanner.serializer import compute_fingerprint  # noqa: E402

DEFAULT_BOOKS_DIR = PROJECT_ROOT / "books"
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "vision" / "pages"
DEFAULT_WIDTH = 1024
JPEG_QUALITY = 90

UUID_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")


# --------------------------------------------------------------------------- #
# Rendering one page                                                          #
# --------------------------------------------------------------------------- #

def page_filename(page: int) -> str:
    return f"{page:04d}.jpg"


def render_page(page: pymupdf.Page, width_px: int = DEFAULT_WIDTH) -> Tuple[pymupdf.Pixmap, Dict[str, Any]]:
    """Render one page to an RGB pixmap exactly `width_px` wide.

    Returns the pixmap and its index record. The caller owns the pixmap and
    should drop it as soon as it is saved.
    """
    rect = page.rect
    width_pt = float(rect.width)
    height_pt = float(rect.height)
    zoom = width_px / width_pt
    # Clip to the page rect so the pixmap is exactly the page, not the
    # mediabox; alpha off keeps it RGB and a third smaller in RAM.
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, clip=rect)
    if pix.width != width_px:
        raise RuntimeError(f"page {page.number + 1}: pixmap is {pix.width} px wide, wanted {width_px}")
    record = {
        "page": page.number + 1,
        "width_pt": round(width_pt, 3),
        "height_pt": round(height_pt, 3),
        "width_px": pix.width,
        "height_px": pix.height,
    }
    return pix, record


def render_page_to_file(page: pymupdf.Page, out_path: Path, width_px: int = DEFAULT_WIDTH) -> Dict[str, Any]:
    pix, record = render_page(page, width_px)
    tmp = out_path.with_suffix(".jpg.part")
    pix.save(str(tmp), output="jpeg", jpg_quality=JPEG_QUALITY)
    os.replace(tmp, out_path)
    pix = None
    return record


def _parse_pages(spec: Optional[str], page_count: int) -> List[int]:
    """'31' or '30-32' or '1,5,9-12' -> 1-based page numbers within the book."""
    if not spec:
        return list(range(1, page_count + 1))
    pages: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(part)
        for p in range(max(1, lo), min(page_count, hi) + 1):
            if p not in pages:
                pages.append(p)
    return pages


def _trim_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Rendering one book (runs in a child process)                                #
# --------------------------------------------------------------------------- #

def render_book(task: Dict[str, Any]) -> Dict[str, Any]:
    pymupdf.TOOLS.set_low_memory(True)
    t0 = time.time()
    book_id: str = task["book_id"]
    pdf_path: str = task["pdf_path"]
    out_dir = Path(task["out_dir"]) / book_id
    width_px: int = task.get("width", DEFAULT_WIDTH)
    force: bool = bool(task.get("force"))
    pages_spec: Optional[str] = task.get("pages")

    result: Dict[str, Any] = {"status": "error", "book_id": book_id, "title": task.get("title", book_id)}
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        fp = compute_fingerprint(pdf_path)
        fp_file = out_dir / "fingerprint.txt"
        stored = fp_file.read_text().strip() if fp_file.is_file() else None
        if stored is not None and stored != fp:
            # The PDF was re-exported: every old JPEG is stale.
            force = True
            result["refingerprinted"] = True

        doc = pymupdf.open(pdf_path)
        try:
            page_count = doc.page_count
            wanted = _parse_pages(pages_spec, page_count)
            rendered = 0
            skipped = 0
            index: List[Dict[str, Any]] = []
            index_path = out_dir / "index.jsonl"
            existing: Dict[int, Dict[str, Any]] = {}
            if index_path.is_file() and not force:
                with open(index_path) as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            rec = json.loads(line)
                            existing[int(rec["page"])] = rec

            for pno in wanted:
                out_path = out_dir / page_filename(pno)
                if out_path.is_file() and not force and pno in existing:
                    index.append(existing[pno])
                    skipped += 1
                    continue
                page = doc[pno - 1]
                rec = render_page_to_file(page, out_path, width_px)
                index.append(rec)
                rendered += 1
                page = None
                if rendered % 50 == 0:
                    _trim_memory()

            # Keep index lines for pages outside this run's --pages range.
            merged = dict(existing)
            for rec in index:
                merged[int(rec["page"])] = rec
            with open(index_path.with_suffix(".jsonl.part"), "w") as fh:
                for pno in sorted(merged):
                    fh.write(json.dumps(merged[pno], separators=(",", ":")) + "\n")
            os.replace(index_path.with_suffix(".jsonl.part"), index_path)
            fp_file.write_text(fp + "\n")
        finally:
            doc.close()
            doc = None
            _trim_memory()

        jpgs = [p for p in out_dir.glob("*.jpg")]
        result.update({
            "status": "rendered" if rendered else "skipped",
            "page_count": page_count,
            "wanted": len(wanted),
            "rendered": rendered,
            "skipped": skipped,
            "files": len(jpgs),
            "bytes": sum(p.stat().st_size for p in jpgs),
            "fingerprint": fp,
        })
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"

    result["duration"] = round(time.time() - t0, 2)
    # ru_maxrss is the process's peak RSS in KiB on Linux: the honest number,
    # since RSS at return time is after everything was freed.
    result["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    try:
        result["rss_mb"] = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:
        result["rss_mb"] = 0.0
    return result


# --------------------------------------------------------------------------- #
# Book discovery                                                              #
# --------------------------------------------------------------------------- #

def discover_books(books_dir: Path, only: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """Every PDF in `books_dir`, one entry per distinct book.

    The book id is the UUID in the file name, else the file stem. Two files
    with the same fingerprint are the same book (books/full_pdf.pdf and
    books/matematik.pdf are legacy copies of catalogue books); the UUID-named
    file wins, so the id matches `activities/books/` and `train/splits.json`.
    """
    found: Dict[str, Dict[str, Any]] = {}
    for entry in sorted(os.listdir(books_dir)):
        if not entry.lower().endswith(".pdf"):
            continue
        path = Path(os.path.realpath(books_dir / entry))
        if not path.is_file():
            continue
        m = UUID_RE.search(entry)
        book_id = m.group(1).lower() if m else path.stem
        fp = compute_fingerprint(str(path))
        dup = found.get(fp)
        if dup is not None:
            if m and not dup["uuid_named"]:
                dup.update(book_id=book_id, pdf_path=str(path), uuid_named=True, aliases=dup["aliases"] + [dup["pdf_path"]])
            else:
                dup["aliases"].append(str(path))
            continue
        found[fp] = {"book_id": book_id, "pdf_path": str(path), "fingerprint": fp,
                     "uuid_named": bool(m), "aliases": [], "size": path.stat().st_size}
    books = list(found.values())
    for b in books:
        b["title"] = b["book_id"]
    try:
        from books_manager import BooksManager  # noqa: WPS433
        manager = BooksManager(base_dir=str(PROJECT_ROOT), edition="full")
        titles = {b["id"]: b.get("title", b["id"]) for b in manager.books if b.get("id")}
        for b in books:
            b["title"] = titles.get(b["book_id"], b["book_id"])
    except Exception:
        pass
    if only:
        wanted = [w.strip().lower() for w in only if w.strip()]
        books = [b for b in books if any(w in b["book_id"].lower() or w in b["title"].lower() for w in wanted)]
    # Largest first, interleaved with smallest, so two workers never both hold a 500 MB book.
    books.sort(key=lambda b: b["size"], reverse=True)
    out: List[Dict[str, Any]] = []
    lo, hi = 0, len(books) - 1
    while lo <= hi:
        out.append(books[lo]); lo += 1
        if lo <= hi:
            out.append(books[hi]); hi -= 1
    return out


# --------------------------------------------------------------------------- #
# Memory monitor (same shape as scan.py)                                      #
# --------------------------------------------------------------------------- #

class MemoryMonitor:
    def __init__(self, interval: float = 0.1):
        self.interval = interval
        self.stop_event = threading.Event()
        self.peak_workers_rss = 0
        self.peak_total_rss = 0
        self.thread: Optional[threading.Thread] = None

    def _sample(self) -> None:
        parent = psutil.Process(os.getpid())
        while not self.stop_event.is_set():
            try:
                parent_rss = parent.memory_info().rss
                workers = 0
                for child in parent.children(recursive=True):
                    try:
                        if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                            workers += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                self.peak_workers_rss = max(self.peak_workers_rss, workers)
                self.peak_total_rss = max(self.peak_total_rss, parent_rss + workers)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            time.sleep(self.interval)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()

    def stop(self) -> Dict[str, float]:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.0)
        return {"workers_rss_mb": round(self.peak_workers_rss / 2**20, 1),
                "total_rss_mb": round(self.peak_total_rss / 2**20, 1)}


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--books-dir", default=str(DEFAULT_BOOKS_DIR))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--only", default=None, help="comma-separated book id prefixes or title words")
    ap.add_argument("--pages", default=None, help="page numbers for a smoke run, e.g. 31 or 30-32,40")
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--force", action="store_true", help="re-render pages whose JPEG exists")
    ap.add_argument("--summary", default=None, help="write the run summary JSON here")
    args = ap.parse_args(argv)

    books = discover_books(Path(args.books_dir), args.only.split(",") if args.only else None)
    if not books:
        print("no books found")
        return 1
    aliases = sum(len(b["aliases"]) for b in books)
    print(f"{len(books)} book(s), {sum(b['size'] for b in books) / 2**20:,.0f} MB of PDF"
          + (f", {aliases} duplicate file(s) folded into their catalogue book" if aliases else "")
          + f" | {args.workers} worker(s), width {args.width} px, q{JPEG_QUALITY}"
          + (f", pages {args.pages}" if args.pages else "")
          + (", --force" if args.force else ""))

    tasks = [{"book_id": b["book_id"], "pdf_path": b["pdf_path"], "title": b["title"],
              "out_dir": args.out_dir, "width": args.width, "force": args.force, "pages": args.pages}
             for b in books]

    monitor = MemoryMonitor()
    monitor.start()
    t0 = time.time()
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1) as pool:
        futures = {pool.submit(render_book, t): t for t in tasks}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = {"status": "error", "book_id": t["book_id"], "title": t["title"], "error": str(exc)}
            results.append(res)
            bid = res["book_id"][:8]
            title = str(res.get("title", ""))[:34].ljust(34)
            if res["status"] in ("rendered", "skipped"):
                print(f"  {'✓' if res['status'] == 'rendered' else '-'} [{bid}] {title} | "
                      f"{res['rendered']:3d} rendered, {res['skipped']:3d} kept of {res['page_count']:3d} pgs | "
                      f"{res['duration']:6.1f}s | {res['bytes'] / 2**20:6.1f} MB | peak {res['peak_rss_mb']:.0f} MB RSS"
                      + (" | PDF changed, re-rendered" if res.get("refingerprinted") else ""), flush=True)
            else:
                print(f"  ✗ [{bid}] {title} | FAILED: {res.get('error')}", flush=True)

    wall = time.time() - t0
    mem = monitor.stop()
    ok = [r for r in results if r["status"] in ("rendered", "skipped")]
    summary = {
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "books": len(results),
        "failed": [r["book_id"] for r in results if r["status"] == "error"],
        "pages": sum(r.get("page_count", 0) for r in ok),
        "rendered": sum(r.get("rendered", 0) for r in ok),
        "kept": sum(r.get("skipped", 0) for r in ok),
        "files": sum(r.get("files", 0) for r in ok),
        "bytes": sum(r.get("bytes", 0) for r in ok),
        "wall_seconds": round(wall, 1),
        "workers": args.workers,
        "width_px": args.width,
        "peak_worker_rss_mb": max((r.get("peak_rss_mb", 0) for r in ok), default=0),
        "peak_workers_rss_mb_sum": mem["workers_rss_mb"],
        "peak_total_rss_mb": mem["total_rss_mb"],
        "per_book": sorted(results, key=lambda r: r["book_id"]),
    }
    print("=" * 72)
    print(f"RENDER COMPLETE  {summary['files']} files, {summary['bytes'] / 2**30:.2f} GB, "
          f"{summary['rendered']} rendered + {summary['kept']} kept of {summary['pages']} pages, "
          f"{wall:.0f}s wall ({summary['rendered'] / max(wall, 1e-3):.1f} pg/s)")
    print(f"Peak RSS: one worker {summary['peak_worker_rss_mb']:.0f} MB, all workers together "
          f"{mem['workers_rss_mb']:.0f} MB, total {mem['total_rss_mb']:.0f} MB; failed: {len(summary['failed'])}")
    if args.summary:
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        with open(args.summary, "w") as fh:
            json.dump(summary, fh, indent=1)
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
