#!/usr/bin/env python3
"""
The work queue: which rule empties the most sheets, and which sheets to look at.

A quarter to a half of the catalogue's sheets produce no hotspot -- 26.9% of the
6,185 sheets in the local corpus, 44.3% across all 56 books on the bakes as they
stand -- and until now that number had no breakdown. There was no way to tell a
sheet that asks nothing from one whose question was refused by a gate. `scan.py --trace` writes a `trace.json.gz`
recording every refusal; this reads them back and ranks the rules by how many
sheets each emptied.

That ranking is the same reasoning the scorecard's bucket histogram applies to
the publisher's manifest, extended to the sheets the manifest never mentions --
which is most of them. It says what to fix next, and it is deliberately blunt
about it: the biggest bucket is the next thing to work on.

It is also the sample source. For each rule the report names actual sheets and
quotes the text that was refused, so a bucket can be opened in the reader rather
than argued about in the abstract.

**A second ranking carries the supervision.** Most empty sheets are empty
because they should be -- a title page, a table of contents, the national
anthem, a page of running prose -- so the raw empty-sheet ranking is dominated
by correct behaviour. The sheets that prove a miss are the ones where every
hotspot is an `oge-` region synthesised from the publisher's icon: the manifest
says an activity is there, and the rules found nothing to bind it to. That set
is derived from the corpus and the publisher's own metadata, with nobody drawing
a box, which is the standing condition on supervision here.

Usage:
    python3 tools/hotspot_extraction/train/triage.py
    python3 tools/hotspot_extraction/train/triage.py --rule marker.out-of-run --samples 30
    python3 tools/hotspot_extraction/train/triage.py --json queue.json
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.hotspot_extraction.scanner.trace import (  # noqa: E402
    attribute_empty_page,
    load_trace_gz,
)

BOOKS_DIR = os.path.join(PROJECT_ROOT, "activities", "books")

# How many example sheets to keep per rule while walking. Kept small so the
# whole catalogue's traces never sit in memory at once: a book's trace is read,
# reduced, and dropped before the next is opened.
KEEP_SAMPLES = 8

# An activity synthesised from a publisher icon is named for the manifest entry
# it came from; `grow_activity_regions` names its own after the marker's label.
ANCHORED_ID_MARK = "-oge-"


def carried_by_anchor(page: Dict[str, Any]) -> bool:
    """
    Did this sheet yield hotspots, but only ones the publisher's icon placed?

    Such a sheet is a miss with a witness: the manifest asserts an activity is
    there and every rule declined to find it. It is the most valuable row in
    the queue, and the label costs nothing -- it falls out of the join that
    already exists.
    """
    regions = page.get("regions") or []
    return bool(regions) and all(ANCHORED_ID_MARK in r for r in regions)


def trace_paths(books_dir: str = BOOKS_DIR) -> List[Tuple[str, str]]:
    """Every book with a trace sidecar, as (book_id, path)."""
    if not os.path.isdir(books_dir):
        return []
    out = []
    for book_id in sorted(os.listdir(books_dir)):
        p = os.path.join(books_dir, book_id, "trace.json.gz")
        if os.path.isfile(p):
            out.append((book_id, p))
    return out


def collect(
    books_dir: str = BOOKS_DIR,
    samples: int = KEEP_SAMPLES,
    only_rule: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Walk every trace sidecar and reduce it to the queue.

    One book is held at a time and reduced before the next is opened, for the
    same reason every other sweep in this project works that way.
    """
    by_rule: Dict[str, int] = {}
    refusals: Dict[str, int] = {}
    by_book: Dict[str, Dict[str, Any]] = {}
    examples: Dict[str, List[Dict[str, Any]]] = {}
    anchor_only_by_rule: Dict[str, int] = {}
    anchor_only_examples: List[Dict[str, Any]] = []
    pages_total = 0
    empty_total = 0
    anchor_only_total = 0

    for book_id, path in trace_paths(books_dir):
        data = load_trace_gz(path)
        pages = data.get("pages", [])
        pages_total += len(pages)
        book_empty = 0
        book_rules: Dict[str, int] = {}

        for page in pages:
            for key, n in page.get("counts", {}).items():
                refusals[key] = refusals.get(key, 0) + n

            if carried_by_anchor(page):
                anchor_only_total += 1
                cause = attribute_empty_page(page)
                anchor_only_by_rule[cause] = anchor_only_by_rule.get(cause, 0) + 1
                if len(anchor_only_examples) < samples * 2:
                    anchor_only_examples.append({
                        "book": book_id[:8],
                        "page": page.get("page"),
                        "printedPage": page.get("printedPage"),
                        "regions": len(page.get("regions") or []),
                        "rule": cause,
                    })

            if page.get("regions"):
                continue
            book_empty += 1
            cause = attribute_empty_page(page)
            by_rule[cause] = by_rule.get(cause, 0) + 1
            book_rules[cause] = book_rules.get(cause, 0) + 1

            if only_rule and cause != only_rule:
                continue
            bucket = examples.setdefault(cause, [])
            if len(bucket) < samples:
                quoted = next(
                    (d.get("text") for d in page.get("drops", ())
                     if d.get("text") and f"{d['stage']}.{d['rule']}" == cause),
                    "",
                )
                bucket.append({
                    "book": book_id[:8],
                    "page": page.get("page"),
                    "printedPage": page.get("printedPage"),
                    "text": quoted,
                })

        empty_total += book_empty
        by_book[book_id] = {
            "pages": len(pages),
            "empty": book_empty,
            "emptyShare": round(book_empty / len(pages), 3) if pages else 0.0,
            "topRule": max(book_rules, key=lambda k: book_rules[k]) if book_rules else None,
        }
        del data, pages

    return {
        "books": len(by_book),
        "pages": pages_total,
        "emptyPages": empty_total,
        "emptyShare": round(empty_total / pages_total, 3) if pages_total else 0.0,
        "emptyPagesByRule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "anchorOnlyPages": anchor_only_total,
        "anchorOnlyByRule": dict(sorted(anchor_only_by_rule.items(), key=lambda kv: -kv[1])),
        "anchorOnlyExamples": anchor_only_examples,
        "refusalsByRule": dict(sorted(refusals.items(), key=lambda kv: -kv[1])),
        "examples": examples,
        "byBook": by_book,
    }


