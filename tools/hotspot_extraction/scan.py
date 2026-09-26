#!/usr/bin/env python3
"""
High-speed local activity hotspot scanner.

Uses PyMuPDF to extract layout, markers, drawn panels, solutions, and publisher anchors
in parallel across multiple worker processes with bounded memory (< 500 MB total RSS).

Usage:
    python3 tools/hotspot_extraction/scan.py --all
    python3 tools/hotspot_extraction/scan.py --all --workers 4
    python3 tools/hotspot_extraction/scan.py --only 0e966773,1cc573f6 --force
    python3 tools/hotspot_extraction/scan.py --all --quiet
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import ctypes
import gc
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import psutil
import pymupdf

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from books_manager import BooksManager
from tools.hotspot_extraction.scanner import (
    ActivityRegion,
    PageLayout,
    PagePrimitives,
    PublisherOge,
    compute_book_folio,
    compute_fingerprint,
    detect_book_calibration,
    detect_layout,
    detect_activity_markers,
    detect_panels,
    detect_solution_spaces,
    extract_page_primitives,
    GrowthTrace,
    grow_activity_regions,
    load_publisher_oges,
    reconcile_anchors,
    find_unplaced_anchors,
    save_diagnostics_gz,
    save_regions_json,
    serialize_book_regions,
    serialize_diagnostics,
)
from tools.hotspot_extraction.scanner.pipeline import detect_page
from tools.hotspot_extraction.scanner.trace import (
    save_trace_gz,
    serialize_page_trace,
    serialize_trace,
)
from tools.hotspot_extraction.scanner.profile import (
    DEFAULT_PROFILE_PATH,
    apply_profile,
    changed_from_default,
    from_dict,
    load_profile,
    profile_hash,
)


def _trim_memory() -> None:
    pymupdf.TOOLS.store_shrink(100)
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


class MemoryMonitor:
    """
    Background sampler that monitors memory across worker processes.
    Tracks USS (Unique Set Size - true unshared physical RAM) and RSS.
    """
    def __init__(self, sample_interval: float = 0.04):
        self.interval = sample_interval
        self.stop_event = threading.Event()
        self.peak_workers_uss: int = 0
        self.peak_workers_rss: int = 0
        self.peak_total_uss: int = 0
        self.peak_total_rss: int = 0
        self.thread: Optional[threading.Thread] = None

    def _sample(self) -> None:
        try:
            parent = psutil.Process(os.getpid())
        except Exception:
            return

        while not self.stop_event.is_set():
            try:
                try:
                    p_info = parent.memory_full_info()
                    parent_uss = p_info.uss
                    parent_rss = p_info.rss
                except Exception:
                    parent_uss = parent.memory_info().rss
                    parent_rss = parent_uss

                worker_uss = 0
                worker_rss = 0
                for child in parent.children(recursive=True):
                    try:
                        if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                            try:
                                c_info = child.memory_full_info()
                                worker_uss += c_info.uss
                                worker_rss += c_info.rss
                            except Exception:
                                r = child.memory_info().rss
                                worker_uss += r
                                worker_rss += r
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                if worker_uss > self.peak_workers_uss:
                    self.peak_workers_uss = worker_uss
                if worker_rss > self.peak_workers_rss:
                    self.peak_workers_rss = worker_rss

                total_uss = parent_uss + worker_uss
                total_rss = parent_rss + worker_rss
                if total_uss > self.peak_total_uss:
                    self.peak_total_uss = total_uss
                if total_rss > self.peak_total_rss:
                    self.peak_total_rss = total_rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            time.sleep(self.interval)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()

    def stop(self) -> Dict[str, int]:
        if self.thread:
            self.stop_event.set()
            self.thread.join(timeout=1.0)
        return {
            "workers_uss": self.peak_workers_uss,
            "workers_rss": self.peak_workers_rss,
            "total_uss": self.peak_total_uss,
            "total_rss": self.peak_total_rss,
        }


def scan_single_book(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Worker task to scan a single PDF book and serialize regions & diagnostics.
    """
    pymupdf.TOOLS.set_low_memory(True)

    book_id = task["book_id"]
    pdf_path = task["pdf_path"]
    out_dir = task["out_dir"]
    meta_dir = task["meta_dir"]
    force = task.get("force", False)
    quiet = task.get("quiet", False)
    want_trace = task.get("trace", False)
    title = task.get("title", book_id)

    # The profile travels as a plain dict rather than as a `Profile`, because
    # the task crosses a process boundary and a dict is the one form that
    # cannot depend on this module's import order in the child. Applying it
    # here, in the worker, is what makes each book's bake reproducible from the
    # task alone.
    profile = from_dict(task.get("profile") or {})
    apply_profile(profile)
    want_profile_hash = profile_hash(profile)

    t0 = time.time()
    try:
        fp = compute_fingerprint(pdf_path)
    except Exception as e:
        return {
            "status": "error",
            "book_id": book_id,
            "title": title,
            "error": f"Fingerprint calculation failed: {e}",
            "duration": round(time.time() - t0, 3),
        }

    if out_dir.lower().endswith(".json"):
        regions_file = out_dir
        diag_file = os.path.splitext(out_dir)[0] + ".diagnostics.json.gz"
        trace_file = os.path.splitext(out_dir)[0] + ".trace.json.gz"
        os.makedirs(os.path.dirname(os.path.abspath(regions_file)), exist_ok=True)
    else:
        book_out_dir = os.path.join(out_dir, book_id)
        regions_file = os.path.join(book_out_dir, "regions.json")
        diag_file = os.path.join(book_out_dir, "diagnostics.json.gz")
        trace_file = os.path.join(book_out_dir, "trace.json.gz")
        os.makedirs(book_out_dir, exist_ok=True)

    # Incremental skipping check. A bake is current only if the PDF *and* the
    # detector are unchanged: a retrained profile draws different regions from
    # the same bytes, so its hash is half of what "current" means.
    if (not force and os.path.isfile(regions_file) and os.path.isfile(diag_file)
            and (not want_trace or os.path.isfile(trace_file))):
        try:
            with open(regions_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("fingerprint") == fp and existing.get("profile") == want_profile_hash:
                return {
                    "status": "skipped",
                    "book_id": book_id,
                    "title": title,
                    "reason": "current",
                    "fingerprint": fp,
                    "duration": round(time.time() - t0, 3),
                }
        except Exception:
            pass

    # Open document and scan
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        return {
            "status": "error",
            "book_id": book_id,
            "title": title,
            "error": f"Failed to open PDF: {e}",
            "duration": round(time.time() - t0, 3),
        }

    page_count = doc.page_count
    try:
        folio_offset, folio_map = compute_book_folio(doc)

        meta_path = os.path.join(meta_dir, f"{book_id}.json")
        oges = load_publisher_oges(meta_path) if os.path.isfile(meta_path) else []

        pages_activities: Dict[int, List[ActivityRegion]] = {}
        pages_layout: Dict[int, PageLayout] = {}
        pages_dimensions: Dict[int, Tuple[float, float]] = {}
        diagnostics_by_page: Dict[int, Dict[str, Any]] = {}
        anchors_by_page: Dict[int, List[str]] = {}
        sample_spans: List[Any] = []
        page_traces: List[Dict[str, Any]] = []

        total_regions = 0
        total_anchored = 0
        total_questions = 0

        for p in range(1, page_count + 1):
            prim = extract_page_primitives(doc, p)
            pages_dimensions[p] = (prim.width, prim.height)

            printed_page = folio_map.get(p)
            page_oges = [o for o in oges if o.sayfano == printed_page] if printed_page is not None else []

            trace = GrowthTrace(record=want_trace)
            result = detect_page(
                prim,
                page_num=p,
                printed_page=printed_page,
                page_oges=page_oges,
                trace=trace,
            )
            layout = result.layout
            markers = result.markers
            activities = result.activities
            pages_layout[p] = layout
            if result.anchor_ids:
                anchors_by_page[p] = result.anchor_ids

            if want_trace:
                page_traces.append(serialize_page_trace(
                    page_num=p,
                    trace=trace,
                    region_ids=[a.id for a in activities],
                    printed_page=printed_page,
                    anchors=anchors_by_page.get(p, ()),
                ))

            if activities:
                pages_activities[p] = activities
                total_regions += len(activities)
                total_anchored += sum(1 for a in activities if a.anchored)
                total_questions += sum(len(a.items) for a in activities if a.items)

            # Diagnostics sidecar. The drawn geometry came back with the
            # regions rather than being rebuilt here, so the blocks a bake is
            # judged against are the ones its regions were grown among.
            diagnostics_by_page[p] = result.diagnostics(prim.width, prim.height, as_lists=True)

            if p <= 40:
                for s in prim.spans:
                    t_str = s.text.strip().lower()
                    if re.fullmatch(r"[a-z]", t_str) or re.fullmatch(r"\d{1,2}", t_str):
                        sample_spans.append(s)

            del prim, layout, markers, activities, result
            del trace
            _trim_memory()

        # Calibration
        cal_info = detect_book_calibration(
            doc=None,
            pages_activities=pages_activities,
            sample_spans=sample_spans,
        )

        # Serialization
        regions_json_data = serialize_book_regions(
            book_id=book_id,
            pdf_path=pdf_path,
            page_count=page_count,
            pages_activities=pages_activities,
            folio_map=folio_map,
            folio_offset=folio_offset,
            calibration_info=cal_info,
            pages_layout=pages_layout,
            page_dimensions=pages_dimensions,
            anchors_by_page=anchors_by_page,
        )

        diag_json_data = serialize_diagnostics(
            book_id=book_id,
            pdf_path=pdf_path,
            diagnostics_by_page=diagnostics_by_page,
            fingerprint=fp,
        )

        save_regions_json(regions_file, regions_json_data)
        save_diagnostics_gz(diag_file, diag_json_data)

        if want_trace:
            save_trace_gz(trace_file, serialize_trace(
                book_id=book_id,
                pdf_path=pdf_path,
                fingerprint=fp,
                pages=page_traces,
            ))

    except Exception as e:
        doc.close()
        return {
            "status": "error",
            "book_id": book_id,
            "title": title,
            "error": str(e),
            "duration": round(time.time() - t0, 3),
        }

    doc.close()
    doc = None
    _trim_memory()

    duration = time.time() - t0

    try:
        worker_rss = psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        worker_rss = 0.0

    return {
        "status": "baked",
        "book_id": book_id,
        "title": title,
        "page_count": page_count,
        "duration": round(duration, 3),
        "pages_per_sec": round(page_count / max(0.001, duration), 1),
        "total_regions": total_regions,
        "total_anchored": total_anchored,
        "total_questions": total_questions,
        "worker_rss_mb": round(worker_rss, 1),
        "fingerprint": fp,
    }


def discover_books(
    books_dir: str,
    meta_dir: str,
    catalog_path: str,
    only_ids: Optional[List[str]] = None,
    all_books: bool = False,
) -> List[Dict[str, Any]]:
    """
    Discover all available local PDF textbooks.
    """
    manager = BooksManager(base_dir=str(PROJECT_ROOT), edition="full")
    catalog_map = {b["id"]: b.get("title", b["id"]) for b in manager.books if b.get("id")}

    # Scan books_dir for all PDFs
    discovered: Dict[str, Dict[str, Any]] = {}
    uuid_pattern = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")

    if os.path.isdir(books_dir):
        for entry in os.listdir(books_dir):
            if not entry.lower().endswith(".pdf"):
                continue
            full_path = os.path.join(books_dir, entry)
            real_path = os.path.realpath(full_path)
            if not os.path.isfile(real_path):
                continue

            # Determine book ID
            match = uuid_pattern.search(os.path.basename(real_path))
            if match:
                b_id = match.group(1).lower()
            else:
                b_id = os.path.splitext(os.path.basename(real_path))[0]

            if b_id in discovered:
                continue

            title = catalog_map.get(b_id, b_id)
            discovered[b_id] = {
                "book_id": b_id,
                "pdf_path": real_path,
                "title": title,
            }

    # Also check any catalog books with local installations
    for b_id, b_title in catalog_map.items():
        if b_id not in discovered:
            local_path = manager.get_local_path(b_id)
            if local_path and os.path.isfile(local_path):
                discovered[b_id] = {
                    "book_id": b_id,
                    "pdf_path": os.path.realpath(local_path),
                    "title": b_title,
                }

    candidate_list = list(discovered.values())

    # Filter if --only was specified
    if only_ids:
        normalized_only = [x.strip().lower() for x in only_ids if x.strip()]
        filtered = []
        for cand in candidate_list:
            cid = cand["book_id"].lower()
            ctitle = cand["title"].lower()
            cpath = cand["pdf_path"].lower()
            if any(w in cid or w in ctitle or w in cpath for w in normalized_only):
                filtered.append(cand)
        return filtered

    return candidate_list


def main() -> int:
    parser = argparse.ArgumentParser(
        description="High-speed local activity hotspot scanner."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan all books found in books/ or kitap_pdf_linkleri.txt",
    )
    parser.add_argument(
        "--only",
        type=str,
        help="Comma-separated list of book IDs to scan",
    )
    parser.add_argument(
        "--pdf",
        type=str,
        help="Direct path to a PDF file to scan",
    )
    parser.add_argument(
        "pdf_files",
        nargs="*",
        help="Direct path(s) to PDF file(s) to scan",
    )
    default_workers = min(4, os.cpu_count() or 4)
    parser.add_argument(
        "--workers",
        type=int,
        default=default_workers,
        help=f"Number of parallel worker processes (default: {default_workers})",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=os.path.join(PROJECT_ROOT, "activities", "books"),
        help="Output directory or .json file for bakes (default: activities/books)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-scan even if the existing bake is up to date",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-page progress output",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Also write trace.json.gz: what each sheet refused, and under which rule",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=DEFAULT_PROFILE_PATH,
        help=(
            "Detector profile JSON (default: scanner/profile.json, or the "
            "built-in defaults when that file does not exist)"
        ),
    )

    args = parser.parse_args()

    # Loaded once, in the parent, so an unreadable or out-of-range profile is a
    # message before any work starts rather than twenty-seven identical worker
    # crashes.
    try:
        active_profile_obj = load_profile(args.profile)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Profile {args.profile}: {exc}", file=sys.stderr)
        return 2
    profile_dict = active_profile_obj.to_dict()
    active_hash = profile_hash(active_profile_obj)
    moved = changed_from_default(active_profile_obj)
    profile_note = " (defaults)" if not moved else f" ({len(moved)} knob(s) moved)"

    books_dir = os.path.join(PROJECT_ROOT, "books")
    meta_dir = os.path.join(PROJECT_ROOT, "activities_meta")
    catalog_path = os.path.join(PROJECT_ROOT, "kitap_pdf_linkleri.txt")

    only_ids = [x.strip() for x in args.only.split(",")] if args.only else None

    direct_pdfs = []
    if args.pdf:
        direct_pdfs.append(args.pdf)
    if args.pdf_files:
        direct_pdfs.extend(args.pdf_files)

    if direct_pdfs:
        uuid_pattern = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
        manager = BooksManager(base_dir=str(PROJECT_ROOT), edition="full")
        catalog_map = {b["id"]: b.get("title", b["id"]) for b in manager.books if b.get("id")}
        books = []
        for p in direct_pdfs:
            real_path = os.path.realpath(p)
            if not os.path.isfile(real_path):
                print(f"Error: PDF file not found: {p}", file=sys.stderr)
                return 1
            if only_ids and len(direct_pdfs) == 1:
                b_id = only_ids[0]
            else:
                m = uuid_pattern.search(os.path.basename(real_path))
                b_id = m.group(1).lower() if m else os.path.splitext(os.path.basename(real_path))[0]
            title = catalog_map.get(b_id, b_id)
            books.append({
                "book_id": b_id,
                "pdf_path": real_path,
                "title": title,
            })
    else:
        if not args.all and not only_ids:
            args.all = True

        books = discover_books(
            books_dir=books_dir,
            meta_dir=meta_dir,
            catalog_path=catalog_path,
            only_ids=only_ids,
            all_books=args.all,
        )

    if not books:
        print("No matching local books found to scan.")
        return 0

    out_dir = os.path.abspath(args.out)
    if out_dir.lower().endswith(".json"):
        os.makedirs(os.path.dirname(out_dir), exist_ok=True)
    else:
        os.makedirs(out_dir, exist_ok=True)

    print("=" * 72)
    print("INTERAKTIV ACTIVITY HOTSPOT SCANNER (PyMuPDF)")
    print(f"Target books:    {len(books)}")
    print(f"Worker count:    {args.workers}")
    print(f"Output dir:      {out_dir}")
    print(f"Force re-bake:   {args.force}")
    print("=" * 72)

    # Interleave large and small books so workers don't all concurrently process massive documents
    sorted_by_size = sorted(books, key=lambda b: os.path.getsize(b["pdf_path"]), reverse=True)
    interleaved: List[Dict[str, Any]] = []
    left, right = 0, len(sorted_by_size) - 1
    while left <= right:
        interleaved.append(sorted_by_size[left])
        left += 1
        if left <= right:
            interleaved.append(sorted_by_size[right])
            right -= 1
    books = interleaved

    tasks = [
        {
            "book_id": b["book_id"],
            "pdf_path": b["pdf_path"],
            "title": b["title"],
            "out_dir": out_dir,
            "meta_dir": meta_dir,
            "force": args.force,
            "quiet": args.quiet,
            "trace": args.trace,
            "profile": profile_dict,
        }
        for b in books
    ]

    # Start memory monitoring
    monitor = MemoryMonitor(sample_interval=0.04)
    monitor.start()

    wall_start = time.time()
    results: List[Dict[str, Any]] = []

    print(
        f"\nLaunching {len(tasks)} book(s) across {args.workers} worker process(es) "
        f"| profile {active_hash}{profile_note}\n"
    )

    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1) as executor:
        future_map = {executor.submit(scan_single_book, t): t for t in tasks}
        for future in as_completed(future_map):
            task = future_map[future]
            try:
                res = future.result()
            except Exception as e:
                res = {
                    "status": "error",
                    "book_id": task["book_id"],
                    "title": task["title"],
                    "error": str(e),
                }
            results.append(res)

            status = res.get("status", "unknown")
            b_id = res.get("book_id", "")[:8]
            title = res.get("title", "")[:32].ljust(32)

            if status == "baked":
                pages = res.get("page_count", 0)
                dur = res.get("duration", 0)
                pps = res.get("pages_per_sec", 0)
                regs = res.get("total_regions", 0)
                qns = res.get("total_questions", 0)
                rss = res.get("worker_rss_mb", 0)
                print(
                    f"  ✓ [{b_id}] {title} | {pages:3d} pgs in {dur:5.2f}s ({pps:5.1f} pg/s) | "
                    f"{regs:3d} acts, {qns:3d} qns | {rss:4.1f} MB RSS"
                )
            elif status == "skipped":
                dur = res.get("duration", 0)
                print(f"  - [{b_id}] {title} | Skipped (bake is current) [{dur:.2f}s]")
            else:
                err = res.get("error", "unknown error")
                print(f"  ✗ [{b_id}] {title} | FAILED: {err}")

    wall_elapsed = time.time() - wall_start
    mem_stats = monitor.stop()
    peak_workers_uss_mb = mem_stats["workers_uss"] / (1024 * 1024)
    peak_workers_rss_mb = mem_stats["workers_rss"] / (1024 * 1024)
    peak_total_uss_mb = mem_stats["total_uss"] / (1024 * 1024)
    peak_total_rss_mb = mem_stats["total_rss"] / (1024 * 1024)

    # Summary statistics
    total_pages = sum(r.get("page_count", 0) for r in results if r.get("status") == "baked")
    total_regions = sum(r.get("total_regions", 0) for r in results if r.get("status") == "baked")
    total_questions = sum(r.get("total_questions", 0) for r in results if r.get("status") == "baked")
    baked_count = sum(1 for r in results if r.get("status") == "baked")
    skipped_count = sum(1 for r in results if r.get("status") == "skipped")
    error_count = sum(1 for r in results if r.get("status") == "error")

    overall_pps = total_pages / max(0.001, wall_elapsed)

    print("\n" + "=" * 72)
    print("BATCH SCAN COMPLETE")
    print(f"Total time:             {wall_elapsed:.2f}s")
    print(f"Peak workers RAM (USS): {peak_workers_uss_mb:.1f} MB (Budget: 500 MB)")
    print(f"Peak workers RAM (RSS): {peak_workers_rss_mb:.1f} MB")
    print(f"Peak total RAM (USS):   {peak_total_uss_mb:.1f} MB")
    print(f"Peak total RAM (RSS):   {peak_total_rss_mb:.1f} MB")
    print(f"Pages processed:        {total_pages} pages ({overall_pps:.1f} pages/sec)")
    print(f"Yield:                  {total_regions} activities, {total_questions} sub-questions")
    print(f"Results:                {baked_count} baked, {skipped_count} skipped, {error_count} failed")
    print("=" * 72)

    return 1 if error_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
