"""Tests for what snap.py may and may not move, and for icons.py (no GPU, no model, no PDF). From the project root:

    .venv-vision/bin/python -m pytest tools/hotspot_extraction/vision/test_snap_icons.py -q
"""
from types import SimpleNamespace

from tools.hotspot_extraction.scanner.anchors import PublisherOge
from tools.hotspot_extraction.scanner.regions import ActivityRegion, PageGeometry
from tools.hotspot_extraction.scanner.serializer import serialize_book_regions
from tools.hotspot_extraction.vision import icons, snap

PAGE_W, PAGE_H = 600.0, 800.0


def _line(x0, y0, x1, y1):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "spans": []}


def _block(rect, kind="panel"):
    return {"rect": rect, "kind": kind}


def _geom(lines=(), blocks=()):
    return PageGeometry(lines=list(lines), blocks=list(blocks), content_box=(40, 40, 560, 760), page_w=PAGE_W, page_h=PAGE_H)


def _held(rect, geom):
    return {id(ln) for ln in snap.held_lines(rect, geom)}


# ------------------------------------------------------------------- snap.py

def test_an_edge_stepping_off_a_line_never_gives_up_a_line_the_box_holds():
    # The foot of the box clips a tall line in the next column (a drop capital); the last
    # question stands level with it. Before, the edge went to the nearest clear seam, which
    # was above the question, and the question was lost.
    question, drop_cap = _line(54, 340, 227, 357), _line(297, 320, 495, 350)
    geom = _geom([question, drop_cap])
    box = (41, 338, 502, 660)
    snapped = snap.seam_snap(box, geom)
    assert snapped[1] <= question["y0"]
    assert _held(snapped, geom) == _held(box, geom) == {id(question)}


def test_an_edge_in_the_ascender_room_of_a_line_stays():
    lines = [_line(50, 480, 400, 494)]
    box = (45, 300, 420, 492)             # 2 pt inside the line's box: over the ink, not through it
    assert snap.seam_snap(box, _geom(lines)) == box


def test_an_edge_through_a_line_the_box_holds_moves_out_past_it():
    line = _line(50, 480, 400, 494)
    snapped = snap.seam_snap((45, 300, 420, 488), _geom([line]))
    assert snapped[3] >= line["y1"] and snapped[3] - line["y1"] <= 2


def test_a_block_is_not_left_at_the_cost_of_a_line_the_box_holds():
    # The same clipped block as in test_phase5, but the box has a line of its own in the clipped part.
    line = _line(120, 260, 380, 272)
    geom = _geom([line], [_block((100, 100, 400, 310))])
    assert snap.settle([(0.9, (100, 250, 400, 600))], geom) == [(100, 250, 400, 600)]


def test_a_block_is_not_taken_when_it_brings_in_a_line_from_outside_it():
    beside = _line(402, 305, 409, 325)    # right of the block, below the box: neither's
    geom = _geom([beside], [_block((100, 300, 400, 420))])
    assert snap.settle([(0.9, (90, 330, 410, 600))], geom) == [(90, 330, 410, 600)]


def test_a_box_taller_than_a_hotspot_is_left_out_and_the_box_inside_it_stands():
    page, question = (50, 100, 550, 700), (60, 300, 300, 400)
    assert snap.settle([(0.95, page), (0.7, question)], _geom()) == [question]


def test_two_boxes_on_one_block_grow_to_their_shared_boundary():
    geom = _geom([], [_block((100, 300, 400, 460), "cell")])
    out = sorted(snap.settle([(0.9, (100, 350, 400, 600)), (0.8, (100, 200, 400, 330))], geom), key=lambda r: -r[3])
    assert len(out) == 2
    upper, lower = out
    assert lower[3] <= upper[1]                     # they do not overlap
    assert upper[1] <= 331 and upper[3] == 600      # the upper one came down to its neighbour


# ------------------------------------------------- snap.py: between_siblings

