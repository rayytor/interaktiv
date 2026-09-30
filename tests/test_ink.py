"""Book drawings: the 5 MB budget, erasing, and saving."""

import json
import os

import pytest

from interaktiv_core import appdirs
from interaktiv_core.ink import (
    BUDGET_BYTES,
    BookInk,
    EraseSession,
    InkStroke,
    drawings_dir,
)


def stroke(page=0, points=None, kind="pen", t=0, width=2.0):
    return InkStroke(
        page=page,
        kind=kind,
        rgba="#d32f2fff",
        width=width,
        points=points if points is not None else [10, 10, 20, 20, 30, 30],
        created_ms=t,
    )


def long_stroke(t, n=400):
    return stroke(points=[c for i in range(n) for c in (100 + i * 0.37, 200 + i * 0.53)], t=t)


class TestStroke:
    def test_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            stroke(kind="spray")

    def test_rejects_odd_points(self):
        with pytest.raises(ValueError):
            stroke(points=[1, 2, 3])

    def test_rounds_to_a_tenth_of_a_point(self):
        s = stroke(points=[1.234, 5.0, 6.06, 7.99])
        assert s.points == [1.2, 5, 6.1, 8]

    def test_json_round_trip(self):
        s = stroke(kind="rect", points=[0, 0, 50, 40], t=123)
        back = InkStroke.from_json(json.loads(json.dumps(s.to_json())))
        assert back.to_json() == s.to_json()

    def test_rect_outline_is_closed(self):
        outline = stroke(kind="rect", points=[0, 0, 10, 20]).outline()
        assert outline[0] == outline[-1]
        assert len(outline) == 5


class TestBudget:
    def test_size_matches_the_file(self, tmp_path):
        ink = BookInk("b", path=str(tmp_path / "b.json"))
        for t in range(5):
            ink.add(long_stroke(t))
        ink.remove([next(iter(ink._strokes))])
        assert ink.size_bytes() == len(ink.to_bytes())
        ink.save()
        assert os.path.getsize(ink.path) == ink.size_bytes()

    def test_oldest_strokes_go_first(self, tmp_path):
        one = len(long_stroke(0).to_json()["pts"])  # sanity: the strokes are big
        assert one > 100
        budget = BookInk("b").size_bytes() + 3 * long_stroke(0).encoded_size() + 10
        ink = BookInk("b", path=str(tmp_path / "b.json"), budget=budget)
        strokes = [long_stroke(t) for t in range(6)]
        evicted = []
        for s in strokes:
            evicted += ink.add(s)
        assert [s.created_ms for s in evicted] == [0, 1, 2]
        assert sorted(s.created_ms for s in ink._strokes.values()) == [3, 4, 5]
        assert ink.size_bytes() <= budget

    def test_newest_is_kept_even_if_alone_it_is_too_big(self):
        ink = BookInk("b", budget=10)
        ink.add(stroke(t=1))
        newest = stroke(t=2)
        ink.add(newest)
        assert list(ink._strokes) == [newest.id]

    def test_five_megabytes_on_disk(self, tmp_path):
        ink = BookInk("b", path=str(tmp_path / "b.json"))
        t = 0
        while t < 100000:
            ink.add(long_stroke(t, n=2000))
            t += 1
            if ink.size_bytes() > BUDGET_BYTES - 40000:
                break
        # Go well past the budget: the file never exceeds it.
        for extra in range(20):
            evicted = ink.add(long_stroke(t + extra, n=2000))
        assert evicted, "adding past 5 MB deletes old strokes"
        assert min(s.created_ms for s in ink._strokes.values()) > 0
        ink.save()
        assert os.path.getsize(ink.path) <= BUDGET_BYTES


