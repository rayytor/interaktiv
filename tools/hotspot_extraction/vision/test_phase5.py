"""Phase 5 tests for snap.py and infer.py (no GPU, no model). Run with the vision venv from the project root:

    .venv-vision/bin/python -m pytest tools/hotspot_extraction/vision/test_phase5.py -q
"""
from types import SimpleNamespace

from tools.hotspot_extraction.scanner.regions import PageGeometry
from tools.hotspot_extraction.vision import infer, snap


def _line(x0, y0, x1, y1):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "spans": []}


def _geom(lines, blocks=()):
    return PageGeometry(lines=list(lines), blocks=list(blocks), content_box=(40, 40, 560, 760), page_w=600, page_h=800)


def test_pixels_become_points_with_y_up_and_stay_on_the_sheet():
    # A 1000 x 2000 px render of a 500 x 1000 pt sheet: half the numbers, y measured from the bottom.
    assert snap.px_to_points([100, 200, 300, 600], 1000, 2000, 500, 1000) == (50.0, 700.0, 150.0, 900.0)
    assert snap.px_to_points([-20, -10, 1100, 2100], 1000, 2000, 500, 1000) == (0.0, 0.0, 500.0, 1000.0)


def test_an_edge_inside_a_line_moves_to_the_gap_between_lines():
    lines = [_line(50, 500, 400, 512), _line(50, 480, 400, 492), _line(50, 460, 400, 472)]
    snapped = snap.seam_snap((45, 300, 420, 486), _geom(lines))      # top edge through the middle line
    assert not any(ln["y0"] < snapped[3] < ln["y1"] for ln in lines)   # clear of every line
    assert abs(snapped[3] - 486) <= 10
    assert snapped[:3] == (45, 300, 420)


def test_an_edge_already_in_clear_space_is_left_alone():
    lines = [_line(50, 500, 400, 512), _line(50, 480, 400, 492)]
    rect = (45, 300, 420, 496)
    assert snap.seam_snap(rect, _geom(lines)) == rect


def _marker(label, bbox):
    return SimpleNamespace(label=label, slug=None, span=SimpleNamespace(bbox=bbox), column_index=0)


def test_a_marker_in_the_top_left_quarter_labels_the_box_once():
    rect = (50, 300, 400, 500)
    top_left, bottom, right = _marker("a", (52, 485, 60, 495)), _marker("b", (52, 310, 60, 320)), _marker("c", (350, 485, 358, 495))
    taken = set()
    assert snap.marker_for(rect, [bottom, right, top_left], taken) is top_left
    taken.add(id(top_left))
    assert snap.marker_for(rect, [bottom, right, top_left], taken) is None


def _span(text, x0, x1, font="Helvetica", color=0, flags=0):
    return SimpleNamespace(text=text, bbox=(x0, 485, x1, 495), font=font, color=color, flags=flags, size=10.0)


def _label(span):
    return SimpleNamespace(label=span.text, span=span, is_prompt=False, is_step=False)


def test_the_first_glyph_of_a_line_of_mathematics_is_not_a_label():
    b, rest = _span("b", 58, 64), _span("∈ ℝ, n", 67, 120)
    assert not snap.stands_apart(_label(b), [b, rest])
    alone = _span("2", 58, 64)
    assert not snap.stands_apart(_label(alone), [alone])


def test_a_label_is_bracketed_bold_or_coloured():
    text = _span("Work in pairs.", 80, 200)
    for span in (_span("a)", 57, 65), _span("3.", 57, 65), _span("h", 65, 72, color=214621),
                 _span("b", 57, 63, font="Helvetica-Bold")):
        assert snap.stands_apart(_label(span), [span, text])
    assert snap.stands_apart(SimpleNamespace(label="", span=text, is_prompt=True, is_step=False), [text])


def test_of_two_overlapping_boxes_the_confident_one_stays():
    a, b, c = [0, 0, 100, 100], [5, 5, 100, 100], [200, 200, 300, 300]
    kept = infer.suppress([(0.6, a), (0.9, b), (0.7, c)])
    assert [s for s, _ in kept] == [0.9, 0.7]


def _block(rect, kind="panel"):
    return {"rect": rect, "kind": kind}


def test_a_low_confidence_box_inside_another_is_dropped():
    big, strip, apart = (60, 600, 520, 730), (65, 705, 520, 730), (60, 300, 520, 500)
    kept = snap.drop_nested([(0.53, strip), (0.89, big), (0.95, apart)])
    assert sorted(r for _, r in kept) == sorted([big, apart])


def test_a_block_mostly_held_is_taken_whole():
    geom = _geom([], [_block((100, 300, 400, 420))])
    assert snap.settle([(0.9, (90, 330, 410, 600))], geom) == [(90, 300, 410, 600)]


def test_a_block_reaching_into_a_neighbour_does_not_halve_the_box():
    # The figure pokes 12 pt into the right-hand box. The left box keeps its
    # whole height and does not retreat above the figure; its right edge may come
    # out towards the figure's, as far as the neighbour and no further.
    geom = _geom([], [_block((61, 237, 313, 415), "figure")])
    left, right = (66, 255, 286, 733), (301, 156, 520, 700)
    out = snap.settle([(0.9, left), (0.9, right)], geom)
    got = next(r for r in out if r[0] < 100)
    assert got[1] == 237 and got[3] == 733 and 286 <= got[2] <= 301
    assert right in out


def test_a_block_only_clipped_is_left_when_that_is_cheap():
    geom = _geom([], [_block((100, 100, 400, 310))])
    assert snap.settle([(0.9, (100, 250, 400, 600))], geom) == [(100, 310, 400, 600)]


def test_two_overlapping_boxes_are_parted_and_neither_is_lost():
    geom = _geom([_line(50, 505, 400, 515), _line(50, 480, 400, 490)])
    out = snap.settle([(0.9, (50, 495, 400, 700)), (0.8, (50, 300, 400, 500))], geom)
    assert len(out) == 2
    upper, lower = sorted(out, key=lambda r: -r[3])
    assert upper[1] > lower[3] and 490 <= lower[3] and upper[1] <= 505


# ------------------------------------------------------------- self_label.py

def test_a_page_with_one_doubtful_box_is_left_out_whole():
    from tools.hotspot_extraction.vision import self_label
    a, b = [0, 0, 100, 100], [200, 200, 300, 300]
    assert self_label.sure_boxes([(0.95, a), (0.5, b)], 0.9) is None
    assert [s for s, _ in self_label.sure_boxes([(0.95, a), (0.93, b)], 0.9)] == [0.95, 0.93]
    assert self_label.sure_boxes([], 0.9) == []                       # an empty page


def test_picks_are_capped_seeded_and_mostly_not_empty():
    from tools.hotspot_extraction.vision import self_label
    candidates = [(p, 2) for p in range(1, 101)] + [(p, 0) for p in range(101, 201)]
    picked = self_label.pick_pages(candidates, per_book=40, empty_share=0.2, seed=1)
    assert sum(1 for p in picked if p <= 100) == 40 and sum(1 for p in picked if p > 100) == 10
    assert picked == self_label.pick_pages(candidates, per_book=40, empty_share=0.2, seed=1)
