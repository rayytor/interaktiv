"""Tests for content.py: what a page holds besides its activities (no PDF, no model). From the project root:

    python3 -m pytest tools/hotspot_extraction/scanner/test_content.py -q
"""
from types import SimpleNamespace

from tools.hotspot_extraction.scanner import content
from tools.hotspot_extraction.scanner.layout import Column, PageLayout
from tools.hotspot_extraction.scanner.primitives import ImageRect, PagePrimitives, TextSpan, VectorDrawing
from tools.hotspot_extraction.scanner.regions import ActivityRegion, PageGeometry, rect_overlap

PAGE_W, PAGE_H = 600.0, 800.0
BODY = 10.0
PROSE = "running text that fills its measure"


def _span(x0, y0, x1, y1, text=PROSE, size=BODY, bold=False):
    return TextSpan(text=text, bbox=(x0, y0, x1, y1), font="Body-Bold" if bold else "Body", size=size,
                    flags=16 if bold else 0, color=0)


def _line(x0, top, x1, text=PROSE, size=BODY, bold=False):
    """A line of type whose box is 1.2 of its size, hanging from `top`."""
    y0 = top - 1.2 * size
    return {"x0": x0, "y0": y0, "x1": x1, "y1": top, "spans": [_span(x0, y0, x1, top, text, size, bold)]}


def _para(x0, top, x1, n, **kw):
    """`n` lines set solid, top first; returns the lines and the y the paragraph ends at."""
    lines = [_line(x0, top - i * 12.0, x1, **kw) for i in range(n)]
    return lines, top - n * 12.0


def _page(lines=(), blocks=(), drawings=(), images=()):
    spans = [s for ln in lines for s in ln["spans"]]
    prim = PagePrimitives(page_num=7, width=PAGE_W, height=PAGE_H, spans=spans,
                          drawings=list(drawings), images=list(images))
    layout = PageLayout(content_box=(40, 44, 560, 760), columns=[Column(0, 40, 560, 760, 44)], body_font_size=BODY)
    geom = PageGeometry(lines=list(lines), blocks=list(blocks), content_box=layout.content_box,
                        page_w=PAGE_W, page_h=PAGE_H)
    return prim, layout, geom


def _activity(rect, aid="p7-a"):
    return ActivityRegion(id=aid, label="a", column=0, rect=rect, parts=[rect])


def _regions(lines=(), blocks=(), activities=(), **kw):
    prim, layout, geom = _page(lines, blocks, **kw)
    return content.content_regions(prim, layout, geom, list(activities), 7)


def _holds(region, line):
    cx, cy = (line["x0"] + line["x1"]) / 2.0, (line["y0"] + line["y1"]) / 2.0
    r = region.rect
    return r[0] <= cx <= r[2] and r[1] <= cy <= r[3]


# ------------------------------------------------------------ blocks of type

def test_a_paragraph_is_one_content_region_with_its_first_line_as_headline():
    lines, _ = _para(50, 700, 550, 5)
    (region,) = _regions(lines)
    assert region.kind == "content" and region.label is None and region.id == "p7-c1"
    assert region.headline == PROSE
    assert all(_holds(region, ln) for ln in lines)


def test_white_a_line_deep_parts_two_blocks_and_a_paragraph_space_does_not():
    first, end = _para(50, 700, 550, 4)
    close, end2 = _para(50, end - 4.0, 550, 4)            # paragraph spacing: the same block
    far, _ = _para(50, end2 - 30.0, 550, 4)               # a blank band: another block
    upper, lower = _regions(first + close + far)
    assert all(_holds(upper, ln) for ln in first + close)
    assert all(_holds(lower, ln) for ln in far)


def test_two_columns_are_parted_down_the_channel_and_read_left_first():
    left, _ = _para(50, 700, 290, 6)
    right, _ = _para(310, 700, 550, 6)
    a, b = _regions(right + left)
    assert all(_holds(a, ln) for ln in left) and all(_holds(b, ln) for ln in right)
    assert rect_overlap(a.rect, b.rect) == 0.0


def test_two_texts_set_close_side_by_side_are_not_bridged_by_the_line_that_joins_them():
    # The page geometry reads a row whose two halves stand 8 pt apart as one line.
    lines = []
    for i in range(5):
        top = 700 - i * 12.0
        lines.append({"x0": 50, "y0": top - 12, "x1": 550, "y1": top,
                      "spans": [_span(50, top - 12, 296, top), _span(304, top - 12, 550, top)]})
    a, b = _regions(lines)
    assert a.rect[2] <= 300 <= b.rect[0]


