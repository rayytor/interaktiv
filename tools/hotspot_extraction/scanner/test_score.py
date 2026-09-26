"""
Tests for the hotspot scorer (`scanner/score.py`).

These exist because the scorer replaced a JavaScript one that was deleted once
this module reproduced its numbers, so the diff that proved the port correct is
no longer reproducible. What that diff established is asserted here instead:

1. **Denominator integrity.** Every manifest entry lands in exactly one bucket
   and the buckets sum to the manifest size. This is the property the original
   rate lacked -- it was computed over the entries whose printed page had
   already resolved, so a folio failure removed an entry from the denominator
   rather than counting against it, and the two largest classes of failure were
   invisible by construction.
2. **A missing or stale sidecar refuses to score.** Without the drawn geometry
   every violation check silently passes and a book scores as clean, which is
   worse than not scoring it because it looks like a result.
3. **The violation rules themselves** -- whole-or-nothing, overlap, tall,
   sliver -- on geometry small enough to check by hand.

The rules are imported from `scanner.regions`, not restated, so a threshold
that moves there moves here too. That is the point of the port.
"""

import gzip
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from tools.hotspot_extraction.scanner.regions import (
    MIN_HOTSPOT,
    TALL_REGION,
    WHOLE_TOL,
)
from tools.hotspot_extraction.scanner.score import (
    BUCKETS,
    SCORER,
    Tally,
    bucket_for,
    baked_book_ids,
    finish_row,
    interactive_oges,
    page_list,
    quantiles,
    score_baked,
    score_catalogue,
    takeable,
    tally_page,
)

PAGE_W, PAGE_H = 600.0, 800.0


def _page(activities, panels=(), solutions=(), markers=()):
    """A synthetic sheet in the shape `tally_page` consumes."""
    res = {
        "pageWidth": PAGE_W,
        "pageHeight": PAGE_H,
        "confidence": "strong",
        "activities": list(activities),
    }
    diag = {
        "panels": [tuple(map(float, r)) for r in panels],
        "solutions": [tuple(map(float, r)) for r in solutions],
        "markers": [tuple(map(float, r)) for r in markers],
    }
    return res, diag


def _act(act_id, rect, label=None, parts=None, items=None, anchored=False, oge_id=None):
    a = {
        "id": act_id,
        "label": label,
        "rect": tuple(map(float, rect)),
        "parts": [tuple(map(float, p)) for p in (parts or [rect])],
        "items": items or [],
    }
    if anchored:
        a["anchored"] = True
    if oge_id:
        a["ogeId"] = oge_id
    return a


# ----------------------------------------------------------- violation rules


class TestWholeOrNothing:
    """A region takes a drawn block whole or leaves it alone."""

    BLOCK = (100.0, 100.0, 300.0, 200.0)   # 200 x 100

    def test_a_region_that_takes_the_block_whole_is_not_a_cut(self):
        res, diag = _page([_act("p1-a", (90, 90, 310, 210))], solutions=[self.BLOCK])
        t = Tally()
        tally_page(t, 1, res, diag, [], set())
        assert t.solutions_cut == 0

    def test_a_region_that_misses_the_block_entirely_is_not_a_cut(self):
        res, diag = _page([_act("p1-a", (350, 100, 500, 200))], solutions=[self.BLOCK])
        t = Tally()
        tally_page(t, 1, res, diag, [], set())
        assert t.solutions_cut == 0

    def test_a_region_whose_edge_lands_inside_the_block_is_a_cut(self):
        # Covers the left half: share 0.5, comfortably inside the tolerance band.
        res, diag = _page([_act("p1-a", (50, 100, 200, 200))], solutions=[self.BLOCK])
        t = Tally()
        tally_page(t, 1, res, diag, [], set())
        assert t.solutions_cut == 1

    def test_a_rounded_corner_grazing_the_block_is_not_a_cut(self):
        # Under WHOLE_TOL of the block's area: not a sliced panel.
        graze = (100.0, 100.0, 100.0 + 200.0 * (WHOLE_TOL / 2), 200.0)
        res, diag = _page([_act("p1-a", graze)], solutions=[self.BLOCK])
        t = Tally()
        tally_page(t, 1, res, diag, [], set())
        assert t.solutions_cut == 0

    def test_panels_are_counted_separately_from_solutions(self):
        res, diag = _page([_act("p1-a", (50, 100, 200, 200))], panels=[self.BLOCK])
        t = Tally()
        tally_page(t, 1, res, diag, [], set())
        assert (t.panels_cut, t.solutions_cut) == (1, 0)


