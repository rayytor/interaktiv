#!/usr/bin/env python3
"""
Which books the detector may be fitted on, and which it may not be scored against.

The checked-in benchmark report claimed 79.4% when the catalogue-wide number was
67.2%, and the whole of that 12-point overstatement came from one mistake:
`compare_scorecard.py` graded only the ten books whose PDFs happened to be on
disk, which are the same ten the constants had been hand-tuned against. Fitting
~70 parameters against a corpus will reproduce that failure at a far larger
scale unless a part of the corpus is fenced off before fitting starts.

So the split is a committed file, not a runtime choice:

  * **Held out is roughly a third of the books**, and the fitting loop may never
    read them -- not to score a candidate, not to break a tie, not to pick a
    stopping point. `assert_trainable()` is what enforces it.
  * **Both templates on both sides.** These books come in two families -- the
    ELT coursebooks and workbooks, set in English with a regular exercise
    apparatus, and the Turkish subject textbooks, which title activities
    descriptively and lean on the publisher's icons. A split that put one
    family wholly on one side would measure the wrong thing.
  * **Both ends of the violation distribution on both sides.** Holding out only
    clean books flatters the result; holding out only dirty ones makes every
    change look like a regression.

    The second axis was originally the publisher match rate, and it had to be
    changed once the corpus was baked: with the current detector every one of
    the 27 local books scores between 80% and 100%, so match rate separates
    nothing and a split stratified on it would have been stratified on noise.
    Cut violations per region still spread from 0.000 to 0.235 across the same
    books -- and it is the number this project judges a change by, since a page
    with no hotspot beats a page with a wrong one. The match rate is still
    recorded per book, as metadata rather than as a stratum.
  * **Assignment is deterministic and written down.** Each book carries the
    stratum it was drawn from and the match rate it had when the split was made,
    so the choice can be audited later rather than taken on trust.

Usage:
    python3 tools/hotspot_extraction/train/splits.py --propose     # write splits.json
    python3 tools/hotspot_extraction/train/splits.py               # show the split
    python3 tools/hotspot_extraction/train/splits.py --check       # is it still valid?
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from books_manager import BooksManager  # noqa: E402

SPLITS_VERSION = 1
SPLITS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "splits.json")

# One book in three is held out. Lower than that and a stratum of two or three
# books contributes nothing to the held-out side; much higher and the fitting
# corpus stops covering the failure modes it is meant to fix.
HELD_OUT_EVERY = 3

# The ELT family names itself: the series titles, and the word for English.
# Everything else in this catalogue is a Turkish subject textbook.
ELT_TITLE_RE = re.compile(
    r"stepwise|waymark|i̇ngilizce|ingilizce|english|coursebook|workbook",
    re.IGNORECASE | re.UNICODE,
)

# Where a book sits in the violation distribution -- cut blocks (panels plus
# solution spaces) per baked region. The thresholds are read off the shape of
# the corpus rather than chosen round: on the 27 local books the rate runs from
# 0.000 to 0.235, with a natural floor of books that cut almost nothing and a
# tail that cuts one block for every four to eight regions.
VIOLATION_BANDS: Tuple[Tuple[str, float], ...] = (
    ("clean", 0.02),
    ("low", 0.07),
    ("high", float("inf")),
)


def template_of(title: str) -> str:
    """Which of the two book families this is."""
    return "elt" if ELT_TITLE_RE.search(title or "") else "subject"


def band_of(cut_rate: Optional[float]) -> str:
    """
    Which end of the violation distribution a book sits at.

    A book with no regions at all has no rate and is filed as `none`: there is
    nothing to be right or wrong about on it, so it is its own stratum rather
    than being counted among the books that cut nothing.
    """
    if cut_rate is None:
        return "none"
    for name, ceiling in VIOLATION_BANDS:
        if cut_rate < ceiling:
            return name
    return "high"


def cut_rate_of(row: Dict[str, Any]) -> Optional[float]:
    """Cut blocks per region for one scored book, or None if it baked nothing."""
    regions = (row.get("yield") or {}).get("regions") or 0
    if not regions:
        return None
    v = row.get("violations") or {}
    return (v.get("panelsCut", 0) + v.get("solutionsCut", 0)) / regions


def catalogue_titles() -> Dict[str, str]:
    manager = BooksManager(base_dir=PROJECT_ROOT, edition="full")
    return {b["id"]: b.get("title", b["id"]) for b in manager.books if b.get("id")}


def local_book_ids() -> List[str]:
    """Books whose PDF is on this machine -- the only ones a fitting run can use."""
    manager = BooksManager(base_dir=PROJECT_ROOT, edition="full")
    out = []
    for b in manager.books:
        book_id = b.get("id")
        if not book_id or book_id in out:
            continue
        local = manager.get_local_path(book_id)
        if local and os.path.isfile(local):
            out.append(book_id)
    return sorted(out)


def propose(scorecard: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Build the split from the corpus as it currently stands.

    Books are grouped by (template, match band) and every third book in each
    stratum, walking the stratum in id order, is held out. Deterministic on
    purpose: re-running this on the same corpus produces the same split, so a
    result cannot be improved by reshuffling until the held-out set is kind.
    """
    from tools.hotspot_extraction.scanner.score import score_catalogue

    ids = local_book_ids()
    if not ids:
        raise RuntimeError("No local PDFs: fetch the corpus first (Stage 3.1).")

    titles = catalogue_titles()
    if scorecard is None:
        scorecard = score_catalogue(ids)
    rows = {r["bookId"]: r for r in scorecard.get("books", [])}

    meta: Dict[str, Dict[str, Any]] = {}
    for book_id in ids:
        title = titles.get(book_id, book_id)
        row = rows.get(book_id) or {}
        cuts = cut_rate_of(row)
        meta[book_id] = {
            "title": title,
            "template": template_of(title),
            "band": band_of(cuts),
            "cutRate": round(cuts, 4) if cuts is not None else None,
            "matchRate": (row.get("join") or {}).get("matchRate"),
            "pages": row.get("pages"),
            "regions": (row.get("yield") or {}).get("regions"),
        }

    data = stratify(meta)
    data["scorer"] = scorecard.get("scorer")
    return data