def test_a_lists_numbers_are_not_a_column():
    lines = []
    for i in range(4):
        top = 700 - i * 12.0
        lines.append(_line(50, top, 60, text=f"{i + 1}."))
        lines.append(_line(75, top, 550))
    (region,) = _regions(lines)
    assert region.rect[0] <= 50


def test_a_heading_opens_the_block_under_it_and_closes_the_one_above():
    above, end = _para(50, 700, 550, 3)
    heading = _line(50, end - 6.0, 200, text="A heading", size=14.0)
    below, _ = _para(50, heading["y0"] - 4.0, 550, 3)
    upper, lower = _regions(above + [heading] + below)
    assert not _holds(upper, heading) and _holds(lower, heading)
    assert all(_holds(lower, ln) for ln in below)


def test_a_heading_with_nothing_under_it_gets_no_region():
    heading = _line(50, 700, 200, text="A heading", size=14.0)
    assert _regions([heading]) == []


def test_a_bold_line_under_a_poem_is_its_attribution_not_a_heading():
    poem, end = _para(50, 700, 300, 4)
    book = _line(180, end - 3.0, 300, text="The Name of the Book", bold=True)
    (region,) = _regions(poem + [book])
    assert _holds(region, book)


def test_a_block_taller_than_half_the_sheet_is_cut_at_a_paragraph_seam_only():
    first, end = _para(50, 740, 550, 22)
    second, _ = _para(50, end - 5.0, 550, 22)
    upper, lower = _regions(first + second)
    assert all(_holds(upper, ln) for ln in first) and all(_holds(lower, ln) for ln in second)
    # One long paragraph has no such seam, and is not cut through a sentence.
    solid, _ = _para(50, 740, 550, 38)
    assert len(_regions(solid)) == 1


# ------------------------------------------------------------- drawn things

def test_a_panel_is_one_region_with_the_type_it_holds():
    inside, _ = _para(110, 590, 390, 3)
    outside, _ = _para(50, 740, 550, 3)
    text, panel = _regions(inside + outside, [{"rect": (100, 500, 400, 600), "kind": "panel"}])
    assert panel.rect == (100, 500, 400, 600) and panel.headline == PROSE
    assert all(_holds(text, ln) for ln in outside)


def test_two_panels_drawn_edge_to_edge_are_two_regions():
    blocks = [{"rect": (50, 400, 300, 600), "kind": "panel"}, {"rect": (50, 200, 300, 400), "kind": "panel"}]
    a, b = _regions(blocks=blocks)
    assert a.rect[1] >= b.rect[3] - 0.01 and rect_overlap(a.rect, b.rect) <= 1.0


def test_a_picture_is_a_region_and_a_small_one_is_not():
    big = ImageRect(bbox=(100, 300, 400, 500), width=600, height=400)
    icon = ImageRect(bbox=(500, 700, 530, 730), width=60, height=60)
    regions = _regions(images=[big, icon])
    assert [r.rect for r in regions] == [(100, 300, 400, 500)]


def test_a_drawn_diagram_is_one_region_with_its_labels_and_caption():
    def box(x0, y0, x1, y1):
        return VectorDrawing(rect=(x0, y0, x1, y1), fill=(0.8, 0.5, 0.2), stroke=None, width=0.0, is_rect=True, lines=[])
    drawings = [box(100, 500, 180, 560), box(320, 500, 400, 560)]
    labels = [_line(110, 495, 170, text="Glikoz", size=8.0), _line(330, 495, 390, text="Piruvat", size=8.0)]
    caption = _line(100, 470, 330, text="Görsel 2.1: a caption under the drawing", size=8.0)
    (region,) = _regions(labels + [caption], drawings=drawings)
    assert region.rect[0] <= 100 and region.rect[2] >= 400 and _holds(region, caption)


def test_a_rule_standing_alone_does_not_close_the_channel_it_stands_in():
    left, _ = _para(50, 700, 290, 6)
    right, _ = _para(310, 700, 550, 6)
    rule = VectorDrawing(rect=(299.5, 600, 300.5, 710), fill=None, stroke=(0, 0, 0), width=1.0, is_rect=False,
                         lines=[(300, 600, 300, 710)])
    assert len(_regions(left + right, drawings=[rule])) == 2


# ---------------------------------------------------------------- activities