class TestTakeable:
    def test_geometry_reaching_off_the_sheet_is_dropped(self):
        # A path bbox recorded past the sheet edge is an artefact, not a block.
        off = (100.0, 100.0, PAGE_W + 50.0, 200.0)
        assert takeable([off], (0, 0, PAGE_W, PAGE_H), PAGE_W, PAGE_H, False) == []

    def test_require_length_keeps_only_blocks_the_region_runs_alongside(self):
        rect = (0.0, 100.0, 50.0, 140.0)          # 40 pt tall
        overlapping = (100.0, 100.0, 300.0, 140.0)  # fully alongside
        past = (100.0, 300.0, 300.0, 400.0)         # nowhere near
        kept = takeable([overlapping, past], rect, PAGE_W, PAGE_H, True)
        assert kept == [overlapping]

    def test_without_require_length_every_on_sheet_block_is_kept(self):
        rect = (0.0, 100.0, 50.0, 140.0)
        blocks = [(100.0, 100.0, 300.0, 140.0), (100.0, 300.0, 300.0, 400.0)]
        assert takeable(blocks, rect, PAGE_W, PAGE_H, False) == blocks


class TestGeometryRules:
    def test_overlapping_parts_are_counted_once_per_pair(self):
        res, diag = _page([
            _act("p1-a", (100, 100, 300, 200)),
            _act("p1-b", (250, 150, 400, 250)),
        ])
        t = Tally()
        tally_page(t, 1, res, diag, [], set())
        assert t.overlaps == 1

    def test_regions_that_merely_touch_do_not_overlap(self):
        res, diag = _page([
            _act("p1-a", (100, 100, 300, 200)),
            _act("p1-b", (300, 100, 400, 200)),
        ])
        t = Tally()
        tally_page(t, 1, res, diag, [], set())
        assert t.overlaps == 0

    def test_a_region_taller_than_the_threshold_is_tall(self):
        h = TALL_REGION * PAGE_H + 10
        res, diag = _page([_act("p1-a", (100, 10, 300, 10 + h))])
        t = Tally()
        tally_page(t, 1, res, diag, [], set())
        assert t.tall == 1

    def test_a_region_under_the_minimum_in_either_dimension_is_a_sliver(self):
        thin = MIN_HOTSPOT / 2
        res, diag = _page([
            _act("p1-a", (100, 100, 100 + thin, 300)),   # too narrow
            _act("p1-b", (200, 100, 400, 100 + thin)),   # too short
        ])
        t = Tally()
        tally_page(t, 1, res, diag, [], set())
        assert t.slivers == 2

    def test_every_part_of_a_multi_part_region_is_checked(self):
        # A region's continuation band in the next column is its own rectangle,
        # and a violation in it is still a violation.
        block = (400.0, 100.0, 500.0, 200.0)
        act = _act("p1-a", (50, 100, 200, 300),
                   parts=[(50, 100, 200, 300), (380, 100, 450, 200)])
        res, diag = _page([act], solutions=[block])
        t = Tally()
        tally_page(t, 1, res, diag, [], set())
        assert t.parts == 2
        assert t.solutions_cut == 1


# ------------------------------------------------------------------ buckets


class TestDenominatorIntegrity:
    """
    Every manifest entry is filed exactly once, whatever happened to it.

    This is the property the original match rate lacked: entries whose printed
    page resolved to no sheet were dropped from the denominator instead of
    counting against the score.
    """

    def _row(self, oges, bucket_of, pages_for_printed=None):
        t = Tally()
        t.bucket_of = dict(bucket_of)
        return finish_row(t, {
            "file": "x.pdf", "bookId": "x", "pages": 10, "oges": oges,
            "pagesForPrinted": pages_for_printed or {}, "pagesSpec": None,
            "folioCollisions": 0, "source": "test",
        })

    def test_unfiled_entries_become_unresolved_page_not_nothing(self):
        oges = [{"id": "a", "sayfano": 4}, {"id": "b", "sayfano": 9}]
        row = self._row(oges, {"a": "ok"})
        assert row["buckets"]["unresolved-page"] == 1
        assert sum(row["buckets"].values()) == len(oges)

    def test_buckets_always_sum_to_the_manifest_size(self):
        oges = [{"id": str(i), "sayfano": i} for i in range(1, 8)]
        row = self._row(oges, {"1": "ok", "2": "ok", "3": "rule-violation",
                               "4": "page-blank", "5": "drift"})
        assert sum(row["buckets"].values()) == 7
        assert row["join"]["oges"] == 7

    def test_match_rate_is_ok_over_the_whole_manifest(self):
        oges = [{"id": str(i), "sayfano": i} for i in range(1, 5)]
        row = self._row(oges, {"1": "ok", "2": "ok", "3": "drift", "4": "page-blank"})
        assert row["join"]["matched"] == 2
        assert row["join"]["matchRate"] == 0.5

    def test_a_book_with_no_manifest_entries_has_no_rate_rather_than_zero(self):
        row = self._row([], {})
        assert row["join"]["matchRate"] is None

    def test_out_of_frame_entries_are_page_not_read_only_on_a_slice(self):
        oges = [{"id": "far", "sayfano": 99}]
        row = self._row(oges, {}, pages_for_printed={5: [5], 6: [6]})
        assert row["buckets"]["unresolved-page"] == 1
        row_sliced = finish_row(
            Tally(),
            {"file": "x.pdf", "bookId": "x", "pages": 2, "oges": oges,
             "pagesForPrinted": {5: [5], 6: [6]}, "pagesSpec": "5-6",
             "folioCollisions": 0, "source": "test"},
        )
        assert row_sliced["buckets"]["page-not-read"] == 1

    def test_every_bucket_name_is_declared(self):
        row = self._row([{"id": "a", "sayfano": 1}], {"a": "ok"})
        assert set(row["buckets"]) >= set(BUCKETS)


