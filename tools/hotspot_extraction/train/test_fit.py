"""
Tests for the objective and the optimizer.

The one that matters most is `test_chunking_does_not_change_the_score`: the
whole speed argument for slicing books into page chunks rests on the seams
being invisible, and if they are not, every number a fitting run produces is
measured against a book the detector never bakes.

The rest guard the property the project is actually about -- that correctness
outranks coverage -- by asserting it arithmetically rather than trusting that
the weights were chosen well.
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.hotspot_extraction.scanner.profile import DEFAULTS, TUNABLE, BY_KEY
from tools.hotspot_extraction.scanner.score import Tally, merge_tallies
from tools.hotspot_extraction.train import cache as cache_mod
from tools.hotspot_extraction.train import fit as fit_mod
from tools.hotspot_extraction.train import objective as obj


def _a_cached_book():
    ids = cache_mod.cached_books()
    return ids[0] if ids else None


class TestWeights:
    def test_no_amount_of_coverage_pays_for_one_violation(self):
        """
        A page with no hotspot beats a page with a wrong one -- as arithmetic.

        The clean book finds regions on half its pages and breaks no rule; the
        other finds them everywhere and cuts one drawn block in a hundred
        regions. The second must score worse, or the loss does not say what
        this project says.
        """
        clean = obj.BookScore(
            book_id="a", pages=100, regions=100, oges=0, matched=0,
            panels_cut=0, solutions_cut=0, overlaps=0, tall=0, slivers=0,
            pages_with_regions=50,
        )
        greedy = obj.BookScore(
            book_id="b", pages=100, regions=100, oges=0, matched=0,
            panels_cut=1, solutions_cut=0, overlaps=0, tall=0, slivers=0,
            pages_with_regions=100,
        )
        assert clean.loss() < greedy.loss()

    def test_one_overlap_outweighs_a_whole_book_of_coverage(self):
        clean = obj.BookScore("a", 100, 100, 0, 0, 0, 0, 0, 0, 0, 0)
        overlapping = obj.BookScore("b", 100, 100, 0, 0, 0, 0, 1, 0, 0, 100)
        assert clean.loss() < overlapping.loss()

    def test_a_book_with_no_manifest_is_not_scored_as_perfectly_matched(self):
        """
        Otherwise a fitter buys match rate by finding nothing where nothing checks it.
        """
        no_manifest = obj.BookScore("a", 10, 10, 0, 0, 0, 0, 0, 0, 0, 10)
        assert no_manifest.miss_rate == 0.0
        assert not no_manifest.has_manifest
        with_manifest = obj.BookScore("b", 10, 10, 10, 0, 0, 0, 0, 0, 0, 10)
        assert with_manifest.loss() > no_manifest.loss()

    def test_finding_what_the_publisher_marked_is_worth_something(self):
        missed = obj.BookScore("a", 10, 10, 10, 0, 0, 0, 0, 0, 0, 10)
        found = obj.BookScore("b", 10, 10, 10, 10, 0, 0, 0, 0, 0, 10)
        assert found.loss() < missed.loss()


class TestMergeTallies:
    def test_counters_add(self):
        merged = merge_tallies([
            Tally(regions=3, panels_cut=1, drifts=[1.0]),
            Tally(regions=4, panels_cut=2, drifts=[2.0]),
        ])
        assert merged.regions == 7
        assert merged.panels_cut == 3
        assert sorted(merged.drifts) == [1.0, 2.0]

    def test_the_first_chunk_to_claim_an_entry_keeps_it(self):
        """
        `bucket_of` files each entry on the first *sheet* that claims it, so the
        merge has to take chunks in page order and keep the earliest verdict --
        not whichever child returned first.
        """
        early = Tally(bucket_of={"oge-1": "ok"})
        late = Tally(bucket_of={"oge-1": "rule-violation"})
        assert merge_tallies([early, late]).bucket_of["oge-1"] == "ok"

    def test_by_anchored_adds_per_key(self):
        a = Tally()
        a.by_anchored["panelsCut"] = 2
        b = Tally()
        b.by_anchored["panelsCut"] = 3
        b.by_anchored["tall"] = 1
        merged = merge_tallies([a, b])
        assert merged.by_anchored["panelsCut"] == 5
        assert merged.by_anchored["tall"] == 1


class TestChunkPlan:
    @pytest.mark.skipif(not cache_mod.cached_books(), reason="no cache built")
    def test_chunks_cover_every_page_exactly_once(self):
        book = _a_cached_book()
        head = cache_mod.shard_header(cache_mod.shard_path(book))
        chunks = obj.plan_chunks([book], chunk_pages=37)
        covered = [p for c in chunks for p in range(c.first_page, c.last_page + 1)]
        assert covered == sorted(covered)
        assert covered == list(range(1, head["pageCount"] + 1))

    @pytest.mark.skipif(not cache_mod.cached_books(), reason="no cache built")
    def test_chunking_does_not_change_the_score(self):
        """
        The load-bearing test: a book scored in slices must score as one walk.

        Chunking exists only to stop one 400-page book deciding the wall time of
        every evaluation. It is worth having only if the number it produces is
        the same number, so this compares a chunked evaluation against scoring
        the whole book in one process.
        """
        book = _a_cached_book()
        whole = obj.book_score_of(obj.score_book(book))
        chunk_results = [
            obj.tally_chunk({
                "profile": DEFAULTS.to_dict(),
                "cache_dir": cache_mod.DEFAULT_CACHE_DIR,
                "book_id": c.book_id,
                "index": c.index,
                "first_page": c.first_page,
                "last_page": c.last_page,
            })
            for c in obj.plan_chunks([book], chunk_pages=13)
        ]
        in_pieces = obj.book_score_of(obj.rows_from_chunks(chunk_results)[0])
        assert in_pieces == whole


class TestEncoding:
    def test_round_trip_leaves_a_profile_unchanged(self):
        knobs = list(TUNABLE)
        x = fit_mod.encode(DEFAULTS, knobs)
        assert DEFAULTS == fit_mod.decode(DEFAULTS, knobs, x)

    def test_the_unit_interval_spans_the_declared_range(self):
        knob = BY_KEY["PAD"]
        lo_end = fit_mod.decode(DEFAULTS, [knob], [0.0])
        hi_end = fit_mod.decode(DEFAULTS, [knob], [1.0])
        assert lo_end.PAD == knob.lo
        assert hi_end.PAD == knob.hi

    def test_out_of_range_coordinates_are_clamped_not_wrapped(self):
        knob = BY_KEY["PAD"]
        assert fit_mod.decode(DEFAULTS, [knob], [-3.0]).PAD == knob.lo
        assert fit_mod.decode(DEFAULTS, [knob], [9.0]).PAD == knob.hi

    def test_integer_knobs_decode_to_integers(self):
        knob = BY_KEY["GRAPHIC_PASSES"]
        value = fit_mod.decode(DEFAULTS, [knob], [0.5]).GRAPHIC_PASSES
        assert isinstance(value, int)


class TestBudget:
    def test_an_evaluation_ceiling_stops_the_run(self):
        b = fit_mod.Budget(evaluations=2)
        assert not b.spent()
        b.charge(); b.charge()
        assert b.spent()

    def test_a_spent_clock_stops_the_run(self):
        b = fit_mod.Budget(seconds=-1)
        assert b.spent()
