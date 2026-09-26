"""
What "better" means, as one number -- and, more importantly, as its parts.

The rule this project is judged by is that a page with no hotspot beats a page
with a wrong one, so the loss is written to make that true arithmetically
rather than as an intention: the violation weights are large enough that no
amount of coverage can pay for a single cut block, overlap or sliver. Coverage
appears at all only to break ties between two settings that violate nothing,
which is the one case where finding more is unambiguously better.

Two things keep it honest.

**Per-book normalisation.** `51cdbbce` is 417 pages and `c9f63718` is 71. Summed
raw, the fit would be a fit to the long books; every component is therefore a
rate -- per region, or per manifest entry -- before the books are averaged.

**The ruler does not move.** The three thresholds the violation rules are
written in are frozen in `scanner/profile.py`, so a candidate cannot score well
by loosening the definition of a violation. Without that this whole module
would measure nothing.

The scalar is what the optimizer follows; it is never what gets reported. Every
evaluation carries its components, because "loss fell 4%" is not a finding and
"solution cuts fell 31%, match rate fell 2pp" is.
"""

from dataclasses import dataclass, field, asdict
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.hotspot_extraction.scanner.profile import Profile, apply_profile, from_dict, profile_hash
from tools.hotspot_extraction.scanner.score import (
    Tally,
    finish_book,
    merge_tallies,
    score_pages,
    tally_pages,
)
from tools.hotspot_extraction.scanner.serializer import serialize_activity
from tools.hotspot_extraction.scanner.regions import clean_page_activities
from tools.hotspot_extraction.train import cache as cache_mod


@dataclass(frozen=True)
class Weights:
    """
    How much each kind of wrongness costs.

    The numbers are not arbitrary and their *ratios* are the whole design. A
    book has on the order of one region per page and at most a few hundred
    manifest entries, so a rate of 0.01 is a real quantity of either. `cut` at
    100 against `cover` at 1 means a candidate would have to fill every blank
    page in the book to pay for a cut rate of one violation per hundred
    regions -- which it cannot, so it never tries.

    `miss` sits between them: failing to find what the publisher says is there
    is a real defect, but a wrong region is worse than a missing one, and the
    weights say so.
    """
    cut: float = 100.0
    overlap: float = 200.0
    tall: float = 40.0
    sliver: float = 40.0
    miss: float = 20.0
    cover: float = 1.0


DEFAULT_WEIGHTS = Weights()


@dataclass
class BookScore:
    """One book's contribution, as rates rather than counts."""
    book_id: str
    pages: int
    regions: int
    oges: int
    matched: int
    panels_cut: int
    solutions_cut: int
    overlaps: int
    tall: int
    slivers: int
    pages_with_regions: int

    @property
    def cut_rate(self) -> float:
        return (self.panels_cut + self.solutions_cut) / self.regions if self.regions else 0.0

    @property
    def overlap_rate(self) -> float:
        return self.overlaps / self.regions if self.regions else 0.0

    @property
    def tall_rate(self) -> float:
        return self.tall / self.regions if self.regions else 0.0

    @property
    def sliver_rate(self) -> float:
        return self.slivers / self.regions if self.regions else 0.0

    @property
    def miss_rate(self) -> float:
        """
        Share of the publisher's entries with no sound region.

        A book with no manifest contributes nothing here rather than a free
        zero: it has no ground truth, so it cannot be right or wrong about it,
        and scoring it as perfect would let a fitter buy match rate by finding
        nothing in the books that can check it.
        """
        return (self.oges - self.matched) / self.oges if self.oges else 0.0

    @property
    def has_manifest(self) -> bool:
        return self.oges > 0

    @property
    def coverage(self) -> float:
        return self.pages_with_regions / self.pages if self.pages else 0.0

    def loss(self, w: Weights = DEFAULT_WEIGHTS) -> float:
        total = (
            w.cut * self.cut_rate
            + w.overlap * self.overlap_rate
            + w.tall * self.tall_rate
            + w.sliver * self.sliver_rate
            - w.cover * self.coverage
        )
        if self.has_manifest:
            total += w.miss * self.miss_rate
        return total


