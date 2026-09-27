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

from tools.hotspot_extraction.scanner.profile import DEFAULTS, TUNABLE, BY_KEY, apply_profile, profile_applied
from tools.hotspot_extraction.scanner.score import Tally, merge_tallies
from tools.hotspot_extraction.train import cache as cache_mod
from tools.hotspot_extraction.train import fit as fit_mod
from tools.hotspot_extraction.train import objective as obj


def _a_cached_book():
    ids = cache_mod.cached_books()
    return ids[0] if ids else None


def _a_cached_training_book():
    from tools.hotspot_extraction.train import splits as splits_mod
    cached = set(cache_mod.cached_books())
    ids = [b for b in splits_mod.train_ids() if b in cached]
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


class TestGates:
    """
    A book with no manifest scores `100·cut_rate − coverage`, so emptying it is
    its optimum. The loss cannot see that; the gates are what does.
    """

    def _ev(self, *books):
        return obj.Evaluation(profile_hash="x", loss=0.0, books=list(books))

    def test_a_book_emptied_to_buy_a_better_loss_fails(self):
        before = obj.BookScore("nomanifest", 100, 80, 0, 0, 2, 0, 0, 0, 0, 60)
        after = obj.BookScore("nomanifest", 100, 10, 0, 0, 0, 0, 0, 0, 0, 10)
        assert after.loss() < before.loss()  # the exit the gates close
        failures = self._ev(after).gates(self._ev(before))
        assert {f["gate"] for f in failures} == {"coverage", "regions"}
        assert all(f["bookId"] == "nomanifest" for f in failures)

    def test_small_losses_within_the_allowance_pass(self):
        before = obj.BookScore("a", 100, 100, 10, 8, 0, 0, 0, 0, 0, 60)
        after = obj.BookScore("a", 100, 85, 10, 8, 0, 0, 0, 0, 0, 56)
        assert self._ev(after).gates(self._ev(before)) == []

    def test_coverage_gains_never_fail(self):
        before = obj.BookScore("a", 100, 50, 0, 0, 0, 0, 0, 0, 0, 40)
        after = obj.BookScore("a", 100, 58, 0, 0, 0, 0, 0, 0, 0, 70)
        assert self._ev(after).gates(self._ev(before)) == []

    def test_a_book_whose_regions_multiply_fails(self):
        """Cuts are per region: more regions dilute the rate without moving an edge."""
        before = obj.BookScore("a", 100, 174, 0, 0, 0, 0, 0, 0, 0, 40)
        after = obj.BookScore("a", 100, 418, 0, 0, 0, 0, 0, 0, 0, 41)
        failures = self._ev(after).gates(self._ev(before))
        assert [f["gate"] for f in failures] == ["regions-gained"]

    def test_a_small_book_may_gain_a_few_regions(self):
        before = obj.BookScore("a", 100, 5, 0, 0, 0, 0, 0, 0, 0, 5)
        after = obj.BookScore("a", 100, 12, 0, 0, 0, 0, 0, 0, 0, 9)
        assert self._ev(after).gates(self._ev(before)) == []

    def test_a_book_the_candidate_did_not_score_fails(self):
        before = obj.BookScore("a", 100, 50, 0, 0, 0, 0, 0, 0, 0, 40)
        failures = self._ev().gates(self._ev(before))
        assert failures == [{"bookId": "a", "gate": "not-scored"}]


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


    @pytest.mark.skipif(not cache_mod.cached_books(), reason="no cache built")
    def test_the_pool_outlives_its_workers(self):
        """
        Two evaluations, each needing more chunks than the workers may serve.

        With `max_tasks_per_child`, CPython's executor stops replacing retired
        workers (gh-115634) and `map` waits forever. Run in a child with a
        clock, so the old failure is a failed test rather than a hung suite.
        """
        import subprocess
        book = _a_cached_book()
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from tools.hotspot_extraction.train import objective as obj\n"
            "from tools.hotspot_extraction.scanner.profile import DEFAULTS\n"
            "if __name__ == '__main__':\n"
            "    with obj.ChunkPool(workers=2, chunk_pages=13, tasks_per_child=2) as pool:\n"
            "        a = pool.evaluate(DEFAULTS, [%r])\n"
            "        b = pool.evaluate(DEFAULTS, [%r])\n"
            "    whole = obj.book_score_of(obj.score_book(%r))\n"
            "    assert a.books == b.books == [whole], (a.books, b.books, whole)\n"
            "    print('ok')\n"
        ) % (PROJECT_ROOT, book, book, book)
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=180
        )
        assert done.returncode == 0, done.stderr[-2000:]
        assert done.stdout.strip().endswith("ok")