class TestErase:
    def test_splits_a_line_in_two(self):
        ink = BookInk("b")
        line = stroke(points=[c for x in range(0, 101, 5) for c in (x, 50)])
        ink.add(line)
        removed, added = ink.erase(0, [(50, 40), (50, 60)], radius=4)
        assert removed == [line]
        assert len(added) == 2
        left, right = sorted(added, key=lambda s: s.points[0])
        assert max(left.points[0::2]) < 50 and min(right.points[0::2]) > 50
        assert all(s.created_ms == line.created_ms for s in added)

    def test_fast_stroke_segment_is_cut_between_points(self):
        # Two far-apart samples: the eraser crosses the segment, not a point.
        ink = BookInk("b")
        ink.add(stroke(points=[0, 50, 100, 50, 200, 50]))
        removed, added = ink.erase(0, [(50, 0), (50, 100)], radius=2)
        assert len(removed) == 1

    def test_misses_leave_strokes_alone(self):
        ink = BookInk("b")
        ink.add(stroke())
        assert ink.erase(0, [(500, 500)], radius=5) == ([], [])
        assert ink.erase(1, [(10, 10)], radius=5) == ([], [])

    def test_shape_goes_whole(self):
        ink = BookInk("b")
        box = stroke(kind="rect", points=[0, 0, 100, 100])
        ink.add(box)
        removed, added = ink.erase(0, [(100, 50)], radius=3)
        assert removed == [box] and added == []

    def test_eraser_inside_a_rect_does_not_touch_it(self):
        ink = BookInk("b")
        ink.add(stroke(kind="rect", points=[0, 0, 100, 100]))
        assert ink.erase(0, [(50, 50)], radius=3) == ([], [])

    def test_session_undo_and_redo(self):
        ink = BookInk("b")
        line = stroke(points=[c for x in range(0, 101, 5) for c in (x, 50)])
        ink.add(line)
        session = EraseSession()
        session.record(*ink.erase(0, [(30, 40), (30, 60)], radius=3))
        session.record(*ink.erase(0, [(70, 40), (70, 60)], radius=3))
        assert len(ink) == 3
        session.undo(ink)
        assert [s.id for s in ink.strokes_on(0)] == [line.id]
        session.redo(ink)
        assert len(ink) == 3 and ink.get(line.id) is None


class TestPersistence:
    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "x" / "b.json")
        ink = BookInk("b", path=path)
        a, b = stroke(page=3, t=1), stroke(page=4, kind="ellipse", points=[0, 0, 9, 9], t=2)
        ink.add(a)
        ink.add(b)
        assert ink.save() and not ink.dirty
        back = BookInk.load("b", path=path)
        assert [s.to_json() for s in back.strokes_on(3)] == [a.to_json()]
        assert [s.to_json() for s in back.strokes_on(4)] == [b.to_json()]
        assert back.size_bytes() == os.path.getsize(path)

    def test_damaged_file_is_an_empty_book(self, tmp_path):
        path = tmp_path / "b.json"
        path.write_text("{not json")
        assert len(BookInk.load("b", path=str(path))) == 0

    def test_bad_strokes_are_skipped(self, tmp_path):
        path = tmp_path / "b.json"
        good = stroke(t=5).to_json()
        path.write_text(json.dumps({"version": 1, "strokes": [{"kind": "x"}, good]}))
        assert [s.id for s in BookInk.load("b", path=str(path)).strokes_on(0)] == [good["id"]]

    def test_page_versions_change_with_the_page(self):
        ink = BookInk("b")
        before = ink.page_version(2)
        ink.add(stroke(page=2))
        assert ink.page_version(2) > before
        assert ink.page_version(1) == 0

    def test_drawings_live_in_xdg_data(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert appdirs.data_dir() == str(tmp_path / "interaktiv")
        assert drawings_dir() == str(tmp_path / "interaktiv" / "drawings")
        assert BookInk("abc").path == str(tmp_path / "interaktiv" / "drawings" / "abc.json")


class TestSimplify:
    def test_straight_run_keeps_its_ends(self):
        from interaktiv_core.ink import simplify
        pts = [(x * 0.5, 10.0) for x in range(100)]
        assert simplify(pts, 0.1) == [pts[0], pts[-1]]

    def test_corner_is_kept(self):
        from interaktiv_core.ink import simplify
        pts = [(float(x), 0.0) for x in range(11)] + [(10.0, float(y)) for y in range(1, 11)]
        out = simplify(pts, 0.1)
        assert (10.0, 0.0) in out and out[0] == pts[0] and out[-1] == pts[-1]
        assert len(out) == 3
