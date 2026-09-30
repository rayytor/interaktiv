"""Phase 2 tests for select.py, teacher.py, qa.py. Run with the vision venv from the project root:

    .venv-vision/bin/python -m pytest tools/hotspot_extraction/vision/test_phase2.py -q
"""
import json
from pathlib import Path

import pytest

from tools.hotspot_extraction.vision import qa, select, teacher

RUNS = Path(__file__).resolve().parent / "runs"
INDEX31 = {"page": 31, "width_pt": 569.764, "height_pt": 796.535, "width_px": 1024, "height_px": 1432}


# ----------------------------------------------------------------- select.py

def _index(n, spread_first=True):
    out = []
    for p in range(1, n + 1):
        w = 1148.17 if (p == 1 and spread_first) else 569.764
        out.append({"page": p, "width_pt": w, "height_pt": 796.535, "width_px": 1024, "height_px": 1432})
    return out


def _bake(pages_with_regions, folio):
    return {"pages": {str(p): {"activities": [{"id": f"p{p}-a"}]} for p in pages_with_regions},
            "folio": {"byPage": {str(k): v for k, v in folio.items()}}}


def test_strata_partition_and_spread_excluded():
    index = _index(20)
    bake = _bake([2, 3, 4, 5, 6], {})
    strata = select.stratify(index, bake, {3: 1, 10: 2})
    assert {p for p, _ in strata["manifest"]} == {3, 10}
    assert {p for p, _ in strata["baked"]} == {2, 4, 5, 6}
    total = sum(len(v) for v in strata.values())
    assert total == 19                       # the spread on page 1 is never selectable
    assert 1 not in {p for v in strata.values() for p, _ in v}


def test_sample_is_seeded_and_tops_up():
    strata = {"baked": [(p, "b") for p in range(2, 60)], "manifest": [(70, "m")], "empty": [(80, "e"), (81, "e")]}
    a = select.sample_book("book-x", strata, 45, 1)
    b = select.sample_book("book-x", strata, 45, 1)
    c = select.sample_book("book-x", strata, 45, 2)
    assert a == b and a != c
    assert len(a) == 45
    assert sum(1 for r in a if r["stratum"] == "manifest") == 1
    assert sum(1 for r in a if r["stratum"] == "empty") == 2
    # 70/15/15 of 45 rounds to 32/7/7, settled to 31/7/7; manifest has 1, empty has 2 -> 11 top-ups
    assert sum(1 for r in a if "top-up" in r["why"]) == 45 - 31 - 1 - 2
    assert len({r["page"] for r in a}) == 45


# ---------------------------------------------------------------- teacher.py

@pytest.mark.parametrize("text", [
    '[{"box_2d": [1, 2, 3, 4], "label": "a"}]',
    'Sure.\n```json\n[{"box_2d": [1, 2, 3, 4], "label": "a"}]\n```\nDone.',
    'Here: [{"box_2d": [1, 2, 3, 4], "label": "a"}] end',
    '{"activities": [{"box_2d": [1, 2, 3, 4], "label": 3}]}',
])
def test_parse_boxes_tolerates_prose_and_fences(text):
    boxes = teacher.parse_boxes(text)
    assert len(boxes) == 1 and boxes[0]["box_2d"] == [1, 2, 3, 4]


def test_parse_boxes_rejects_bad_boxes():
    assert teacher.parse_boxes("[]") == []
    for bad in ("no json here", '[{"box_2d": [5, 5, 1, 9]}]', '[{"box_2d": [0, 0, 1200, 1]}]', '[{"label": "a"}]'):
        with pytest.raises(teacher.ParseError):
            teacher.parse_boxes(bad)


def test_box_conversion_matches_phase0_drawing():
    px = teacher.box_to_pixels([116, 91, 335, 936], 1024, 1432)
    assert px == [93.2, 166.1, 958.5, 479.7]
    rect = teacher.pixels_to_points(px, INDEX31)
    # y up: bottom edge below top edge, inside the page, x in points
    assert 0 < rect[0] < rect[2] < INDEX31["width_pt"]
    assert 0 < rect[1] < rect[3] < INDEX31["height_pt"]
    assert rect[3] == pytest.approx(INDEX31["height_pt"] - 166.1 / (1432 / 796.535), abs=0.01)
    assert rect[0] == pytest.approx(93.2 / (1024 / 569.764), abs=0.01)


def test_prompt_version_and_hint(tmp_path):
    version, body, sha = teacher.load_prompt()
    assert version.startswith("v") and "box_2d" in body and len(sha) == 12
    p = teacher.build_prompt(body, teacher.hint_line(3))
    assert "attached image" in p and body in p and "lists 3 interactive activities" in p
    assert teacher.hint_line(0) is None
    bad = tmp_path / "p.md"
    bad.write_text("no version line\n")
    with pytest.raises(ValueError):
        teacher.load_prompt(bad)


