#!/usr/bin/env python3
"""
The primitives cache: parse each book once, replay the detector a thousand times.

Fitting the detector's constants means running detection over the corpus once
per candidate parameter set. Measured on four installed books, the split is
lopsided and in our favour:

| Book                        | parse      | replay        | replay share |
|-----------------------------|------------|---------------|--------------|
| `09f62a7e` (biology, 193pp) | 49.6 ms/pg | **4.24 ms/pg**| 7.9%         |
| `0e966773` (ELT WB, 165pp)  | 23.4 ms/pg | **3.22 ms/pg**| 12.1%        |
| `51cdbbce` (maths, 417pp)   | 23.1 ms/pg | **4.62 ms/pg**| 16.7%        |
| `d13a75d3` (524 MB, 319pp)  | 17.8 ms/pg | **17.68 ms/pg**| 49.8%       |

"Parse" is `extract_page_primitives`; "replay" is everything a parameter can
change -- `detect_layout`, `detect_activity_markers`, `grow_activity_regions`
and anchor reconciliation. So the PDF is re-read on every evaluation for
nothing, and caching what the parse produced removes between half and
nine-tenths of the cost. It also means 1.86 GB of PDFs never has to be reopened
during a fitting run.

**The cache sits below layout, markers and growth, which is the point.** Cache
the output of a later stage and the constants of that stage stop being
trainable; caching the primitives leaves all ~70 of them free.

Three properties, each a standing rule of this project rather than a preference:

  * **One short-lived child per book.** A sweep that keeps every book in one
    process ends up holding all of them resident, which once took this machine
    into swap. Each shard is built in a child that exits before the next starts
    (`max_tasks_per_child=1`), and the allocator is trimmed between pages.
  * **No heap caps.** A cap is a licence to use that much. The budget is
    observed and reported, not reserved.
  * **Keyed by the book's fingerprint.** `compute_fingerprint` is what the bake
    already records, so a re-exported PDF invalidates its shard automatically
    rather than quietly training on stale geometry.

Usage:
    python3 tools/hotspot_extraction/train/cache.py --all
    python3 tools/hotspot_extraction/train/cache.py --only 550e601a --force
    python3 tools/hotspot_extraction/train/cache.py --all --verify
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
import os
import pickle
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import psutil  # noqa: E402
import pymupdf  # noqa: E402

from books_manager import BooksManager  # noqa: E402
from tools.hotspot_extraction.scan import _trim_memory  # noqa: E402
from tools.hotspot_extraction.scanner import (  # noqa: E402
    PagePrimitives,
    PublisherOge,
    compute_book_folio,
    compute_fingerprint,
    extract_page_primitives,
    load_publisher_oges,
)

CACHE_VERSION = 1

# Derived data, rebuilt from the PDFs on demand and never shipped, so it lives
# outside `activities/` -- that tree is the bake the reader loads.
DEFAULT_CACHE_DIR = os.path.join(PROJECT_ROOT, ".cache", "primitives")

# Pickle protocol 5 writes the many small float tuples these dataclasses are
# made of compactly, and it is in every Python this project supports.
PICKLE_PROTOCOL = 5


@dataclass
class PageShard:
    """One sheet, in the form replay needs it."""
    page_num: int
    primitives: PagePrimitives
    printed_page: Optional[int]
    oges: List[PublisherOge] = field(default_factory=list)


@dataclass
class BookShard:
    """
    One book's parse, and the publisher metadata joined to it.

    The folio map is stored with it rather than recomputed, for the reason the
    bake precomputes it in the first place: it is voted from the sheets that
    were actually read, so computing it from a subset is not the same answer.
    """
    version: int
    book_id: str
    title: str
    fingerprint: str
    page_count: int
    folio_offset: Optional[int]
    folio_map: Dict[int, int]
    pages: List[PageShard]

    def page(self, page_num: int) -> Optional[PageShard]:
        for p in self.pages:
            if p.page_num == page_num:
                return p
        return None


def shard_path(book_id: str, cache_dir: str = DEFAULT_CACHE_DIR) -> str:
    return os.path.join(cache_dir, f"{book_id}.pkl")


def shard_fingerprint(path: str) -> Optional[str]:
    """
    What bytes a shard on disk was made from, without unpickling the whole of it.

    A 20 MB shard is expensive to load only to find it is stale, so the header
    is written first and read on its own: the pickle stream holds a small dict
    followed by the `BookShard`, and `Unpickler.load()` stops after the first.
    """
    try:
        with open(path, "rb") as f:
            head = pickle.Unpickler(f).load()
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError):
        return None
    if not isinstance(head, dict) or head.get("version") != CACHE_VERSION:
        return None
    return head.get("fingerprint")


def shard_header(path: str) -> Optional[Dict[str, Any]]:
    """
    The shard's header -- book id, fingerprint, page count -- without the pages.

    The fitting driver needs the page count of every book to divide the corpus
    into chunks, and unpickling 113 MB in the parent to learn twenty-seven
    integers would defeat the point of running the work in children.
    """
    try:
        with open(path, "rb") as f:
            head = pickle.Unpickler(f).load()
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError):
        return None
    if not isinstance(head, dict) or head.get("version") != CACHE_VERSION:
        return None
    return head


def save_shard(path: str, shard: BookShard) -> int:
    """Write a shard, header first. Returns bytes written."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        pickle.dump(
            {"version": CACHE_VERSION, "bookId": shard.book_id,
             "fingerprint": shard.fingerprint, "pageCount": shard.page_count},
            f, protocol=PICKLE_PROTOCOL,
        )
        pickle.dump(shard, f, protocol=PICKLE_PROTOCOL)
    os.replace(tmp, path)
    return os.path.getsize(path)