# Knobs the block detectors read. Moved far enough that, measured under the
# candidate, a sheet would lose most of its answer spaces and panels.
_RULER_BENDER = DEFAULTS.replace(RULE_MIN=72.0, PANEL_MIN_H=100.0)
_RULER_PAGES = (1, 60)


class TestTheRulerDoesNotMove:
    """
    The blocks a cut is counted against are the scorer's ruler.

    If a candidate profile could shrink them, it would score better by
    detecting fewer answer spaces rather than by cutting fewer, and a fitting
    loop would find that long before it found anything real.
    """

    def _tally(self, profile, book):
        try:
            return obj.tally_chunk({
                "profile": profile.to_dict(),
                "cache_dir": cache_mod.DEFAULT_CACHE_DIR,
                "book_id": book,
                "index": 0,
                "first_page": _RULER_PAGES[0],
                "last_page": _RULER_PAGES[1],
            })["tally"]
        finally:
            apply_profile(DEFAULTS)

    @pytest.mark.skipif(not cache_mod.cached_books(), reason="no cache built")
    def test_block_knobs_cannot_move_the_ruler(self):
        book = _a_cached_training_book()
        default = self._tally(DEFAULTS, book)
        bent = self._tally(_RULER_BENDER, book)
        assert default.solutions_total > 0 and default.panels_total > 0
        assert bent.solutions_total == default.solutions_total
        assert bent.panels_total == default.panels_total

    @pytest.mark.skipif(not cache_mod.cached_books(), reason="no cache built")
    def test_the_knobs_do_move_the_blocks_growth_sees(self):
        """
        Otherwise the test above proves nothing: the bent profile has to change
        what the block detectors return when they run under it.
        """
        from tools.hotspot_extraction.scanner.pipeline import detect_blocks

        book = _a_cached_training_book()
        shard = cache_mod.load_shard(cache_mod.shard_path(book))
        first, last = _RULER_PAGES
        fewer = 0
        for sp in shard.pages:
            if not first <= sp.page_num <= last:
                continue
            ruler = cache_mod.replay_page(sp).blocks
            with profile_applied(_RULER_BENDER):
                result = cache_mod.replay_page(sp)
                live = detect_blocks(sp.primitives, result.layout, result.markers)
            assert result.blocks == ruler
            if len(live.solutions) + len(live.panels) < len(ruler.solutions) + len(ruler.panels):
                fewer += 1
        assert fewer > 0


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


class TestScreenReuse:
    def _log(self, tmp_path, baseline, spans):
        import json
        path = tmp_path / "screen.json"
        path.write_text(json.dumps({
            "baselineLoss": baseline,
            "screen": [{"knob": k, "span": v} for k, v in spans.items()],
        }))
        return fit_mod.read_screen(str(path))

    def test_the_ranking_is_the_screens_own(self, tmp_path):
        spans = {k.key: 0.0 for k in TUNABLE}
        spans[TUNABLE[3].key] = 0.5
        spans[TUNABLE[1].key] = 2.0
        spans[TUNABLE[7].key] = 0.5
        movers = fit_mod.screened_movers(self._log(tmp_path, 5.0, spans), 5.0)
        assert [k.key for k in movers] == [TUNABLE[1].key, TUNABLE[3].key, TUNABLE[7].key]

    def test_a_screen_from_another_start_is_refused(self, tmp_path):
        spans = {k.key: 1.0 for k in TUNABLE}
        with pytest.raises(SystemExit):
            fit_mod.screened_movers(self._log(tmp_path, 5.0, spans), 4.0)

    def test_a_screen_that_ran_out_of_time_is_refused(self, tmp_path):
        spans = {k.key: 1.0 for k in TUNABLE[:-1]}
        with pytest.raises(SystemExit):
            fit_mod.screened_movers(self._log(tmp_path, 5.0, spans), 5.0)