def test_sample_pages_is_seeded():
    pages = [{"book_id": "b", "page": p} for p in range(1, 101)]
    a = teacher.sample_pages(pages, 0.1, 7)
    assert len(a) == 10 and a == teacher.sample_pages(pages, 0.1, 7) and a != teacher.sample_pages(pages, 0.1, 8)


def test_requests_today_counts_only_today(tmp_path):
    log = tmp_path / "requests.jsonl"
    teacher.log_request({"ts": "2026-09-28 10:00:00", "route": "api", "key": 0, "page": 1}, log)
    teacher.log_request({"ts": "2026-09-27 10:00:00", "route": "api", "key": 0, "page": 2}, log)
    assert teacher.requests_today(log, "2026-09-28") == 1


# --------------------------------------------------------------------- qa.py

class _AnyPage(dict):
    """`oges_by_page` stand-in: the same icons on every page."""
    def __init__(self, n):
        super().__init__()
        self._n = n

    def get(self, page, default=None):
        return [{} for _ in range(self._n)]


class FakeCtx:
    def __init__(self, diag=None, icons=None):
        self._diag = diag
        self._icons = icons or []
        self.oges_by_page = _AnyPage(len(self._icons))

    def page_diag(self, page):
        return self._diag

    def icons(self, page, w, h):
        return [{"id": str(i), "title": f"icon{i}", "x": x, "y": y} for i, (x, y) in enumerate(self._icons)]


def _label(rects, labels=None):
    return {"book": "b", "page": 31, "index": INDEX31,
            "boxes": [{"label": (labels[i] if labels else None), "rect": list(r), "px": [0, 0, 1, 1]} for i, r in enumerate(rects)]}


def test_judge_clean_page_is_trusted():
    v = qa.judge_page(_label([(40, 600, 340, 700), (40, 300, 340, 590)], ["a", "b"]), FakeCtx())
    assert v["passes"] and v["defects"] == [] and v["labels_ok"] and v["overlap_major"] == 0


def test_judge_empty_page_without_icons_is_trusted():
    assert qa.judge_page(_label([]), FakeCtx())["passes"]


def test_judge_finds_sliver_overlap_miss_and_cut():
    diag = {"panels": [(30, 320, 520, 620)], "solutions": [(360, 200, 480, 240)], "markers": []}
    ctx = FakeCtx(diag=diag, icons=[(50, 690), (300, 100)])
    v = qa.judge_page(_label([
        (40, 600, 340, 700),      # covers icon (50, 690)
        (40, 400, 340, 700),      # major overlap with the first; cuts the panel (320..620)
        (300, 210, 500, 215),     # sliver (5 pt tall); cuts the solution block (12.5 % of it)
    ]), ctx)
    assert v["sliver"] == 1 and v["overlap_major"] >= 1 and v["miss"] == 1 and v["cut"] >= 2
    assert v["missed_icons"] == ["icon1"]
    assert set(v["defects"]) == {"sliver", "overlap", "miss"} and not v["passes"]


def test_minor_overlap_is_not_a_defect():
    v = qa.judge_page(_label([(40, 600, 340, 700), (40, 500, 340, 603)]), FakeCtx())
    assert v["overlap_minor"] == 1 and v["overlap_major"] == 0 and v["passes"]


def test_icon_band():
    ctx = FakeCtx(icons=[(10, 710)])          # 30 pt left of the box and 10 pt above its top: the margin icon
    assert qa.judge_page(_label([(40, 600, 340, 700)]), ctx, band=16, xband=0.10)["miss"] == 0
    assert qa.judge_page(_label([(40, 600, 340, 700)]), ctx, band=4, xband=0.10)["miss"] == 1
    assert qa.judge_page(_label([(40, 600, 340, 700)]), ctx, band=16, xband=0.01)["miss"] == 1


def test_label_run():
    assert qa.label_run_ok(["a", "b", "c"]) == (True, 0)
    assert qa.label_run_ok(["a", "a", "b"]) == (True, 1)
    assert qa.label_run_ok(["1", "2", "3"]) == (True, 0)
    assert qa.label_run_ok([None, None]) == (True, 0)
    assert qa.label_run_ok(["a", "c"]) == (False, 0)
    assert qa.label_run_ok(["Q1", "b"]) == (False, 0)


def test_agreement_greedy_iou():
    a = [[0, 0, 100, 100], [200, 200, 300, 300]]
    b = [[5, 5, 100, 100], [500, 500, 600, 600]]
    agree, m = qa.page_agreement(a, b)
    assert m == 1 and agree == pytest.approx(0.5)
    assert qa.page_agreement([], []) == (1.0, 0)
    assert qa.page_agreement(a, []) == (0.0, 0)