def report(queue: Dict[str, Any], only_rule: Optional[str] = None) -> str:
    lines: List[str] = []
    a = lines.append

    a("=" * 72)
    a("EMPTY-SHEET WORK QUEUE")
    a("=" * 72)
    a(f"Traces read:     {queue['books']} book(s), {queue['pages']} pages")
    a(f"Sheets with no hotspot: {queue['emptyPages']} "
      f"({queue['emptyShare'] * 100:.1f}%)")
    a("")
    a("Ranked by how many sheets each rule emptied. A sheet is attributed to the")
    a("rule that refused most on it, preferring a real gate over a rule that only")
    a("reports that nothing survived.")
    a("")
    a(f"  {'rule':<32} {'sheets':>7} {'share':>7}")
    a(f"  {'-' * 32} {'-' * 7} {'-' * 7}")
    total = max(1, queue["emptyPages"])
    for rule, n in queue["emptyPagesByRule"].items():
        a(f"  {rule:<32} {n:>7} {n / total * 100:>6.1f}%")

    a("")
    a("Sheets to look at" + (f" for {only_rule}" if only_rule else ", per rule") + ":")
    for rule, rows in queue["examples"].items():
        if only_rule and rule != only_rule:
            continue
        a(f"\n  {rule}")
        for r in rows:
            printed = f"p.{r['printedPage']}" if r["printedPage"] is not None else "p.?"
            text = f"  {r['text'][:52]!r}" if r["text"] else ""
            a(f"    {r['book']}  sheet {r['page']:>4}  {printed:<7}{text}")

    a("")
    a("-" * 72)
    a("SHEETS THE PUBLISHER CARRIED ALONE")
    a("-" * 72)
    a(f"{queue['anchorOnlyPages']} sheet(s) yielded hotspots, but every one of them was")
    a("synthesised from a publisher icon -- the manifest says an activity is there and")
    a("no rule found it. This is the free supervision: a miss with a witness.")
    a("")
    a(f"  {'rule that refused most':<32} {'sheets':>7}")
    a(f"  {'-' * 32} {'-' * 7}")
    for rule, n in queue["anchorOnlyByRule"].items():
        a(f"  {rule:<32} {n:>7}")
    if queue["anchorOnlyExamples"]:
        a("")
        for r in queue["anchorOnlyExamples"]:
            printed = f"p.{r['printedPage']}" if r["printedPage"] is not None else "p.?"
            a(f"    {r['book']}  sheet {r['page']:>4}  {printed:<7} "
              f"{r['regions']} anchored  ({r['rule']})")

    a("")
    a("Books by share of empty sheets:")
    worst = sorted(queue["byBook"].items(), key=lambda kv: -kv[1]["emptyShare"])
    for book_id, b in worst[:15]:
        a(f"  {book_id[:8]}  {b['empty']:>4}/{b['pages']:<4} "
          f"{b['emptyShare'] * 100:>5.1f}%   {b['topRule'] or ''}")
    a("=" * 72)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--books-dir", default=BOOKS_DIR)
    ap.add_argument("--rule", help="show samples for this rule only, e.g. marker.out-of-run")
    ap.add_argument("--samples", type=int, default=KEEP_SAMPLES,
                    help=f"example sheets to keep per rule (default: {KEEP_SAMPLES})")
    ap.add_argument("--json", metavar="PATH", help="also write the queue as JSON")
    args = ap.parse_args()

    paths = trace_paths(args.books_dir)
    if not paths:
        print("No trace sidecars found. Bake with --trace first:")
        print("    python3 tools/hotspot_extraction/scan.py --all --trace --workers 2")
        return 1

    queue = collect(args.books_dir, samples=args.samples, only_rule=args.rule)
    print(report(queue, only_rule=args.rule))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