def stratify(meta: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Divide books already classified by template and band into the two sides.

    Kept separate from `propose` so that the assignment can be argued with on
    its own: it is pure, it depends on nothing on disk, and it is where the
    guarantee that both sides span both templates and both ends of the match
    distribution actually comes from.
    """
    strata: Dict[Tuple[str, str], List[str]] = {}
    for book_id, m in meta.items():
        strata.setdefault((m["template"], m["band"]), []).append(book_id)

    train: List[str] = []
    held_out: List[str] = []
    for key in sorted(strata):
        members = sorted(strata[key])
        for i, book_id in enumerate(members):
            # The stratum's *second* book is the first held out, so a stratum of
            # two contributes to both sides instead of putting its only pair in
            # training.
            (held_out if i % HELD_OUT_EVERY == 1 else train).append(book_id)

    return {
        "version": SPLITS_VERSION,
        "heldOutEvery": HELD_OUT_EVERY,
        "books": meta,
        "train": sorted(train),
        "heldOut": sorted(held_out),
        "strata": {f"{t}/{b}": sorted(v) for (t, b), v in sorted(strata.items())},
    }


def load(path: str = SPLITS_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != SPLITS_VERSION:
        raise ValueError(f"splits.json is version {data.get('version')}, expected {SPLITS_VERSION}")
    return data


def train_ids(path: str = SPLITS_PATH) -> List[str]:
    return list(load(path)["train"])


def held_out_ids(path: str = SPLITS_PATH) -> List[str]:
    return list(load(path)["heldOut"])


def assert_trainable(book_ids: Sequence[str], path: str = SPLITS_PATH) -> None:
    """
    Refuse to proceed if a fitting run is about to read a held-out book.

    This raises rather than warning. A warning in a loop that runs two thousand
    times is a line of output nobody reads, and the whole value of the held-out
    set is that it stays unread.
    """
    held = set(held_out_ids(path))
    trespass = sorted(set(book_ids) & held)
    if trespass:
        raise AssertionError(
            "held-out books may not be scored during fitting: " + ", ".join(trespass)
        )


def check(path: str = SPLITS_PATH) -> List[str]:
    """
    What is wrong with the split as recorded, if anything.

    A split is stale the moment the corpus changes: a book fetched after it was
    written is in neither list, and one deleted since is in a list but not on
    disk. Either way a fitting run would silently cover a different corpus than
    the split describes.
    """
    problems: List[str] = []
    data = load(path)
    train, held = set(data["train"]), set(data["heldOut"])

    overlap = sorted(train & held)
    if overlap:
        problems.append(f"in both lists: {', '.join(overlap)}")

    on_disk = set(local_book_ids())
    missing = sorted((train | held) - on_disk)
    if missing:
        problems.append(f"in the split but no PDF on disk: {', '.join(b[:8] for b in missing)}")
    unassigned = sorted(on_disk - (train | held))
    if unassigned:
        problems.append(f"on disk but in neither list: {', '.join(b[:8] for b in unassigned)}")

    books = data.get("books", {})
    for side, name in ((train, "train"), (held, "heldOut")):
        templates = {books.get(b, {}).get("template") for b in side}
        if len(templates) < 2:
            problems.append(f"{name} covers only the {templates} template(s)")
        bands = {books.get(b, {}).get("band") for b in side}
        if not ({"clean", "high"} & bands) or len(bands) < 2:
            problems.append(f"{name} does not span the violation distribution: {sorted(bands)}")
    return problems


def describe(data: Dict[str, Any]) -> str:
    books = data.get("books", {})

    def table(ids: Sequence[str]) -> str:
        lines = []
        for book_id in ids:
            m = books.get(book_id, {})
            cuts, match = m.get("cutRate"), m.get("matchRate")
            lines.append(
                f"    {book_id[:8]}  {m.get('template', '?'):<7} {m.get('band', '?'):<5}"
                f"  {('%.3f' % cuts) if cuts is not None else '   --':>6} cuts/reg"
                f"  {('%.0f%%' % (match * 100)) if match is not None else '  --':>5} match"
                f"  {str(m.get('pages', '?')):>4}pp  {str(m.get('title', ''))[:32]}"
            )
        return "\n".join(lines)

    out = [
        f"Train ({len(data['train'])} books)",
        table(data["train"]),
        "",
        f"Held out ({len(data['heldOut'])} books) -- never scored during fitting",
        table(data["heldOut"]),
        "",
        "Strata (template/band):",
    ]
    for key, members in data.get("strata", {}).items():
        held = set(data["heldOut"])
        marked = " ".join(f"{b[:8]}{'*' if b in held else ''}" for b in members)
        out.append(f"    {key:<14} {marked}")
    out.append("\n    * = held out")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--propose", action="store_true",
                    help="build the split from the corpus on disk and write splits.json")
    ap.add_argument("--check", action="store_true",
                    help="report whether the recorded split still describes the corpus")
    ap.add_argument("--path", default=SPLITS_PATH)
    args = ap.parse_args()

    if args.propose:
        if os.path.isfile(args.path):
            print(f"{args.path} already exists.")
            print("A split is rewritten only deliberately: re-proposing after a fitting run "
                  "would let a disappointing held-out result be split away. Delete it first "
                  "if the corpus has genuinely changed.")
            return 1
        data = propose()
        with open(args.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Wrote {args.path}\n")
        print(describe(data))
        return 0

    if not os.path.isfile(args.path):
        print(f"No split recorded at {args.path}. Run with --propose.")
        return 1

    data = load(args.path)
    print(describe(data))
    problems = check(args.path)
    print("")
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Split is valid for the corpus on disk.")
    return 0 if not args.check else 0


if __name__ == "__main__":
    sys.exit(main())