def test_activities_come_back_untouched_in_reading_order_and_nothing_lies_on_them():
    above, _ = _para(50, 740, 550, 3)
    below, _ = _para(50, 380, 550, 3)
    inside, _ = _para(60, 590, 540, 4)                     # the activity's own lines
    act = _activity((50, 420, 550, 600))
    regions = _regions(above + inside + below, activities=[act])
    assert [r.kind for r in regions] == ["content", "activity", "content"]
    assert regions[1] is act and act.rect == (50, 420, 550, 600)
    assert all(rect_overlap(r.rect, act.rect) <= 0.5 for r in regions if r is not act)
    assert not any(_holds(r, ln) for r in regions if r is not act for ln in inside)


def test_a_panel_an_activity_stands_on_is_not_a_region_of_its_own():
    act = _activity((120, 520, 380, 580))
    regions = _regions(blocks=[{"rect": (100, 500, 400, 600), "kind": "panel"}], activities=[act])
    assert regions == [act]


def test_answer_rules_left_outside_a_box_join_the_block_above_them():
    question, end = _para(50, 700, 550, 2)
    rules = [{"rect": (50, end - 20.0 - i * 18.0, 550, end - 6.0 - i * 18.0), "kind": "cell"} for i in range(2)]
    (region,) = _regions(question, rules)
    assert region.rect[1] <= rules[-1]["rect"][1]
    assert _regions(blocks=rules) == []                    # and make nothing alone


# ------------------------------------------------------- pages not content

def _prim(spans):
    return PagePrimitives(page_num=1, width=PAGE_W, height=PAGE_H, spans=spans, drawings=[], images=[])


def test_a_chapter_opener_carries_its_number_in_poster_type():
    layout = SimpleNamespace(body_font_size=BODY)
    assert content.is_opener(_prim([_span(50, 600, 120, 680, "1.", size=72.0)]), layout)
    assert content.is_opener(_prim([_span(50, 600, 300, 660, "2. ÜNİTE", size=60.0)]), layout)
    # Large type alone is a masthead or a feature heading on an ordinary page.
    assert not content.is_opener(_prim([_span(50, 600, 400, 660, "HAVADİS", size=55.0)]), layout)
    assert not content.is_opener(_prim([_span(50, 600, 80, 615, "1.", size=11.0)]), layout)


def test_a_contents_page_says_where_the_first_chapter_starts():
    spans = [_span(50, 700, 200, 720, "İÇİNDEKİLER", size=20.0),
             _span(50, 660, 300, 672, "Kitabın Tanıtımı"), _span(520, 660, 540, 672, "9"),
             _span(50, 640, 300, 652, "1. ÜNİTE: COĞRAFYANIN DOĞASI"), _span(520, 640, 540, 652, "14"),
             _span(50, 620, 300, 632, "1.1. Bir Konu"), _span(520, 620, 540, 632, "16")]
    assert content.contents_page(_prim(spans)) == ("named", 14)
    assert content.contents_page(_prim([_span(50, 660, 300, 672)])) == (None, None)


def test_a_page_of_numbers_is_contents_only_when_it_follows_a_contents_page():
    table = [_span(50, 700 - 14 * i, 300, 712 - 14 * i, f"Ölçüm {i}  {10 + i}") for i in range(10)]
    assert content.contents_page(_prim(table)) == ("listed", None)
    assert content.contents_run([8], [9, 55, 140]) == [8, 9]
    assert content.contents_run([], [55, 56]) == []


def _form_page(extra=()):
    title = _span(60, 720, 320, 734, "ÖZ DEĞERLENDİRME FORMU (1. TEMA: KONUŞMA)", bold=True)
    body, _ = _para(60, 690, 540, 6)
    lines = [{"x0": 60, "y0": 720, "x1": 320, "y1": 734, "spans": [title]}] + body + list(extra)
    return _page(lines, [{"rect": (60, 120, 540, 600), "kind": "grid"}])


def test_a_sheet_titled_as_a_form_is_one_region_from_its_title_to_its_last_line():
    prim, _, geom = _form_page()
    rect, title = content.page_form(prim, geom)
    assert title.startswith("ÖZ DEĞERLENDİRME FORMU")
    assert rect[1] <= 120 and rect[3] >= 734 and rect[0] <= 60 and rect[2] >= 540


def test_a_sentence_that_mentions_a_form_is_not_its_title():
    mention = _line(60, 400, 540, text="Konuşmanızı aşağıdaki öz değerlendirme formuyla değerlendiriniz")
    prim, _, geom = _form_page([mention])
    assert content.page_form(prim, geom) is not None          # the title still stands alone
    prim, _, geom = _page([mention] + _para(60, 380, 540, 6)[0])
    assert content.page_form(prim, geom) is None


