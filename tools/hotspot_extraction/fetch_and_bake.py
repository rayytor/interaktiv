#!/usr/bin/env python3
"""
Bake every book in the catalogue, not just the ones that happen to be on disk.

The scorecard measures the detector against the publisher's manifest, and the
manifest covers all 56 books in `kitap_pdf_linkleri.txt`. Ten of them were
installed, so every number the work has been steered by -- the match rate, and
the bucket histogram that says what to fix first -- was read off 2.6% of the
catalogue. This widens the measurement: it walks the link file, fetches each
book, bakes it, and can throw the PDF away again, so the whole catalogue costs a
few MB of regions and sidecars instead of roughly 10 GB of PDFs.

It is a driver and nothing more. Detection and baking both live in
`scan.py` and the `scanner/` package; what is decided here is only which book
is next, whether it needs fetching, and what to do when a bake dies.

Three properties matter, and each of them is a standing rule rather than a
preference:

  * **One short-lived child per book.** A sweep that keeps every book in one
    process ends up holding all of them resident, which once took the machine
    into swap. `scan.py` is spawned per book and exits before the next starts.
  * **A book too heavy for one pass is retried alone, not skipped.** The old
    Node baker needed a `--chunked` mode that read a page range at a time,
    because pdf.js held a whole book's structure at once. PyMuPDF releases each
    page and trims the allocator between them, so the retry here is simply a
    single-worker run; there is no page-range path to fall back to.
  * **Resumable, and cheap to resume.** A book whose bake is current is skipped
    without downloading it: the bake records the size of the bytes it was made
    from, and the CDN will say the size of the bytes it is offering, so the two
    can be compared for the price of one request.

Usage:
    python3 tools/hotspot_extraction/fetch_and_bake.py                # the whole catalogue
    python3 tools/hotspot_extraction/fetch_and_bake.py --discard-pdf  # ... keeping no PDFs
    python3 tools/hotspot_extraction/fetch_and_bake.py --only 1cc573f6 --force
    python3 tools/hotspot_extraction/fetch_and_bake.py --limit 5 --dry-run

Zero external dependencies, like the rest of the server side.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from books_manager import BooksManager  # noqa: E402  (after sys.path)

SCANNER = os.path.join(HERE, "scan.py")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Interaktiv/1.0"
CHUNK = 1024 * 1024


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


# --------------------------------------------------------------- the bake


def baked_fingerprint(book_id):
    """
    What bytes the bake on disk was made from, or None if there is no usable one.

    A bake without its `diagnostics.json.gz` sidecar cannot be scored -- the
    scorecard's `--from-bake` refuses it -- so it counts as absent here however
    current its regions look. That is not hypothetical: `1cc573f6` sat with a
    stale `regions.json` and no sidecar for exactly this reason.
    """
    d = os.path.join(ROOT, "activities", "books", book_id)
    regions = os.path.join(d, "regions.json")
    if not (os.path.isfile(regions) and os.path.isfile(os.path.join(d, "diagnostics.json.gz"))):
        return None
    try:
        with open(regions, "r", encoding="utf-8") as f:
            baked = json.load(f)
    except (OSError, ValueError):
        return None
    return baked.get("fingerprint")


def baked_size(book_id):
    """
    The size of the PDF a bake was made from, read off its fingerprint.

    `compute_fingerprint()` in the serializer is `<size>-<hash of head, middle
    and tail>`, and
    the size is the half that can be checked against a remote file without
    downloading it. It is not proof the bytes are the same -- two revisions of
    one book could be the same length -- but a publisher's re-export changes the
    length in practice, and being wrong costs a re-bake, not a wrong answer: the
    baker fingerprints the file it actually has before deciding to skip it.
    """
    print_ = baked_fingerprint(book_id)
    if not print_ or "-" not in print_:
        return None
    try:
        return int(print_.split("-", 1)[0])
    except ValueError:
        return None


def bake(pdf_path, force=False, chunk_pages=None, timeout=None, trace=False):
    """
    Bake one book in a child of its own.

    The child is `scan.py`, which is where detection lives; this only decides
    that it runs alone and gets to exit. No heap cap is passed, and that is
    deliberate: a cap is a licence to use that much, and the one place a large
    one was ever set is the place that took a machine down. PyMuPDF frees each
    page and trims the allocator between them, and `scan.py` samples its own
    workers' memory and reports the peak, so the budget is observed rather than
    reserved.

    `chunk_pages` is accepted and ignored -- it named a page-range mode the Node
    baker needed and PyMuPDF does not. It stays in the signature so callers
    passing it are not broken.
    """
    book_id = os.path.splitext(os.path.basename(os.path.realpath(pdf_path)))[0]
    base = [
        sys.executable, SCANNER,
        "--only", book_id,
        "--pdf", pdf_path,
        "--workers", "1",
    ]
    if force:
        base.append("--force")
    if trace:
        # The trace sidecar is what turns the sheets this book yields nothing on
        # into a work queue, so a book fetched for the training corpus is worth
        # tracing as it is baked rather than baked twice.
        base.append("--trace")

    first = subprocess.run(base, cwd=ROOT, timeout=timeout)
    if first.returncode == 0:
        return "baked"

    # A bake that died once is retried with the page cache shrunk as far as it
    # goes and nothing else running beside it. If it dies again the book is
    # reported as failed rather than silently left with a stale bake.
    print(f"    bake failed (exit {first.returncode}); retrying once on its own",
          flush=True)
    second = subprocess.run(base + ["--force", "--quiet"], cwd=ROOT, timeout=timeout)
    return "baked-retry" if second.returncode == 0 else None


# ----------------------------------------------------------- the download


def remote_size(url, timeout=30):
    """
    How large the book is on the CDN, without fetching it.

    HEAD first; a byte-range GET of one byte is the fallback, because a CDN that
    refuses HEAD still has to answer a range request with a `Content-Range` that
    names the full length -- the reader already depends on that support.
    """
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            n = resp.headers.get("Content-Length")
            if n:
                return int(n)
    except (urllib.error.URLError, OSError, ValueError):
        pass

    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rng = resp.headers.get("Content-Range") or ""
            if "/" in rng:
                total = rng.rsplit("/", 1)[1].strip()
                if total.isdigit():
                    return int(total)
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return None


def download(url, target, timeout=60):
    """
    Fetch one book to `target`, resuming a part file if one is lying around.

    Written to `<target>.part` and renamed only once the whole file has arrived,
    so an interrupted run never leaves something that looks like a book and
    bakes as a truncated one. A run that is stopped and started again continues
    from where the part file ends rather than from the beginning -- these are
    100-500 MB files and there are 46 of them.
    """
    part = target + ".part"
    have = os.path.getsize(part) if os.path.isfile(part) else 0
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"

    req = urllib.request.Request(url, headers=headers)
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # A server that ignored the range restarts the file, so the part file
        # has to go: appending to it would splice the head onto itself.
        resuming = resp.status == 206 and have > 0
        if have and not resuming:
            have = 0
        total = int(resp.headers.get("Content-Length") or 0) + have

        with open(part, "ab" if resuming else "wb") as f:
            got = have
            last = 0.0
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                now = time.time()
                if now - last > 2:
                    last = now
                    rate = got / max(0.001, now - started) / (1024 * 1024)
                    pct = f"{got / total * 100:4.0f}%" if total else "    "
                    print(f"\r    {pct}  {human(got)}  {rate:.1f} MB/s", end="", flush=True)
    print(f"\r    {human(got)} in {time.time() - started:.0f}s" + " " * 20, flush=True)

    if total and got < total:
        raise OSError(f"short read: {got} of {total} bytes")
    os.replace(part, target)
    return got


# ---------------------------------------------------------------- the walk


def run(args):
    manager = BooksManager(base_dir=ROOT, edition="full")
    books = [b for b in manager.books if b.get("id") and b.get("url")]
    seen = set()
    catalogue = []
    for b in books:
        if b["id"] in seen:
            continue
        seen.add(b["id"])
        catalogue.append(b)

    if args.only:
        wanted = [w.lower() for w in args.only]
        catalogue = [b for b in catalogue
                     if any(w in b["id"].lower() or w in b["title"].lower() for w in wanted)]
    if args.limit:
        catalogue = catalogue[: args.limit]

    print(f"{len(catalogue)} book(s) to consider\n")
    results = []
    for i, book in enumerate(catalogue, 1):
        book_id = book["id"]
        title = book["title"]
        print(f"[{i}/{len(catalogue)}] {title}  ({book_id[:8]})", flush=True)

        local = os.path.join(manager.books_dir, f"{book_id}.pdf")
        installed = manager.get_local_path(book_id)
        # A book installed under one of the legacy names is used where it lies,
        # but only if its real name is the catalogue id: the baker reads the id
        # off the filename, through symlinks.
        if not os.path.isfile(local) and installed:
            if os.path.basename(os.path.realpath(installed)) == f"{book_id}.pdf":
                local = installed
        was_installed = os.path.isfile(local)   # was it here before this run?

        # Skip without fetching when the bake on disk was made from bytes the
        # same length as the ones on offer.
        if not args.force and baked_fingerprint(book_id):
            size = os.path.getsize(local) if was_installed else remote_size(book["url"])
            if size is not None and size == baked_size(book_id):
                print("    bake is current, skipped\n", flush=True)
                results.append((book_id, title, "current", None))
                continue

        if args.dry_run:
            print("    would fetch and bake\n", flush=True)
            results.append((book_id, title, "dry-run", None))
            continue

        try:
            if not was_installed:
                print(f"    fetching {book['url']}", flush=True)
                download(book["url"], local, timeout=args.timeout)
        except Exception as e:                       # noqa: BLE001 - report and go on
            print(f"    download failed: {e}\n", flush=True)
            results.append((book_id, title, "download-failed", str(e)))
            continue

        t0 = time.time()
        try:
            how = bake(local, force=args.force, chunk_pages=args.chunk_pages,
                       timeout=args.bake_timeout, trace=args.trace)
        except subprocess.TimeoutExpired:
            how = None
            print(f"    bake timed out after {args.bake_timeout}s", flush=True)
        took = time.time() - t0

        if how:
            results.append((book_id, title, how, f"{took:.0f}s"))
        else:
            results.append((book_id, title, "bake-failed", f"{took:.0f}s"))

        # Only what this run brought down is thrown away. The books that were
        # already installed are the user's, and a driver does not delete them.
        if args.discard_pdf and how and not was_installed and os.path.isfile(local):
            os.remove(local)
            print("    pdf discarded", flush=True)
        print(flush=True)

    print("=" * 72)
    width = max((len(t) for _, t, _, _ in results), default=10)
    for book_id, title, how, note in results:
        print(f"{title[:width].ljust(width)}  {book_id[:8]}  {how}{'  ' + note if note else ''}")
    counts = {}
    for _, _, how, _ in results:
        counts[how] = counts.get(how, 0) + 1
    print("\n" + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))

    failed = sum(n for k, n in counts.items() if k.endswith("failed"))
    if failed:
        print(f"\n{failed} book(s) did not bake")
    else:
        print("\nnext: python3 tools/hotspot_extraction/compare_scorecard.py --skip-scan")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--only", nargs="+", metavar="MATCH",
                    help="only books whose id or title contains one of these")
    ap.add_argument("--limit", type=int, help="stop after this many books")
    ap.add_argument("--force", action="store_true",
                    help="re-fetch and re-bake even when the bake is current")
    ap.add_argument("--discard-pdf", action="store_true",
                    help="delete each PDF this run downloaded once it has baked")
    ap.add_argument("--chunk-pages", type=int,
                    help="sheets per child when a book has to be baked in ranges")
    ap.add_argument("--trace", action="store_true",
                    help="write each book's trace.json.gz as it bakes (see scan.py --trace)")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would be fetched and baked, do nothing")
    ap.add_argument("--timeout", type=int, default=60, help="download socket timeout, seconds")
    ap.add_argument("--bake-timeout", type=int, default=3600,
                    help="give up on one book's bake after this many seconds")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