def test_gate_verdicts_with_reask(tmp_path):
    prim = tmp_path / "v1"
    re = tmp_path / "v1-reask"
    ctx_icons = {}

    def fake_ctx(book_id, root, bakes_dir):
        return FakeCtx(icons=[(50, 690)])
    orig = qa.BookContext
    qa.BookContext = fake_ctx           # type: ignore[assignment]
    try:
        teacher.write_json_atomic(prim / "b" / "0031.json", _label([(40, 600, 340, 700)]))            # trusted
        teacher.write_json_atomic(prim / "b" / "0032.json", _label([(300, 100, 340, 200)]))           # miss -> re-ask
        teacher.write_json_atomic(prim / "b" / "0033.json", _label([(300, 100, 340, 200)]))           # miss, re-asked ok
        teacher.write_json_atomic(re / "b" / "0033.json", _label([(40, 600, 340, 700)]))
        teacher.write_json_atomic(prim / "b" / "0034.json", _label([(300, 100, 340, 200)]))           # miss, re-ask still fails
        teacher.write_json_atomic(re / "b" / "0034.json", _label([(300, 100, 340, 200)]))
        (prim / "b" / "0035.failed").write_text("timeout\n")                                          # failed -> re-ask
        verdicts = qa.run_gate(prim, re, None)
    finally:
        qa.BookContext = orig
    by_page = {v["page"]: v for v in verdicts}
    assert by_page[31]["verdict"] == "trusted" and by_page[31]["source"] == "primary"
    assert by_page[32]["verdict"] == "re-ask" and by_page[32]["hint_count"] == 1
    assert by_page[33]["verdict"] == "trusted" and by_page[33]["source"] == "reask"
    assert by_page[34]["verdict"] == "rejected"
    assert by_page[35]["verdict"] == "re-ask" and by_page[35]["request_failed"]
    s = qa.summarize(verdicts)
    assert s["total"]["trusted"] == 2 and s["total"]["rejected"] == 1 and s["total"]["re-ask"] == 2
    out = tmp_path / "reask.json"
    assert qa.write_reask_selection(verdicts, None, out) == 2
    sel = json.load(open(out))
    assert [p["page"] for p in sel["books"]["b"]["pages"]] == [32, 35]
    assert sel["books"]["b"]["pages"][0]["hint_count"] == 1


# ---------------------------------------------------------------- route api

def test_key_pool_paces_and_retires():
    import threading
    pool = teacher.KeyPool(["k0", "k1"], rpm=6000, stop=threading.Event())   # 10 ms interval
    first = [pool.acquire() for _ in range(4)]
    assert sorted(first) == [0, 0, 1, 1]                 # round-robin by idle time
    pool.retire(0)
    assert pool.live == 1 and all(pool.acquire() == 1 for _ in range(3))
    pool.retire(1)
    assert pool.acquire() is None                       # every key spent -> quota stop


def test_key_pool_cooldown_delays_that_key():
    import threading, time
    pool = teacher.KeyPool(["k0", "k1"], rpm=6000, stop=threading.Event())
    pool.cooldown(0, 60)
    t0 = time.time()
    got = [pool.acquire() for _ in range(3)]
    assert got == [1, 1, 1] and time.time() - t0 < 1.0


def test_classify_api_error():
    class E(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code, self.message = code, message
    assert teacher.classify_api_error(E(429, "RESOURCE_EXHAUSTED: quota metric GenerateRequestsPerDayPerProjectPerModel"))[0] == "daily"
    kind, wait = teacher.classify_api_error(E(429, "Resource exhausted. Please retry in 17.3s."))
    assert kind == "rate" and wait == pytest.approx(17.3)
    e = E(429, "You exceeded your current quota. Please retry in 6.8s.")
    e.details = {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaValue": "20"}]}]}}
    assert teacher.classify_api_error(e)[0] == "daily"          # the real free-tier error: per-day id only in details
    assert teacher.classify_api_error(E(503, "UNAVAILABLE: overloaded"))[0] == "transient"
    assert teacher.classify_api_error(E(400, "Invalid JSON schema"))[0] == "bad-request"
    assert teacher.classify_api_error(RuntimeError("boom"))[0] == "error"


def test_api_keys(tmp_path):
    f = tmp_path / "api.md"
    f.write_text("# comment\nAQ.key-one\n\nAQ.key-two")
    assert teacher.load_api_keys(f) == ["AQ.key-one", "AQ.key-two"]
    with pytest.raises(FileNotFoundError):
        teacher.load_api_keys(tmp_path / "missing.md")


def test_requests_today_by_key_counts_api_lines_only(tmp_path):
    log = tmp_path / "requests.jsonl"
    teacher.log_request({"ts": "2026-09-28 10:00:00", "route": "api", "key": 3}, log)
    teacher.log_request({"ts": "2026-09-28 10:00:01", "route": "api", "key": 3}, log)
    teacher.log_request({"ts": "2026-09-28 10:00:02", "book": "b"}, log)              # an old CLI-route line
    assert teacher.requests_today_by_key(log, "2026-09-28") == {3: 2}
    assert teacher.requests_today(log, "2026-09-28") == 2
