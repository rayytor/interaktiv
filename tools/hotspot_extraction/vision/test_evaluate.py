"""Phase 4 tests for the scoring in evaluate.py (no GPU, no model). Run with the vision venv from the project root:

    .venv-vision/bin/python -m pytest tools/hotspot_extraction/vision/test_evaluate.py -q
"""
import pytest

from tools.hotspot_extraction.vision import evaluate

A, B, FAR = (0, 0, 100, 100), (200, 0, 300, 100), (500, 500, 600, 600)


def test_a_teacher_box_is_matched_once():
    hits = evaluate.match_page([A], [(0.9, A), (0.8, A)], 0.5)
    assert hits == [(0.9, True), (0.8, False)]


def test_perfect_predictions_score_one():
    pages = [{"book": "x", "gt": [A, B], "pred": [(0.9, A), (0.8, B)]}, {"book": "x", "gt": [], "pred": []}]
    s = evaluate.score_pages(pages, 0.5)
    assert s["mAP50"] == pytest.approx(1.0) and s["mAP50_95"] == pytest.approx(1.0)
    assert s["precision"] == 1.0 and s["recall"] == 1.0
    assert s["empty_pages"] == 1 and s["empty_pages_with_a_box"] == 0


def test_a_confident_wrong_box_costs_precision_and_a_miss_costs_recall():
    pages = [{"book": "x", "gt": [A, B], "pred": [(0.95, FAR), (0.9, A), (0.2, B)]}]
    s = evaluate.score_pages(pages, 0.5)
    assert s["precision"] == 0.5 and s["recall"] == 0.5         # B is below the confidence
    assert 0.0 < s["mAP50"] < 1.0


def test_a_box_on_an_empty_page_is_counted():
    pages = [{"book": "x", "gt": [], "pred": [(0.7, A)]}, {"book": "y", "gt": [A], "pred": [(0.7, A)]}]
    summary = evaluate.summarize(pages, 0.5)
    assert summary["books"]["x"]["empty_pages_with_a_box"] == 1
    assert summary["books"]["x"]["mAP50"] is None
    assert summary["total"]["precision"] == 0.5


def test_a_loose_box_passes_iou_50_but_not_90():
    loose = (0, 0, 100, 70)
    pages = [{"book": "x", "gt": [A], "pred": [(0.9, loose)]}]
    assert evaluate.average_precision(pages, 0.5) == pytest.approx(1.0)
    assert evaluate.average_precision(pages, 0.9) == 0.0