class TestBucketFor:
    def test_no_explanation_at_all_is_a_blank_page(self):
        assert bucket_for(None) == "page-blank"

    def test_a_candidate_another_entry_won_is_contested(self):
        assert bucket_for({"candidates": 2, "reasons": {"drift"}}) == "contested"

    def test_the_nearest_miss_wins_over_a_further_one(self):
        # LINK_REASONS is ordered nearest-miss first: an entry rejected on drift
        # by one region and on its label by another is a drift problem.
        why = {"candidates": 0, "reasons": {"drift", "label-mismatch"}}
        assert bucket_for(why) == "label-mismatch"


# ------------------------------------------------------------------- inputs


class TestPageList:
    def test_no_spec_is_the_whole_book(self):
        assert page_list(None, 4) == [1, 2, 3, 4]

    def test_ranges_and_singles_and_commas(self):
        assert page_list("2-4,7", 10) == [2, 3, 4, 7]

    def test_pages_outside_the_book_are_dropped(self):
        assert page_list("8-12", 10) == [8, 9, 10]


class TestQuantiles:
    def test_nothing_measured_is_none_not_zero(self):
        assert quantiles([]) is None

    def test_the_max_is_the_largest_value(self):
        assert quantiles([1.0, 2.0, 9.0])["max"] == 9.0


# ---------------------------------------------------- scoring a real bake

BAKED = baked_book_ids()


@pytest.mark.skipif(not BAKED, reason="no bakes on disk")
class TestScoreBaked:
    def test_a_bake_scores_and_its_buckets_sum_to_its_manifest(self):
        for book_id in BAKED[:6]:
            row = score_baked(book_id)
            n_oges = row["join"]["oges"]
            assert sum(row["buckets"].values()) == n_oges, book_id
            assert row["join"]["matched"] == row["buckets"]["ok"], book_id
            assert len(interactive_oges(book_id)) == n_oges, book_id

    def test_violation_counts_are_never_negative_and_parts_cover_regions(self):
        for book_id in BAKED[:6]:
            row = score_baked(book_id)
            for name, n in row["violations"].items():
                assert n >= 0, (book_id, name)
            assert row["yield"]["parts"] >= row["yield"]["regions"], book_id

    def test_a_missing_sidecar_refuses_to_score_rather_than_scoring_clean(self):
        book_id = BAKED[0]
        src = os.path.join(PROJECT_ROOT, "activities", "books", book_id)
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "activities", "books", book_id)
            os.makedirs(dst)
            shutil.copy(os.path.join(src, "regions.json"),
                        os.path.join(dst, "regions.json"))
            with pytest.raises(FileNotFoundError):
                score_baked(book_id, root=tmp)

    def test_a_stale_sidecar_refuses_to_score(self):
        book_id = BAKED[0]
        src = os.path.join(PROJECT_ROOT, "activities", "books", book_id)
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "activities", "books", book_id)
            os.makedirs(dst)
            shutil.copy(os.path.join(src, "regions.json"),
                        os.path.join(dst, "regions.json"))
            with gzip.open(os.path.join(src, "diagnostics.json.gz"), "rb") as f:
                diag = json.loads(f.read().decode("utf-8"))
            diag["fingerprint"] = "0-deadbeef"
            with gzip.open(os.path.join(dst, "diagnostics.json.gz"), "wb") as f:
                f.write(json.dumps(diag).encode("utf-8"))
            with pytest.raises(ValueError):
                score_baked(book_id, root=tmp)

    def test_scoring_is_repeatable(self):
        book_id = BAKED[0]
        assert score_baked(book_id) == score_baked(book_id)


@pytest.mark.skipif(not BAKED, reason="no bakes on disk")
def test_the_catalogue_scores_every_bake_and_stamps_its_scorer():
    out = score_catalogue(book_ids=BAKED[:4])
    assert out["scorer"] == SCORER
    assert len(out["books"]) == 4
    assert "errors" not in out


def test_an_unbaked_book_is_reported_as_an_error_not_skipped():
    out = score_catalogue(book_ids=["definitely-not-a-book"])
    assert out["books"] == []
    assert out["errors"][0]["bookId"] == "definitely-not-a-book"
