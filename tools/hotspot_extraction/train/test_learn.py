"""
Tests for the learned ranker's dataset and baseline.

The first verdict on the ranker was built on a label leak: a region grown from
a manifest entry's icon was a positive by construction, and `is_anchored` told
the model so. These guard the three things that closed it -- no anchored region
in the dataset, no anchored feature, and a baseline that is the detector's own
choice rather than a straw man.
"""

import math
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.hotspot_extraction.scanner.profile import DEFAULTS, apply_profile
from tools.hotspot_extraction.train import cache as cache_mod
from tools.hotspot_extraction.train import learn
from tools.hotspot_extraction.train.features import FEATURE_NAMES, N_FEATURES


def _ds(pages):
    """`pages` is a list of sheets, each a list of (label, cost, model_score) rows."""
    ds = learn.Dataset()
    for p, rows in enumerate(pages, start=1):
        for y, cost, score in rows:
            # One feature beside the bias; the model below reads it as the score.
            ds.x.append([1.0, score] + [0.0] * (N_FEATURES - 2))
            ds.y.append(y)
            ds.book.append("bk")
            ds.page.append(p)
            ds.cost.append(cost)
    return ds


def _model_reading_feature_1():
    return learn.LogisticModel(weights=[0.0, 1.0] + [0.0] * (N_FEATURES - 2))


class TestNoLeak:
    def test_the_anchored_flag_is_not_a_feature(self):
        assert "is_anchored" not in FEATURE_NAMES
        assert learn.MODEL_VERSION >= 2

    @pytest.mark.parametrize("act, anchored", [
        ({"id": "p5-a"}, False),
        ({"id": "p5-a", "anchored": True}, True),
        ({"id": "p5-a", "ogeId": "123"}, True),
        ({"id": "p5-oge-123"}, True),
        ({"id": "p50-oge-123"}, False),   # another sheet's prefix is not this one's
    ])
    def test_what_counts_as_anchored(self, act, anchored):
        assert learn._is_anchored(act, 5) is anchored

    @pytest.mark.skipif(not cache_mod.cached_books(), reason="no cache built")
    def test_a_real_book_yields_no_anchored_examples(self):
        from tools.hotspot_extraction.train import splits as splits_mod
        cached = set(cache_mod.cached_books())
        with_manifest = [
            b for b in splits_mod.train_ids()
            if b in cached and learn.interactive_oges(b)
        ]
        book = with_manifest[0]
        try:
            res = learn.examples_for_book({
                "book_id": book, "cache_dir": cache_mod.DEFAULT_CACHE_DIR,
                "profile": DEFAULTS.to_dict(),
            })
        finally:
            apply_profile(DEFAULTS)
        assert res["anchored_dropped"] == 0
        assert len(res["x"]) == len(res["y"]) == len(res["cost"]) > 0
        assert all(len(x) == N_FEATURES for x in res["x"])
        assert 0 < sum(res["y"]) < len(res["y"])
        # A positive was linked to an icon, so it has a finite cost to one.
        assert all(math.isfinite(c) for c, y in zip(res["cost"], res["y"]) if y)


class TestBaseline:
    def test_the_baseline_picks_the_cheapest_candidate(self):
        ds = _ds([
            [(1, 1.0, 0.0), (0, 9.0, 0.0)],   # cheapest is marked
            [(0, 1.0, 0.0), (1, 9.0, 0.0)],   # cheapest is not
        ])
        assert learn.baseline_top1(ds) == 0.5

    def test_sheets_without_exactly_one_mark_are_not_scored(self):
        ds = _ds([
            [(1, 1.0, 0.0), (1, 9.0, 0.0)],   # two marked
            [(0, 1.0, 0.0), (0, 9.0, 0.0)],   # none marked
            [(1, 1.0, 0.0)],                  # no contest
        ])
        assert learn.baseline_top1(ds) is None
        assert learn.usable_pages(ds) == 0

    def test_contested_sheets_are_those_drift_cannot_separate(self):
        close = learn.CONTESTED / 2
        ds = _ds([
            [(0, 1.0, 0.9), (1, 1.0 + close, 0.1)],   # contested; drift wrong, model wrong
            [(0, 1.0, 0.1), (1, 1.0 + close, 0.9)],   # contested; drift wrong, model right
            [(1, 1.0, 0.1), (0, 20.0, 0.9)],          # clear; drift right, model wrong
        ])
        model = _model_reading_feature_1()
        assert learn.usable_pages(ds, only_contested=True) == 2
        assert learn.baseline_top1(ds, only_contested=True) == 0.0
        assert learn.top1_accuracy(model, ds, only_contested=True) == 0.5
        assert learn.baseline_top1(ds) == pytest.approx(1 / 3)
        assert learn.top1_accuracy(model, ds) == pytest.approx(1 / 3)
