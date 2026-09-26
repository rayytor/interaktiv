"""
Verification suite for marker-free question detection and whole-figure inclusion.

Evaluates:
- books/09f62a7e-20f7-43b2-99c6-b67d24e0809a.pdf (Turkish Biyoloji textbook)
- books/0e966773-5012-4f57-8be5-d892e8c75f22.pdf (English ELT coursebook)

Checks:
1. The grammar of an instruction is what marks a question: a Turkish formal
   imperative, an exhortative, an English imperative opener, or a rubric title.
2. Nothing opens a question mid-sentence, on a bullet, or in lower case.
3. A prompt standing over an enumerated run is that run's stem, not an activity.
4. Picture primitives are welded into whole figures, and a figure is never
   welded across a column gutter or across an activity marker.
5. A page whose only activity carries no label at all ("Konuya Başlarken" over
   a flowchart) yields one hotspot covering the instruction and the chart.
6. An activity that names a graphic below it grows over the whole graphic.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pymupdf

from tools.hotspot_extraction.scanner.figures import _touches, detect_figures
from tools.hotspot_extraction.scanner.layout import detect_layout, _text_lines
from tools.hotspot_extraction.scanner.markers import detect_markers
from tools.hotspot_extraction.scanner.primitives import extract_page_primitives
from tools.hotspot_extraction.scanner.prompts import (
    Paragraph,
    detect_activity_markers,
    detect_prompts,
    reads_as_question,
    reads_as_rubric,
)
from tools.hotspot_extraction.scanner.regions import grow_activity_regions

PDF_BIO = str(PROJECT_ROOT / "books" / "09f62a7e-20f7-43b2-99c6-b67d24e0809a.pdf")
PDF_ELT = str(PROJECT_ROOT / "books" / "0e966773-5012-4f57-8be5-d892e8c75f22.pdf")


def _paragraph(text: str, size: float = 10.0) -> Paragraph:
    """A paragraph with only the fields the grammar tests read."""
    return Paragraph(
        lines=[], column=0, x0=0.0, y0=0.0, x1=100.0, y1=12.0,
        size=size, text=text,
    )


def _skip_unless(pdf_path: str) -> bool:
    if Path(pdf_path).exists():
        return False
    try:
        import pytest
        pytest.skip(f"{pdf_path} not found")
    except ImportError:
        print(f"Skipping: {pdf_path} not found")
    return True


def test_question_grammar():
    """What the grammar accepts as an instruction, and what it refuses."""
    asks = [
        "Aşağıdaki ifadeleri dikkatle okuyunuz.",
        "Verilen boşlukları uygun kelimelerle doldurunuz.",
        "Bu soruyu arkadaşlarınızla tartışalım.",
        "Read the dialogue and answer the questions.",
        "Which vocabulary from this theme can you use correctly?",
        "a) Hangileri hücresel solunum tepkimelerine katılır?",
    ]
    for text in asks:
        assert reads_as_question(_paragraph(text)), f"should ask: {text!r}"

    # The second person plural possessive is not an imperative; a question mark
    # inside a long block of exposition is rhetorical; a bullet opens an item,
    # not a question; and a sentence never begins in lower case.
    refuses = [
        # The aorist wears the imperative's ending with a person marker in
        # front of it: "you will reach", not "reach".
        "Verdiğiniz karara göre ilgili oku takip ederek çıkışa ulaşırsınız ve "
        "böylece konuya hazır hâle gelirsiniz.",
        "Bu bilgileri günlük hayatta kullanabilirsiniz.",
        "Besinlerin içerisine enerji nasıl aktarılmaktadır ve canlılar "
        "tarafından tüketilen besinler nasıl sentezlenmektedir? Yeryüzündeki "
        "birçok canlı için gerekli olan enerjinin kaynağı güneştir.",
        "• Öğretmeniniz gözetiminde gruplar oluşturunuz.",
        "çalışma planı oluşturunuz.",
        "ATP molekülü sürekli kullanılan ve yenilenebilen bir moleküldür.",
    ]
    for text in refuses:
        assert not reads_as_question(_paragraph(text)), f"should refuse: {text!r}"

    assert reads_as_rubric(_paragraph("Konuya Başlarken"), 10.0)
    assert reads_as_rubric(_paragraph("Activity 1"), 10.0)
    assert not reads_as_rubric(_paragraph("Fotosentez olayı kloroplastta gerçekleşir."), 10.0)
    print("  ✓ instruction grammar accepts instructions and refuses prose")


def test_unlabelled_activity_takes_its_chart(pdf_path: str = PDF_BIO):
    """
    Sheet 22 is a titled box -- "Konuya Başlarken" -- holding an instruction and
    a flowchart, and carries no letter or number anywhere. It used to come back
    empty. It must now come back as one hotspot over the whole of it.
    """
    if _skip_unless(pdf_path):
        return
    doc = pymupdf.open(pdf_path)
    prim = extract_page_primitives(doc, 22)
    layout = detect_layout(prim)

    assert not detect_markers(prim, layout=layout), "sheet 22 enumerates nothing"

    markers = detect_activity_markers(prim, layout=layout)
    assert len(markers) == 1, f"expected one prompt, got {[m.label for m in markers]}"
    assert markers[0].is_prompt

    acts = grow_activity_regions(prim, layout, markers)
    assert len(acts) == 1, f"expected one activity, got {len(acts)}"
    rect = acts[0].rect

    # The chart's furthest column of boxes sits past x = 455; the instruction
    # alone stops near 440. Reaching it is the whole point.
    assert rect[0] <= 60.0 and rect[2] >= 500.0, f"chart not covered: {rect}"
    assert rect[3] >= 650.0, f"title not covered: {rect}"
    assert rect[1] <= 200.0, f"foot of the chart not covered: {rect}"
    print(f"  ✓ p22 unlabelled activity covers its flowchart: {rect}")


def test_prompt_defers_to_an_enumerated_run(pdf_path: str = PDF_BIO):
    """
    Sheet 18 sets a "Yönerge" instruction over a numbered list. The numbers are
    the activities; the instruction is their stem and must not become a hotspot
    wrapped round all of them.
    """
    if _skip_unless(pdf_path):
        return
    doc = pymupdf.open(pdf_path)
    prim = extract_page_primitives(doc, 18)
    layout = detect_layout(prim)
    markers = detect_markers(prim, layout=layout)
    assert len(markers) >= 5, "sheet 18 enumerates its questions"
    assert not detect_prompts(prim, layout, markers), "the stem is not an activity"
    print("  ✓ p18 stem over a numbered run raises no prompt")


def test_figure_weld(pdf_path: str = PDF_ELT):
    """A picture's tiles weld; a gutter and a marker both stop the weld."""
    if _skip_unless(pdf_path):
        return

    # Two tiles of one picture, and the same two with a gutter between them.
    left = (70.0, 500.0, 180.0, 580.0)
    right = (190.0, 500.0, 290.0, 580.0)
    assert _touches(left, right), "abutting tiles weld"
    assert not _touches(left, right, gutters=[185.0]), "a gutter stops the weld"
    assert not _touches(left, (400.0, 500.0, 500.0, 580.0)), "distant tiles do not weld"

    doc = pymupdf.open(pdf_path)
    prim = extract_page_primitives(doc, 30)
    layout = detect_layout(prim)
    markers = detect_markers(prim, layout=layout)
    columns = sorted(layout.columns, key=lambda c: c.x0)
    gutters = [
        (a.x1 + b.x0) / 2.0 for a, b in zip(columns, columns[1:]) if b.x0 > a.x1
    ]
    figures = detect_figures(
        prim.images, prim.drawings, _text_lines(
            [s for s in prim.spans
             if s.bbox[1] >= layout.content_box[1] - 2.0
             and s.bbox[3] <= layout.content_box[3] + 2.0]
        ),
        layout.content_box,
        marker_rects=[m.span.bbox for m in markers],
        gutters=gutters,
    )
    assert figures, "sheet 30 is mostly photographs"
    for fig in figures:
        for g in gutters:
            # A photograph set to the full measure of its column overhangs the
            # channel by a point or two; what must not happen is a figure with
            # real extent on both sides of it.
            assert not (fig.art[0] < g - 8.0 and fig.art[2] > g + 8.0), (
                f"figure {fig.rect} welded across the gutter at {g}"
            )
        held = [
            m.label for m in markers
            if fig.rect[1] <= (m.span.bbox[1] + m.span.bbox[3]) / 2.0 <= fig.rect[3]
            and fig.rect[0] - 44.0 <= m.span.bbox[0] <= fig.rect[2] + 44.0
        ]
        assert len(held) < 2, f"figure {fig.rect} spans activities {held}"
    print(f"  ✓ p30 welds {len(figures)} figures, none across a gutter or a marker")


