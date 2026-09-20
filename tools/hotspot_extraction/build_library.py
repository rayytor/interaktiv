#!/usr/bin/env python3
"""
Batch package textbooks into a self-contained school library.

Iterates over installed textbooks (or a specified subset) and builds their
library bundles using `converter/package_book.py`, updating `library/manifest.json`.

Usage:
  python3 converter/build_library.py --all [options]
  python3 converter/build_library.py --only <id1,id2,...> [options]

Options:
  --out DIR           Library directory to write into (default: ./library)
  --force             Re-bake even if a current bake exists
  --with-activities   Copy interactive activities into the bundle (default: fetch on demand)
  --no-thumbnail      Do not generate or copy covers
  --quiet             Only print warnings and errors
"""
import argparse
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from package_book import package_book, write_manifest, get_catalog, find_source_pdf  # noqa: E402


def build_library(book_ids, out_dir=None, force=False, with_activities=False,
                  thumbnail=True, quiet=False):
    """
    Package multiple books into the library and update manifest.json.
    """
    out_dir = os.path.abspath(out_dir or os.path.join(BASE_DIR, "library"))
    os.makedirs(out_dir, exist_ok=True)

    total = len(book_ids)
    success = []
    failed = []

    if not quiet:
        print(f"Packaging {total} book(s) into {os.path.relpath(out_dir, BASE_DIR)}...")

    for i, bid in enumerate(book_ids, 1):
        if not quiet:
            print(f"[{i}/{total}] Packaging {bid}...")
        try:
            entry = package_book(
                bid,
                out_dir=out_dir,
                force=force,
                with_activities=with_activities,
                thumbnail=thumbnail,
                quiet=quiet,
            )
            write_manifest(out_dir, entry, quiet=True)
            success.append(entry)
        except Exception as e:
            print(f"error packaging {bid}: {e}", file=sys.stderr)
            failed.append((bid, str(e)))

    if not quiet:
        print(f"\nDone: {len(success)} packaged successfully, {len(failed)} failed.")
        print(f"Library manifest: {os.path.join(out_dir, 'manifest.json')}")

    return success, failed


def main():
    parser = argparse.ArgumentParser(
        description="Batch package textbooks into a self-contained school library."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all",
        action="store_true",
        help="Package all installed books found in the local catalog",
    )
    group.add_argument(
        "--only",
        type=str,
        help="Comma-separated list of book IDs to package",
    )
    parser.add_argument(
        "--out",
        default=os.path.join(BASE_DIR, "library"),
        help="Library directory to write into (default: ./library)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-bake even if a current bake exists",
    )
    parser.add_argument(
        "--with-activities",
        action="store_true",
        help="Copy interactive activities into the bundle (default: fetch on demand)",
    )
    parser.add_argument(
        "--no-thumbnail",
        action="store_true",
        help="Do not generate or copy covers",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print warnings and errors",
    )

    args = parser.parse_args()

    catalog = get_catalog()
    if args.all:
        installed = []
        for bid in catalog:
            try:
                find_source_pdf(bid)
                installed.append(bid)
            except FileNotFoundError:
                pass
        if not installed:
            print("No installed books found to package.", file=sys.stderr)
            return 1
        book_ids = installed
    else:
        book_ids = [b.strip() for b in args.only.split(",") if b.strip()]
        if not book_ids:
            print("No book IDs specified.", file=sys.stderr)
            return 1

    success, failed = build_library(
        book_ids,
        out_dir=args.out,
        force=args.force,
        with_activities=args.with_activities,
        thumbnail=not args.no_thumbnail,
        quiet=args.quiet,
    )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