@dataclass
class Evaluation:
    """
    One profile's score over one set of books, with the parts kept.

    `books` is per-book because the acceptance gate is per-book: an aggregate
    that improves while one book collapses is the exact failure the checked-in
    benchmark used to hide, and it can only be seen if the rows survive.
    """
    profile_hash: str
    loss: float
    books: List[BookScore] = field(default_factory=list)
    failed: List[Dict[str, str]] = field(default_factory=list)

    def totals(self) -> Dict[str, Any]:
        regions = sum(b.regions for b in self.books)
        oges = sum(b.oges for b in self.books)
        matched = sum(b.matched for b in self.books)
        pages = sum(b.pages for b in self.books)
        cuts = sum(b.panels_cut + b.solutions_cut for b in self.books)
        return {
            "books": len(self.books),
            "pages": pages,
            "regions": regions,
            "cuts": cuts,
            "cutPerRegion": round(cuts / regions, 4) if regions else 0.0,
            "overlaps": sum(b.overlaps for b in self.books),
            "tall": sum(b.tall for b in self.books),
            "slivers": sum(b.slivers for b in self.books),
            "oges": oges,
            "matched": matched,
            "matchRate": round(matched / oges, 4) if oges else None,
            "pagesWithRegions": sum(b.pages_with_regions for b in self.books),
            "coverage": round(sum(b.pages_with_regions for b in self.books) / pages, 4) if pages else 0.0,
            "loss": round(self.loss, 6),
        }

    def summary(self) -> str:
        t = self.totals()
        return (
            f"loss {t['loss']:+.4f} | cuts {t['cuts']} ({t['cutPerRegion']:.4f}/region) "
            f"| overlaps {t['overlaps']} | tall {t['tall']} | slivers {t['slivers']} "
            f"| match {t['matchRate']} | coverage {t['coverage']} "
            f"| {t['regions']} regions over {t['pages']} pages"
        )


# --------------------------------------------------------------- one book

def score_book(
    book_id: str,
    *,
    cache_dir: str = cache_mod.DEFAULT_CACHE_DIR,
    pages: Optional[Sequence[int]] = None,
    shard: Optional["cache_mod.BookShard"] = None,
) -> Dict[str, Any]:
    """
    Replay one cached book under whatever profile is active, and score it.

    The replay reproduces the bake's own final step -- `clean_page_activities`
    then `serialize_activity`, sheets with nothing left dropped -- because the
    scorer's rules are stated over what is written to disk, and the cleanup is
    where slivers are dropped and regions are pushed apart. Scoring the raw
    growth output instead would report violations the shipped bake does not
    have.
    """
    if shard is None:
        shard = cache_mod.load_shard(cache_mod.shard_path(book_id, cache_dir))
    wanted = set(pages) if pages else None
    sheets = [sp for sp in shard.pages if wanted is None or sp.page_num in wanted]

    return score_pages(
        shard.book_id,
        shard.page_count,
        shard.folio_map,
        _replay_pages(sheets),
        source="replay",
    )


def book_score_of(row: Dict[str, Any]) -> BookScore:
    """Reduce a scorer row to the handful of numbers the loss is built from."""
    v = row["violations"]
    y = row["yield"]
    j = row["join"]
    return BookScore(
        book_id=row["bookId"],
        pages=row["pages"],
        regions=y["regions"],
        oges=j["oges"],
        matched=j["matched"],
        panels_cut=v["panelsCut"],
        solutions_cut=v["solutionsCut"],
        overlaps=v["overlaps"],
        tall=v["tall"],
        slivers=v["slivers"],
        pages_with_regions=y["pagesWithRegions"],
    )


# ----------------------------------------------------- chunks of one book
#
# A whole-book task list is only as fast as its longest book, and the corpus has
# two 400-page books that replay in 23 and 17 seconds against a 74-second total.
# Farming those out whole leaves eighteen cores idle for twenty seconds of every
# evaluation, which is the difference between a two-hour fitting run and a
# fourteen-hour one. Chunking by page removes the long pole; `merge_tallies`
# explains why the seams are invisible.


@dataclass(frozen=True)
class Chunk:
    """A slice of one book: the unit of work an evaluation is divided into."""
    book_id: str
    index: int
    first_page: int
    last_page: int

    @property
    def pages(self) -> int:
        return self.last_page - self.first_page + 1