def test_two_forms_on_one_sheet_and_a_form_that_starts_mid_page_are_left_to_their_boxes():
    second = _line(60, 380, 320, text="ÖZ DEĞERLENDİRME FORMU (2. TEMA: YAZMA)", bold=True)
    prim, _, geom = _form_page([second])
    assert content.page_form(prim, geom) is None
    low = _line(60, 380, 200, text="Performans Görevi", bold=True)
    prim, _, geom = _page(_para(60, 720, 540, 6)[0] + [low])
    assert content.page_form(prim, geom) is None


def test_a_bibliography_is_headed_so_or_is_a_list_of_entries():
    headed = _prim([_span(60, 720, 200, 740, "Kaynakça", size=16.0), _span(60, 690, 500, 702, "Akal, O. (1996). Bizans.")])
    assert content.is_bibliography(headed)
    entries = _prim([_span(60, 700 - 14 * i, 500, 712 - 14 * i, f"Yazar{i}, A. (200{i}). Bir Kitap. Ankara.")
                     for i in range(9)])
    assert content.is_bibliography(entries)
    lesson = _prim([_span(60, 700 - 14 * i, 500, 712 - 14 * i, "Osmanlı Devleti 1299 yılında kuruldu, sonra büyüdü.")
                    for i in range(9)])
    assert not content.is_bibliography(lesson)


def test_a_plate_is_one_picture_or_a_lettered_drawing_with_no_lesson_round_it():
    picture = PagePrimitives(page_num=1, width=PAGE_W, height=PAGE_H, drawings=[],
                             spans=[_span(60, 60, 200, 70, "Türkiye Fiziki Haritası")],
                             images=[ImageRect(bbox=(30, 80, 570, 760), width=1000, height=1400)])
    assert content.is_plate(picture)
    assert content.is_plate(_prim([]))                           # a blank sheet
    lesson = PagePrimitives(page_num=1, width=PAGE_W, height=PAGE_H, drawings=[], images=picture.images,
                            spans=[_span(60, 700 - 14 * i, 540, 712 - 14 * i, PROSE + " " + PROSE) for i in range(8)])
    assert not content.is_plate(lesson)


def test_back_matter_is_the_last_bibliography_and_every_sheet_after_it():
    assert content.back_matter_start([314, 315, 316], [317, 318], 318) == 314
    assert content.back_matter_start([269, 270], [], 282) == 269              # maps after it, flagged or not
    assert content.back_matter_start([303, 314, 315], [318, 319], 319) == 314  # 303 is a lesson on writing one
    assert content.back_matter_start([50], [], 300) == 301                   # far from the end: no back matter
    assert content.back_matter_start([], [99, 100], 100) == 99               # a book that ends in plates
    assert content.back_matter_start([], [], 100) == 101
    assert content.back_matter_start([], list(range(1, 101)), 100) == 101    # a scan: every sheet is a picture


def test_front_matter_ends_at_the_first_opener_or_where_the_contents_page_says():
    assert content.front_matter_end([13, 73, 151], [8, 9], None, 318) == 12
    assert content.front_matter_end([], [7, 8], 14, 282) == 13       # openers drawn, not set
    assert content.front_matter_end([], [8], None, 100) == 8
    assert content.front_matter_end([], [], None, 100) == 0
    assert content.front_matter_end([250], [], None, 300) == 0       # an opener that late opens chapter five
    assert content.front_matter_end([56], [8, 9], 13, 321) == 12     # the first opener is drawn; the contents know
    assert content.front_matter_end([34], [8, 9], 34, 185, first_activity=18) == 17   # a starter unit before Theme 1


def test_a_region_standing_in_the_same_place_sheet_after_sheet_is_furniture():
    def head(n):
        return ActivityRegion(id=f"p{n}-c1", label=None, column=0, rect=(300, 750, 550, 765), parts=[], kind="content")

    def body(n):
        return ActivityRegion(id=f"p{n}-c2", label=None, column=0, rect=(50, 300 + n, 550, 600), parts=[], kind="content")
    pages = {p: [head(p), body(p)] for p in range(10, 30, 2)}
    dropped = content.furniture(pages, page_count=100)
    assert sorted(dropped) == sorted(pages)
    assert all(a.id.endswith("c1") for acts in dropped.values() for a in acts)