def load_shard(path: str) -> BookShard:
    """
    Read a shard back whole.

    Two separate `pickle.load` calls, not one `Unpickler` used twice: from
    protocol 4 on the stream is framed, and a reused unpickler reads past the
    end of the first object into the frame holding the second, so the second
    load then starts mid-frame and fails with `STACK_GLOBAL requires str`.
    """
    with open(path, "rb") as f:
        pickle.load(f)                      # the header, already checked
        return pickle.load(f)


def build_shard(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse one book into a shard. Runs in a child of its own and exits after.

    Nothing here runs detection: what is cached is strictly what the PDF states,
    so that every stage above it stays trainable.
    """
    pymupdf.TOOLS.set_low_memory(True)

    book_id = task["book_id"]
    pdf_path = task["pdf_path"]
    cache_dir = task["cache_dir"]
    meta_dir = task["meta_dir"]
    title = task.get("title", book_id)
    force = task.get("force", False)

    t0 = time.time()
    path = shard_path(book_id, cache_dir)
    try:
        fp = compute_fingerprint(pdf_path)
    except Exception as e:                  # noqa: BLE001 - reported, not raised
        return {"status": "error", "book_id": book_id, "title": title,
                "error": f"fingerprint failed: {e}"}

    if not force and shard_fingerprint(path) == fp:
        return {"status": "current", "book_id": book_id, "title": title,
                "bytes": os.path.getsize(path),
                "duration": round(time.time() - t0, 3)}

    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:                  # noqa: BLE001
        return {"status": "error", "book_id": book_id, "title": title,
                "error": f"open failed: {e}"}

    try:
        page_count = doc.page_count
        folio_offset, folio_map = compute_book_folio(doc)

        meta_path = os.path.join(meta_dir, f"{book_id}.json")
        oges = load_publisher_oges(meta_path) if os.path.isfile(meta_path) else []
        by_printed: Dict[int, List[PublisherOge]] = {}
        for o in oges:
            by_printed.setdefault(o.sayfano, []).append(o)

        pages: List[PageShard] = []
        for p in range(1, page_count + 1):
            prim = extract_page_primitives(doc, p)
            printed = folio_map.get(p)
            pages.append(PageShard(
                page_num=p,
                primitives=prim,
                printed_page=printed,
                oges=list(by_printed.get(printed, ())) if printed is not None else [],
            ))
            if p % 32 == 0:
                _trim_memory()

        shard = BookShard(
            version=CACHE_VERSION,
            book_id=book_id,
            title=title,
            fingerprint=fp,
            page_count=page_count,
            folio_offset=folio_offset,
            folio_map=folio_map,
            pages=pages,
        )
        written = save_shard(path, shard)
    except Exception as e:                  # noqa: BLE001
        doc.close()
        return {"status": "error", "book_id": book_id, "title": title, "error": str(e)}
    finally:
        doc.close()

    del shard, pages
    _trim_memory()
    try:
        rss = psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        rss = 0.0

    return {
        "status": "built",
        "book_id": book_id,
        "title": title,
        "pages": page_count,
        "bytes": written,
        "bytes_per_page": round(written / max(1, page_count)),
        "duration": round(time.time() - t0, 3),
        "worker_rss_mb": round(rss, 1),
    }


# ------------------------------------------------------------------ replay


def replay_page(shard_page: PageShard, *, reconcile: bool = True):
    """
    Run detection on one cached sheet, exactly as `scan.py` runs it on a live one.

    "Exactly" is now literal: both call `scanner.pipeline.detect_page`, so the
    sequence cannot drift between the bake and the replay. That is the whole
    reason the cache exists, and it is also how the cache is verified -- a shard
    is only usable if replaying it produces the regions the PDF produces.
    Anything the detector needs that is not reachable from here is a hole in the
    shard, and `--verify` will find it.

    Returns the full `PageResult`, drawn geometry included, because the fitting
    loop scores regions against the panels and solution blocks of the same page
    and rebuilding them separately is how the two would come apart.
    """
    from tools.hotspot_extraction.scanner.pipeline import detect_page

    return detect_page(
        shard_page.primitives,
        page_num=shard_page.page_num,
        printed_page=shard_page.printed_page,
        page_oges=shard_page.oges,
        reconcile=reconcile,
    )


def verify_shard(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Replay a shard against its own PDF and report the first sheet that differs.

    Equality is on the serialized activity -- id, rect and parts -- because that
    is what the bake writes and what the scorer reads. A shard that replays to
    the same regions is a drop-in stand-in for the PDF; one that does not is
    worse than no cache at all, because a fitting run on it would optimise
    against geometry the shipped detector never sees.
    """
    pymupdf.TOOLS.set_low_memory(True)

    from tools.hotspot_extraction.scanner import serialize_activity

    book_id = task["book_id"]
    pdf_path = task["pdf_path"]
    cache_dir = task["cache_dir"]
    title = task.get("title", book_id)
    limit = task.get("limit")

    path = shard_path(book_id, cache_dir)
    if not os.path.isfile(path):
        return {"status": "error", "book_id": book_id, "title": title,
                "error": "no shard"}

    shard = load_shard(path)
    doc = pymupdf.open(pdf_path)
    checked = 0
    try:
        pages = shard.pages if not limit else shard.pages[:limit]
        for sp in pages:
            live = extract_page_primitives(doc, sp.page_num)
            cached_out = [serialize_activity(a) for a in replay_page(sp).activities]

            live_page = PageShard(
                page_num=sp.page_num,
                primitives=live,
                printed_page=sp.printed_page,
                oges=sp.oges,
            )
            live_out = [serialize_activity(a) for a in replay_page(live_page).activities]
            checked += 1
            if cached_out != live_out:
                return {
                    "status": "mismatch", "book_id": book_id, "title": title,
                    "page": sp.page_num, "checked": checked,
                    "cached": len(cached_out), "live": len(live_out),
                }
            del live, live_page, cached_out, live_out
            if sp.page_num % 32 == 0:
                _trim_memory()
    finally:
        doc.close()

    return {"status": "verified", "book_id": book_id, "title": title,
            "checked": checked}


# -------------------------------------------------------------------- walk


def cached_books(cache_dir: str = DEFAULT_CACHE_DIR) -> List[str]:
    """The book ids that have a shard on disk."""
    if not os.path.isdir(cache_dir):
        return []
    return sorted(
        f[:-4] for f in os.listdir(cache_dir) if f.endswith(".pkl")
    )


def local_books(only: Optional[Sequence[str]] = None) -> List[Dict[str, str]]:
    """
    Every catalogue book whose PDF is on this machine, newest parse first.

    The cache can only hold books that are actually here; the other 30 are
    scored from their bakes and are not part of a fitting run.
    """
    manager = BooksManager(base_dir=PROJECT_ROOT, edition="full")
    out: Dict[str, Dict[str, str]] = {}
    for b in manager.books:
        book_id = b.get("id")
        if not book_id or book_id in out:
            continue
        local = manager.get_local_path(book_id)
        if not local or not os.path.isfile(local):
            continue
        if only and not any(w.lower() in book_id.lower() or w.lower() in b.get("title", "").lower()
                            for w in only):
            continue
        out[book_id] = {
            "book_id": book_id,
            "pdf_path": os.path.realpath(local),
            "title": b.get("title", book_id),
        }
    return list(out.values())


def run(args) -> int:
    books = local_books(args.only)
    if not books:
        print("No local PDFs match.")
        return 0

    cache_dir = os.path.abspath(args.cache_dir)
    meta_dir = os.path.join(PROJECT_ROOT, "activities_meta")

    verb = "Verifying" if args.verify else "Building"
    print("=" * 72)
    print(f"PRIMITIVES CACHE -- {verb} {len(books)} shard(s)")
    print(f"Cache dir:    {cache_dir}")
    print(f"Workers:      {args.workers}  (one short-lived child per book)")
    print("=" * 72 + "\n")

    fn = verify_shard if args.verify else build_shard
    tasks = [
        {
            "book_id": b["book_id"],
            "pdf_path": b["pdf_path"],
            "title": b["title"],
            "cache_dir": cache_dir,
            "meta_dir": meta_dir,
            "force": args.force,
            "limit": args.limit,
        }
        for b in books
    ]

    t0 = time.time()
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1) as ex:
        futures = {ex.submit(fn, t): t for t in tasks}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                res = fut.result()
            except Exception as e:          # noqa: BLE001
                res = {"status": "error", "book_id": t["book_id"],
                       "title": t["title"], "error": str(e)}
            results.append(res)

            mark = {"built": "✓", "current": "=", "verified": "✓",
                    "mismatch": "✗", "error": "✗"}.get(res["status"], "?")
            line = f"  {mark} [{res['book_id'][:8]}] {res['title'][:34]:<34}"
            if res["status"] == "built":
                line += (f" {res['pages']:>4} pgs  {res['bytes'] / 1e6:6.1f} MB"
                         f"  {res['bytes_per_page']:>6} B/pg  {res['duration']:6.1f}s"
                         f"  {res['worker_rss_mb']:6.1f} MB RSS")
            elif res["status"] == "current":
                line += f" shard current ({res['bytes'] / 1e6:.1f} MB)"
            elif res["status"] == "verified":
                line += f" {res['checked']} page(s) replay identical"
            elif res["status"] == "mismatch":
                line += (f" page {res['page']}: {res['cached']} cached vs"
                         f" {res['live']} live regions")
            else:
                line += f" {res.get('error', '')}"
            print(line, flush=True)

    took = time.time() - t0
    built = [r for r in results if r["status"] == "built"]
    bad = [r for r in results if r["status"] in ("error", "mismatch")]

    print("\n" + "=" * 72)
    if built:
        total_bytes = sum(r["bytes"] for r in built)
        total_pages = sum(r["pages"] for r in built)
        print(f"Built:        {len(built)} shard(s), {total_pages} pages, "
              f"{total_bytes / 1e6:.1f} MB "
              f"({round(total_bytes / max(1, total_pages))} B/page)")
    on_disk = cached_books(cache_dir)
    size = sum(os.path.getsize(shard_path(b, cache_dir)) for b in on_disk)
    print(f"Cache holds:  {len(on_disk)} shard(s), {size / 1e6:.1f} MB")
    print(f"Wall clock:   {took:.1f}s")
    if bad:
        print(f"\n{len(bad)} book(s) failed: "
              + ", ".join(f"{r['book_id'][:8]} ({r['status']})" for r in bad))
    print("=" * 72)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--all", action="store_true",
                    help="every catalogue book whose PDF is on this machine (default)")
    ap.add_argument("--only", nargs="+", metavar="MATCH",
                    help="only books whose id or title contains one of these")
    ap.add_argument("--force", action="store_true",
                    help="rebuild a shard even when its fingerprint is current")
    ap.add_argument("--verify", action="store_true",
                    help="replay each shard against its PDF and report any difference")
    ap.add_argument("--limit", type=int,
                    help="with --verify, check only the first N pages of each book")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    default_workers = min(2, os.cpu_count() or 1)
    ap.add_argument("--workers", type=int, default=default_workers,
                    help=f"parallel children, bounded by RAM not cores (default: {default_workers})")
    return run(ap.parse_args())


if __name__ == "__main__":
    # Imported under its package name before running, so that `PageShard` and
    # `BookShard` are pickled as `tools.hotspot_extraction.train.cache.*` rather
    # than as `__main__.*`. A shard written by the script has to be readable by
    # anything that imports this module -- the fitting loop, the tests -- and a
    # `__main__`-qualified class is not.
    from tools.hotspot_extraction.train.cache import main as _main

    sys.exit(_main())