def plan_chunks(
    book_ids: Sequence[str],
    *,
    cache_dir: str = cache_mod.DEFAULT_CACHE_DIR,
    chunk_pages: int = 80,
) -> List[Chunk]:
    """
    Divide the corpus into work units of roughly `chunk_pages` sheets.

    Read from the shard headers, so the parent never unpickles a page. Eighty
    is about four seconds of replay on the slowest book in the corpus, which is
    short enough that the tail of an evaluation is not one straggler and long
    enough that process startup stays a small share of it.
    """
    out: List[Chunk] = []
    for book_id in book_ids:
        head = cache_mod.shard_header(cache_mod.shard_path(book_id, cache_dir))
        if not head:
            continue
        total = int(head["pageCount"])
        index = 0
        for first in range(1, total + 1, chunk_pages):
            last = min(total, first + chunk_pages - 1)
            out.append(Chunk(book_id, index, first, last))
            index += 1
    # Biggest first: the long chunks start while there are still cores free,
    # rather than being picked up last and deciding the wall time on their own.
    out.sort(key=lambda c: (-c.pages, c.book_id, c.index))
    return out


def tally_chunk(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Child entry point: replay and tally one slice of one book under one profile.

    Loads the shard, keeps only the pages of this chunk, and releases the rest
    before any detection runs -- a child holding a whole 400-page parse to score
    eighty sheets of it is the memory profile this project does not run.
    """
    import pymupdf
    pymupdf.TOOLS.set_low_memory(True)

    apply_profile(from_dict(task["profile"]))

    book_id = task["book_id"]
    first, last = task["first_page"], task["last_page"]
    shard = cache_mod.load_shard(cache_mod.shard_path(book_id, task["cache_dir"]))
    pages = [sp for sp in shard.pages if first <= sp.page_num <= last]
    folio = {p: v for p, v in shard.folio_map.items() if first <= p <= last}
    page_count = shard.page_count
    shard.pages = []
    del shard

    tally, seen = tally_pages(book_id, folio, _replay_pages(pages))
    return {
        "book_id": book_id,
        "index": task["index"],
        "first_page": first,
        "last_page": last,
        "page_count": page_count,
        "tally": tally,
        "seen": seen,
        "folio": folio,
    }


def _replay_pages(pages) -> Iterable[Tuple[int, float, float, List[Dict[str, Any]], Optional[Dict[str, Any]], Sequence[str]]]:
    """
    Replay sheets one at a time, in the bake's own final form.

    `clean_page_activities` then `serialize_activity`, because the scorer's
    rules are stated over what is written to disk and the cleanup is where
    slivers are dropped and regions pushed apart. Scoring raw growth output
    would report violations the shipped bake does not have.
    """
    for sp in pages:
        result = cache_mod.replay_page(sp)
        acts = clean_page_activities(result.activities) if result.activities else []
        yield (
            sp.page_num,
            sp.primitives.width,
            sp.primitives.height,
            [serialize_activity(a) for a in acts],
            result.diagnostics(sp.primitives.width, sp.primitives.height),
            result.anchor_ids,
        )


def rows_from_chunks(chunk_results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Reassemble finished book rows from however many chunks each arrived in.

    Chunks are put back in page order before merging: `merge_tallies` keeps the
    first claim on each manifest entry, and "first" has to mean first sheet, not
    first child to return.
    """
    by_book: Dict[str, List[Dict[str, Any]]] = {}
    for res in chunk_results:
        by_book.setdefault(res["book_id"], []).append(res)

    rows: List[Dict[str, Any]] = []
    for book_id, parts in sorted(by_book.items()):
        parts.sort(key=lambda r: r["first_page"])
        folio: Dict[int, int] = {}
        seen: List[int] = []
        for r in parts:
            folio.update(r["folio"])
            seen.extend(r["seen"])
        tally = merge_tallies([r["tally"] for r in parts])
        rows.append(finish_book(
            book_id, tally, seen, folio, parts[0]["page_count"], source="replay",
        ))
    return rows


# ----------------------------------------------------------- one candidate

def evaluate(
    profile: Profile,
    book_ids: Sequence[str],
    *,
    cache_dir: str = cache_mod.DEFAULT_CACHE_DIR,
    weights: Weights = DEFAULT_WEIGHTS,
    pages: Optional[Sequence[int]] = None,
) -> Evaluation:
    """
    Score one profile over a set of books, in this process, one shard at a time.

    Books are loaded and released in turn rather than held together: the whole
    corpus is 113 MB of pickles and would be several times that live, and the
    memory rule on this project is absolute. The caller that wants parallelism
    runs *this* in a child process per candidate.
    """
    apply_profile(profile)
    ev = Evaluation(profile_hash=profile_hash(profile), loss=0.0)
    for book_id in book_ids:
        try:
            row = score_book(book_id, cache_dir=cache_dir, pages=pages)
        except (FileNotFoundError, KeyError, ValueError) as exc:
            ev.failed.append({"bookId": book_id, "error": str(exc)})
            continue
        ev.books.append(book_score_of(row))
    if ev.books:
        ev.loss = sum(b.loss(weights) for b in ev.books) / len(ev.books)
    else:
        # No book scored is not a good score. Returning 0 here would make a
        # broken candidate look like a perfect one.
        ev.loss = float("inf")
    return ev


class ChunkPool:
    """
    A reusable pool of chunk workers, and the one place the memory rule is kept.

    The fitting loop evaluates thousands of candidates, and standing up a fresh
    `ProcessPoolExecutor` per candidate would spend more time importing pymupdf
    than detecting. One pool is therefore held across candidates -- but with
    `max_tasks_per_child` bounded, so a worker is retired after a handful of
    chunks rather than living for the whole run and accumulating whatever
    pymupdf does not give back. Each child holds at most one chunk of one book.

    The profile travels in the task, so a long-lived worker is still evaluating
    exactly the candidate the driver sent, with no state carried between them
    beyond the module globals `apply_profile` overwrites on entry.
    """

    def __init__(
        self,
        workers: Optional[int] = None,
        *,
        cache_dir: str = cache_mod.DEFAULT_CACHE_DIR,
        chunk_pages: int = 80,
        tasks_per_child: int = 16,
    ):
        from concurrent.futures import ProcessPoolExecutor
        self.workers = workers or max(1, min(16, (os.cpu_count() or 4) - 2))
        self.cache_dir = cache_dir
        self.chunk_pages = chunk_pages
        self._executor = ProcessPoolExecutor(
            max_workers=self.workers, max_tasks_per_child=tasks_per_child
        )
        self._plans: Dict[Tuple[str, ...], List[Chunk]] = {}

    def chunks_for(self, book_ids: Sequence[str]) -> List[Chunk]:
        key = tuple(book_ids)
        if key not in self._plans:
            self._plans[key] = plan_chunks(
                book_ids, cache_dir=self.cache_dir, chunk_pages=self.chunk_pages
            )
        return self._plans[key]

    def evaluate(
        self,
        profile: Profile,
        book_ids: Sequence[str],
        *,
        weights: Weights = DEFAULT_WEIGHTS,
    ) -> Evaluation:
        payload = profile.to_dict()
        tasks = [
            {
                "profile": payload,
                "cache_dir": self.cache_dir,
                "book_id": c.book_id,
                "index": c.index,
                "first_page": c.first_page,
                "last_page": c.last_page,
            }
            for c in self.chunks_for(book_ids)
        ]
        results = list(self._executor.map(tally_chunk, tasks))
        ev = Evaluation(profile_hash=profile_hash(profile), loss=0.0)
        ev.books = [book_score_of(row) for row in rows_from_chunks(results)]
        ev.loss = (
            sum(b.loss(weights) for b in ev.books) / len(ev.books)
            if ev.books else float("inf")
        )
        return ev

    def close(self) -> None:
        self._executor.shutdown(wait=True)

    def __enter__(self) -> "ChunkPool":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def evaluate_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    The child-process entry point: dicts in, dicts out.

    A `Profile` would pickle fine, but the task crossing the boundary as plain
    JSON-shaped data is what lets a fitting run be resumed, logged and replayed
    without this module's classes being importable at the other end.
    """
    profile = from_dict(payload["profile"])
    weights = Weights(**payload.get("weights", {}))
    ev = evaluate(
        profile,
        payload["books"],
        cache_dir=payload.get("cache_dir", cache_mod.DEFAULT_CACHE_DIR),
        weights=weights,
        pages=payload.get("pages"),
    )
    return {
        "profileHash": ev.profile_hash,
        "loss": ev.loss,
        "totals": ev.totals(),
        "books": [asdict(b) for b in ev.books],
        "failed": ev.failed,
    }


# ------------------------------------------------------------------- CLI

def _verify_one(task: Dict[str, Any]) -> Dict[str, Any]:
    """Child: does replaying this book score identically to its bake on disk?"""
    import pymupdf
    pymupdf.TOOLS.set_low_memory(True)
    from tools.hotspot_extraction.scanner import score as score_mod

    book_id = task["book_id"]
    try:
        baked = score_mod.score_baked(book_id)
    except (FileNotFoundError, ValueError) as exc:
        return {"book_id": book_id, "status": "no-bake", "error": str(exc)}
    try:
        replayed = score_book(book_id, cache_dir=task["cache_dir"])
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return {"book_id": book_id, "status": "no-shard", "error": str(exc)}

    differs = [k for k in ("yield", "join", "buckets", "violations", "stats")
               if baked[k] != replayed[k]]
    return {
        "book_id": book_id,
        "status": "equal" if not differs else "differs",
        "differs": differs,
        "regions": replayed["yield"]["regions"],
        "pages": replayed["pages"],
    }


def _evaluate_one(task: Dict[str, Any]) -> Dict[str, Any]:
    """Child: score one book under one profile."""
    import pymupdf
    pymupdf.TOOLS.set_low_memory(True)
    payload = dict(task)
    payload["books"] = [task["book_id"]]
    out = evaluate_dict(payload)
    out["book_id"] = task["book_id"]
    return out


def main() -> int:
    import argparse
    import json as _json
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from tools.hotspot_extraction.train import splits as splits_mod

    ap = argparse.ArgumentParser(description="The fitting objective: replay cached books and score them.")
    ap.add_argument("--verify", action="store_true",
                    help="Assert a replayed score equals the book's baked score, book by book")
    ap.add_argument("--baseline", action="store_true",
                    help="Score the current profile over the train and held-out splits")
    ap.add_argument("--only", type=str, help="Comma-separated book ids")
    ap.add_argument("--cache-dir", type=str, default=cache_mod.DEFAULT_CACHE_DIR)
    ap.add_argument("--workers", type=int, default=4,
                    help="Child processes; each holds one book's shard and then exits")
    ap.add_argument("--json", type=str, help="Write the result here")
    args = ap.parse_args()

    if args.only:
        groups = {"only": [x.strip() for x in args.only.split(",") if x.strip()]}
    else:
        groups = {"train": splits_mod.train_ids(), "heldOut": splits_mod.held_out_ids()}

    if not args.verify and not args.baseline:
        ap.error("nothing to do: pass --verify or --baseline")

    out: Dict[str, Any] = {}

    if args.verify:
        tasks = [{"book_id": b, "cache_dir": args.cache_dir}
                 for ids in groups.values() for b in ids]
        rows: List[Dict[str, Any]] = []
        # One short-lived child per book, default heap: a sweep that holds
        # every shard at once is the thing this project does not do.
        with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1) as ex:
            for fut in as_completed([ex.submit(_verify_one, t) for t in tasks]):
                row = fut.result()
                rows.append(row)
                mark = {"equal": "=", "differs": "x"}.get(row["status"], "-")
                detail = "" if row["status"] == "equal" else f"  {row.get('differs') or row.get('error')}"
                print(f"  {mark} {row['book_id'][:8]}  {row['status']}{detail}")
        bad = [r for r in rows if r["status"] != "equal"]
        print(f"\nreplay == bake on {len(rows) - len(bad)}/{len(rows)} books")
        out["verify"] = sorted(rows, key=lambda r: r["book_id"])
        if bad and not args.json:
            return 1

    if args.baseline:
        from tools.hotspot_extraction.scanner.profile import load_profile
        profile = load_profile()
        payload_base = {"profile": profile.to_dict(), "cache_dir": args.cache_dir}
        for name, ids in groups.items():
            tasks = [dict(payload_base, book_id=b) for b in ids]
            books: List[Dict[str, Any]] = []
            with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1) as ex:
                for fut in as_completed([ex.submit(_evaluate_one, t) for t in tasks]):
                    res = fut.result()
                    books.extend(res["books"])
            ev = Evaluation(
                profile_hash=profile_hash(profile),
                loss=0.0,
                books=[BookScore(**b) for b in books],
            )
            ev.loss = sum(b.loss() for b in ev.books) / len(ev.books) if ev.books else float("inf")
            print(f"\n{name}: {ev.summary()}")
            out[name] = {
                "profileHash": ev.profile_hash,
                "loss": ev.loss,
                "totals": ev.totals(),
                "books": [asdict(b) for b in sorted(ev.books, key=lambda b: b.book_id)],
            }

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            _json.dump(out, f, indent=2)
            f.write("\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