def _label(text, value, x0, y0, step=False):
    span = SimpleNamespace(bbox=(x0, y0, x0 + 9, y0 + 14), text=text)
    return SimpleNamespace(label=text, normalized_value=float(value), span=span, column_index=0,
                           is_step=step, is_prompt=False)


def _list_page(texts=("3.", "4.", "5.", "6.", "7."), values=(3, 4, 5, 6, 7), extra_lines=(), blocks=(), step=None):
    """Five questions of two lines each down one column; the student boxed the first two and the last."""
    labels, lines, rects = [], [], []
    for n, (text, value) in enumerate(zip(texts, values)):
        top = 700 - 60 * n
        labels.append(_label(text, value, 65, top - 14, step=(n == step)))
        lines += [_line(65, top - 14, 400, top), _line(85, top - 30, 400, top - 16)]
        rects.append((64.0, top - 31.0, 401.0, top))
    boxed = (0, 1, 4)
    claimed = {id(labels[n]): rects[n] for n in boxed}
    return [rects[n] for n in boxed], claimed, labels, _geom(lines + list(extra_lines), blocks)


def test_unboxed_labels_between_two_boxed_ones_get_their_own_lines():
    rects, claimed, labels, geom = _list_page()
    made = snap.between_siblings(rects, claimed, labels, geom)
    assert [m.label for m, _ in made] == ["5.", "6."]
    for (m, rect), top in zip(made, (580, 520)):
        assert rect == (64.0, top - 31.0, 401.0, top)
        assert all(snap.rect_overlap(rect, r) == 0.0 for r in rects)


def test_an_unboxed_label_takes_the_answer_rules_under_it_and_stops_at_the_next_label():
    rules = [_block((100, 540 - 12 * n, 400, 552 - 12 * n), "cell") for n in range(2)]
    rects, claimed, labels, geom = _list_page(blocks=rules)
    made = dict((m.label, r) for m, r in snap.between_siblings(rects, claimed, labels, geom))
    assert made["5."][1] == 527.0 and made["5."][1] > labels[3].span.bbox[3]


def test_no_region_is_made_across_a_picture_or_a_foreign_paragraph_or_for_a_longer_run():
    picture = [_block((100, 525, 400, 548), "figure")]
    assert snap.between_siblings(*_list_page(blocks=picture)) == []
    paragraph = [_line(50, 535, 400, 548)]            # starts left of the label: not the question's
    assert snap.between_siblings(*_list_page(extra_lines=paragraph)) == []
    rects, claimed, labels, geom = _list_page()
    del claimed[id(labels[1])]                        # three unboxed in a row: the student refused the list
    assert snap.between_siblings([rects[0], rects[2]], claimed, labels, geom) == []


def test_unboxed_labels_must_count_on_from_a_boxed_neighbour_and_a_step_is_never_filled_in():
    assert snap.between_siblings(*_list_page(values=(3, 4, 8, 9, 2))) == []
    assert snap.between_siblings(*_list_page(step=2)) == []
    # 4 | 1 2 | 3: a new list that the boxed 3 below belongs to.
    made = snap.between_siblings(*_list_page(texts=("3.", "4.", "1.", "2.", "3."), values=(3, 4, 1, 2, 3)))
    assert [m.label for m, _ in made] == ["1.", "2."]


def test_no_label_at_the_foot_of_a_list_is_filled_in_without_a_boxed_one_below_it():
    rects, claimed, labels, geom = _list_page()
    del claimed[id(labels[4])]
    assert snap.between_siblings(rects[:2], claimed, labels, geom) == []


# ------------------------------------------------------------------ icons.py

def _oge(oid, x, y, title="Soru Çözümü", page=12):
    """An entry whose icon hangs at (x, y) in points, y up."""
    return PublisherOge(id=oid, title=title, sayfano=page, posx=x / PAGE_W * 100.0, posy=(PAGE_H - y) / PAGE_H * 100.0, data="x")


def _region(rid, rect, label=None):
    return ActivityRegion(id=rid, label=label, column=0, rect=rect, parts=[rect], headline=None, items=None, anchored=False)