class TestFinalReport:
    def _book(self, book_id, matched, oges=10):
        return obj.BookScore(
            book_id=book_id, pages=10, regions=10, oges=oges, matched=matched,
            panels_cut=0, solutions_cut=0, overlaps=0, tall=0, slivers=0,
            pages_with_regions=8,
        )

    def test_per_book_match_is_reported(self):
        before = [self._book("aaaaaaaa", 9), self._book("bbbbbbbb", 0, oges=0)]
        after = [self._book("aaaaaaaa", 7), self._book("bbbbbbbb", 0, oges=0)]
        text = fit_mod._compare({}, {}, before, after)
        assert "0.900 -> 0.700 (-0.200)" in text
        assert "no manifest" in text


class TestScreenResume:
    class _Pool:
        def __init__(self):
            self.calls = 0

        def evaluate(self, profile, books, weights=None):
            self.calls += 1
            return obj.Evaluation(profile_hash="x", loss=4.0)

    def test_a_resumed_screen_probes_only_the_rest(self):
        run = fit_mod.Run()
        run.baseline_loss = 5.0
        run.screen = [{"knob": k.key, "span": 0.0} for k in TUNABLE[:-2]]
        run.screen[0]["span"] = 3.0
        pool = self._Pool()
        movers = fit_mod.screen(pool, DEFAULTS, [], run, fit_mod.Budget(), verbose=False)
        assert 1 <= pool.calls <= 4
        assert {row["knob"] for row in run.screen} == {k.key for k in TUNABLE}
        assert movers[0].key == TUNABLE[0].key
        assert {k.key for k in movers[1:]} <= {k.key for k in TUNABLE[-2:]}

    def test_the_log_is_read_before_the_run_overwrites_it(self, tmp_path):
        import json
        path = tmp_path / "screen.json"
        path.write_text(json.dumps({
            "baselineLoss": 5.0, "evaluations": 2, "trials": [],
            "screen": [{"knob": k.key, "span": 0.0} for k in TUNABLE[:-2]],
        }))
        body = fit_mod.read_screen(str(path))
        run = fit_mod.Run(log_path=str(path))
        run.baseline_loss = 5.0
        run.save()                      # what `main` does before screening
        fit_mod.resume_screen(body, 5.0, run)
        assert len(run.screen) == len(TUNABLE) - 2


class TestTheFitKeepsTheGates:
    def _ev(self, loss, regions):
        ev = obj.Evaluation(profile_hash="x", loss=loss)
        ev.books = [obj.BookScore(
            book_id="aaaaaaaa", pages=10, regions=regions, oges=0, matched=0,
            panels_cut=0, solutions_cut=0, overlaps=0, tall=0, slivers=0,
            pages_with_regions=8,
        )]
        return ev

    def test_a_better_loss_that_fails_a_gate_is_not_kept(self):
        run = fit_mod.Run()
        run.best_loss = 5.0
        run.gate_against = self._ev(5.0, 100)
        assert not run.note("descent", DEFAULTS, self._ev(1.0, 70), 0.0)
        assert run.best_loss == 5.0
        assert run.trials[-1].gates and run.trials[-1].gates[0]["gate"] == "regions"
        assert run.note("descent", DEFAULTS, self._ev(4.0, 90), 0.0)
        assert run.best_loss == 4.0

    def test_an_infeasible_candidate_ranks_below_every_feasible_one(self):
        run = fit_mod.Run()
        run.gate_against = self._ev(5.0, 100)
        assert run.fitness(self._ev(-10.0, 50)) > run.fitness(self._ev(30.0, 100))