def test_activity_grows_over_its_graphic(pdf_path: str = PDF_ELT):
    """
    Sheet 33 activity 'd' says "Look at the graphic below". The graphic is a
    labelled arrow two hundred points wide and twenty high -- too shallow to
    pass for a picture on its shorter side, and set in type too small for the
    flow to read -- so the hotspot used to stop at the end of the sentence.
    """
    if _skip_unless(pdf_path):
        return
    doc = pymupdf.open(pdf_path)
    prim = extract_page_primitives(doc, 33)
    layout = detect_layout(prim)
    acts = grow_activity_regions(
        prim, layout, detect_activity_markers(prim, layout=layout)
    )
    act_d = next(a for a in acts if a.label == "d")
    # The arrow sits at y 66..85 and its %100 / %0 labels at 54..65.
    assert act_d.rect[1] <= 60.0, f"graphic not covered by 'd': {act_d.rect}"
    assert act_d.rect[3] >= 110.0, f"instruction lost from 'd': {act_d.rect}"
    print(f"  ✓ p33 activity 'd' covers its arrow graphic: {act_d.rect}")


if __name__ == "__main__":
    test_question_grammar()
    test_unlabelled_activity_takes_its_chart()
    test_prompt_defers_to_an_enumerated_run()
    test_figure_weld()
    test_activity_grows_over_its_graphic()
    print("\nAll prompt & figure checks passed.")