def test_an_icon_in_the_margin_beside_the_question_names_the_region_that_starts_at_its_picture():
    # The region takes the picture above the question, so its top is far above the icon and
    # the join (which measures to the top) refuses the pair.
    acts = [_region("p13-v1", (100, 300, 500, 700))]
    oge = _oge("e1", 80, 420)
    assert icons.bind(acts, [oge], PAGE_W, PAGE_H, 12) == 1
    assert acts[0].oge_id == "e1" and acts[0].rect == (100, 300, 500, 700)


def test_an_icon_is_not_bound_to_a_region_with_another_label():
    acts = [_region("p13-b", (100, 300, 500, 700), label="b")]
    assert icons.bind(acts, [_oge("e1", 80, 420, title="12/a")], PAGE_W, PAGE_H, 12) == 0
    assert acts[0].oge_id is None


def test_a_region_takes_one_entry_the_one_nearest_its_top():
    outer, inner = _region("p13-v1", (100, 200, 500, 700)), _region("p13-v2", (520, 380, 590, 460))
    low, high = _oge("low", 80, 420), _oge("high", 80, 560)
    acts = [outer, inner]
    assert icons.bind(acts, [low, high], PAGE_W, PAGE_H, 12) == 1
    assert outer.oge_id == "high" and inner.oge_id is None          # nearer the top of the region; the other stays a pin


def test_a_doubtful_box_stands_only_where_an_icon_vouches_for_it():
    doubtful = [(0.4, (100, 300, 500, 400)), (0.35, (100, 100, 500, 200)), (0.45, (100, 100, 500, 760))]
    at_first = _oge("e1", 80, 390)
    assert icons.admit([], doubtful, [at_first], PAGE_W, PAGE_H) == [doubtful[0]]     # not the one no icon is at, not the tall one
    sure = [(0.9, (100, 290, 500, 410))]
    assert icons.admit(sure, doubtful, [at_first], PAGE_W, PAGE_H) == []              # a sure box already answers the icon
    at_its_foot = _oge("e2", 80, 310)
    assert icons.admit([], doubtful, [at_its_foot], PAGE_W, PAGE_H) == []             # an icon heads what it opens


def test_an_unanswered_icon_takes_the_panel_it_hangs_at_whole_or_not_at_all():
    panel = (100, 300, 500, 500)
    lines = [_line(120, 460, 480, 472), _line(120, 440, 480, 452), _line(120, 420, 480, 432)]
    oge = _oge("e1", 80, 490)

    acts = []
    assert icons.take_blocks(acts, [oge], _geom(lines, [_block(panel)]), 13, 12) == 1
    assert acts[0].rect == panel and acts[0].anchored and acts[0].id == "p13-oge-e1"

    acts = [_region("p13-v1", (300, 350, 560, 450))]                  # a region already stands on the panel
    assert icons.take_blocks(acts, [oge], _geom(lines, [_block(panel)]), 13, 12) == 0

    acts = []                                                           # a tinted bar with a heading in it
    assert icons.take_blocks(acts, [oge], _geom(lines[:1], [_block(panel)]), 13, 12) == 0


# -------------------------------------------------------------- serializer.py

def test_the_serializer_leaves_the_students_regions_alone_when_told_to(tmp_path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 not a real one")
    # A region that clips a drawn block: the rules cleanup would move its edge off the block.
    rect = (100.0, 250.0, 400.0, 600.0)
    geom = _geom([_line(120, 260, 380, 272)], [_block((100, 100, 400, 310))])

    def bake(clean):
        data = serialize_book_regions(
            book_id="b", pdf_path=str(pdf), page_count=1, pages_activities={1: [_region("p1-v1", rect)]},
            folio_map={1: 1}, folio_offset=0, calibration_info={"confidence": "none", "markerSize": None},
            page_dimensions={1: (PAGE_W, PAGE_H)}, pages_geometry={1: geom}, clean=clean)
        return tuple(data["pages"]["1"]["activities"][0]["rect"])

    assert bake(False) == rect
    assert bake(True) != rect
