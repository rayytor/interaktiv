"""
Unit tests for Stage 2 scanner fixes.

Validates:
1. snap_edges: expand-or-retreat eliminates partial block cuts.
2. clean_page_activities: guarantees 0 overlaps even when geom is None.
3. detect_solution_spaces: supports vertical-only tables, throttles dense mesh grids,
   and prevents linking across intervening prose text.
4. Anchored region line/figure snapping: prevents slicing text lines mid-word.
"""

import pytest
from typing import List, Tuple, Optional
from tools.hotspot_extraction.scanner.primitives import (
    PagePrimitives,
    TextSpan,
    VectorDrawing,
)
from tools.hotspot_extraction.scanner.layout import PageLayout, Column
from tools.hotspot_extraction.scanner.regions import (
    ActivityRegion,
    SubItemRect,
    PageGeometry,
    snap_edges,
    clean_page_activities,
    detect_solution_spaces,
    cuts,
    rect_overlap,
    rect_area,
    tall_floor,
    MIN_HOTSPOT,
    TALL_REGION,
)
from tools.hotspot_extraction.scanner.anchors import (
    PublisherOge,
    _create_anchored_region,
)


def make_drawing(rect: Tuple[float, float, float, float], is_rect: bool = True, stroke=(0, 0, 0), fill=None, width=1.0):
    return VectorDrawing(
        rect=rect,
        fill=fill,
        stroke=stroke,
        width=width,
        is_rect=is_rect,
        lines=[rect],
    )


def make_span(text: str, bbox: Tuple[float, float, float, float], size: float = 10.0, font: str = "Helvetica"):
    return TextSpan(
        text=text,
        bbox=bbox,
        font=font,
        size=size,
        flags=0,
        color=0,
    )


class TestSnapEdges:
    """Test snap_edges resolution of partial cuts."""

    def test_expand_on_substantial_coverage(self):
        """A region covering >= 35% of a block expands to take it whole."""
        block = {"rect": (50.0, 100.0, 250.0, 200.0), "kind": "panel"}
        act = ActivityRegion(
            id="p1-1",
            label="1",
            column=0,
            rect=(40.0, 140.0, 260.0, 300.0),
            parts=[(40.0, 140.0, 260.0, 300.0)],
        )
        geom = PageGeometry(
            lines=[],
            blocks=[block],
            content_box=(40.0, 50.0, 550.0, 750.0),
        )

        assert cuts(act.parts[0], block["rect"])
        snapped = snap_edges([act], geom)
        part = snapped[0].parts[0]
        assert part[1] <= 100.0
        assert not cuts(part, block["rect"])

    def test_retreat_on_grazing_coverage(self):
        """A region barely grazing a block (< 25% share) retreats off it."""
        block = {"rect": (50.0, 200.0, 250.0, 300.0), "kind": "panel"}
        act = ActivityRegion(
            id="p1-1",
            label="1",
            column=0,
            rect=(40.0, 100.0, 260.0, 210.0),
            parts=[(40.0, 100.0, 260.0, 210.0)],
        )
        geom = PageGeometry(
            lines=[],
            blocks=[block],
            content_box=(40.0, 50.0, 550.0, 750.0),
        )

        assert cuts(act.parts[0], block["rect"])
        snapped = snap_edges([act], geom)
        part = snapped[0].parts[0]
        assert part[3] <= 200.0
        assert not cuts(part, block["rect"])

    def test_expand_blocked_by_neighbour_retreats_instead(self):
        """If expanding would overlap an adjacent activity, it retreats instead."""
        block = {"rect": (50.0, 150.0, 250.0, 250.0), "kind": "panel"}
        act_a = ActivityRegion(
            id="p1-1",
            label="1",
            column=0,
            rect=(40.0, 100.0, 260.0, 200.0),
            parts=[(40.0, 100.0, 260.0, 200.0)],
        )
        act_b = ActivityRegion(
            id="p1-2",
            label="2",
            column=0,
            rect=(40.0, 201.0, 260.0, 350.0),
            parts=[(40.0, 201.0, 260.0, 350.0)],
        )
        geom = PageGeometry(
            lines=[],
            blocks=[block],
            content_box=(40.0, 50.0, 550.0, 750.0),
        )

        snapped = snap_edges([act_a, act_b], geom)
        part_a = snapped[0].parts[0]
        part_b = snapped[1].parts[0]
        assert rect_overlap(part_a, part_b) <= 1.0


class TestSnapAbsorbsItsOwnPieces:
    """A piece that grows to take a block whole may take its sibling with it, never half of one."""

    def geom(self, blocks):
        return PageGeometry(lines=[], blocks=blocks, content_box=(28.0, 40.0, 525.0, 740.0),
                            page_w=552.8, page_h=779.5)

    def test_a_swallowed_sibling_is_dropped_and_its_items_follow(self):
        panel = {"rect": (60.0, 232.0, 496.0, 696.0), "kind": "panel"}
        big, small = (34.0, 420.0, 497.0, 696.0), (272.0, 386.0, 405.0, 420.0)
        act = ActivityRegion(
            id="p1-3", label="3", column=0, rect=(34.0, 386.0, 497.0, 696.0), parts=[big, small],
            items=[SubItemRect(id="p1-3-1", label=None, number=1, part_index=1, rect=small)],
        )
        out = snap_edges([act], self.geom([panel]))[0]
        assert len(out.parts) == 1
        assert not cuts(out.parts[0], panel["rect"])
        assert out.items[0].part_index == 0

    def test_a_sibling_only_partly_covered_blocks_the_expansion(self):
        panel = {"rect": (60.0, 232.0, 496.0, 696.0), "kind": "panel"}
        big, astride = (34.0, 420.0, 497.0, 696.0), (272.0, 200.0, 405.0, 419.0)
        act = ActivityRegion(id="p1-3", label="3", column=0, rect=(34.0, 200.0, 497.0, 696.0),
                             parts=[big, astride])
        out = snap_edges([act], self.geom([panel]))[0]
        for i, p in enumerate(out.parts):
            for q in out.parts[i + 1:]:
                assert rect_overlap(p, q) <= 1.0


class TestTallRegions:
    """The tall rule is a share of the real sheet, and enforcing it never slices a block."""

    # A 780 pt sheet: shorter than A4, which is where the old A4 floor let
    # regions run to 0.75 of the sheet.
    W, H = 552.8, 779.5

    def geom(self, blocks=(), lines=()):
        return PageGeometry(
            lines=list(lines),
            blocks=[dict(b) for b in blocks],
            content_box=(28.0, 40.0, 525.0, 740.0),
            page_w=self.W,
            page_h=self.H,
        )

    def test_the_cleanup_measures_the_sheet_it_is_given(self):
        part = (40.0, 140.0, 500.0, 717.0)          # 0.74 of the sheet
        act = ActivityRegion(id="p1-1", label="1", column=0, rect=part, parts=[part])
        out = clean_page_activities([act], geom=self.geom())
        top_to_bottom = out[0].parts[0][3] - out[0].parts[0][1]
        assert top_to_bottom <= TALL_REGION * self.H

    def test_snapping_does_not_grow_a_region_past_the_sheet(self):
        grid = {"rect": (42.0, 140.0, 497.0, 603.0), "kind": "grid"}
        part = (34.0, 176.0, 506.0, 717.0)          # the grid whole would be 0.74
        act = ActivityRegion(id="p1-1", label="1", column=0, rect=part, parts=[part])
        out = snap_edges([act], self.geom([grid]))
        p = out[0].parts[0]
        assert p[3] - p[1] <= TALL_REGION * self.H

    def test_the_floor_leaves_a_block_out_whole(self):
        grid = {"rect": (42.0, 140.0, 497.0, 603.0), "kind": "grid"}
        part = (34.0, 140.0, 506.0, 717.0)
        max_h = (TALL_REGION - 0.005) * self.H
        floor = tall_floor(self.geom([grid]), part, max_h)
        assert floor == pytest.approx(603.0)
        assert not cuts((part[0], floor, part[2], part[3]), grid["rect"])

    def test_the_floor_falls_between_lines_not_through_one(self):
        max_h = (TALL_REGION - 0.005) * self.H
        part = (34.0, 100.0, 506.0, 717.0)
        limit = part[3] - max_h
        # A line of type straddling the limit, and clear space above it.
        line = {"x0": 40.0, "x1": 500.0, "y0": limit - 4.0, "y1": limit + 6.0}
        above = {"x0": 40.0, "x1": 500.0, "y0": limit + 20.0, "y1": limit + 30.0}
        floor = tall_floor(self.geom(lines=[line, above]), part, max_h)
        assert line["y1"] <= floor <= above["y0"]

    def test_with_no_seam_the_floor_is_the_limit(self):
        max_h = (TALL_REGION - 0.005) * self.H
        part = (34.0, 100.0, 506.0, 717.0)
        assert tall_floor(None, part, max_h) == pytest.approx(part[3] - max_h)


class TestCleanPageActivitiesZeroOverlaps:
    """Test that clean_page_activities guarantees zero overlaps even with geom=None."""

    def test_deoverlap_without_geom(self):
        """Two overlapping activities separated cleanly at midpoint when geom=None."""
        act_a = ActivityRegion(
            id="p1-1",
            label="1",
            column=0,
            rect=(50.0, 100.0, 250.0, 220.0),
            parts=[(50.0, 100.0, 250.0, 220.0)],
        )
        act_b = ActivityRegion(
            id="p1-2",
            label="2",
            column=0,
            rect=(50.0, 200.0, 250.0, 320.0),
            parts=[(50.0, 200.0, 250.0, 320.0)],
        )

        assert rect_overlap(act_a.rect, act_b.rect) > 1.0

        cleaned = clean_page_activities([act_a, act_b], geom=None)
        assert len(cleaned) == 2
        pa = cleaned[0].parts[0]
        pb = cleaned[1].parts[0]
        ov = rect_overlap(pa, pb)
        assert ov <= 1.0, f"Expected zero overlap, got {ov}"

    def test_horizontal_deoverlap_without_geom(self):
        """Two side-by-side overlapping activities separated horizontally at midpoint."""
        act_a = ActivityRegion(
            id="p1-1",
            label="1",
            column=0,
            rect=(50.0, 100.0, 160.0, 200.0),
            parts=[(50.0, 100.0, 160.0, 200.0)],
        )
        act_b = ActivityRegion(
            id="p1-2",
            label="2",
            column=1,
            rect=(150.0, 100.0, 260.0, 200.0),
            parts=[(150.0, 100.0, 260.0, 200.0)],
        )

        assert rect_overlap(act_a.rect, act_b.rect) > 1.0
        cleaned = clean_page_activities([act_a, act_b], geom=None)
        assert len(cleaned) == 2
        pa = cleaned[0].parts[0]
        pb = cleaned[1].parts[0]
        assert rect_overlap(pa, pb) <= 1.0


class TestDetectSolutionSpaces:
    """Test solution space detection improvements."""

    def test_vertical_only_table_detected(self):
        """A table ruled with vertical column dividers produces solution cells."""
        v1 = make_drawing(rect=(50.0, 100.0, 51.0, 200.0))
        v2 = make_drawing(rect=(150.0, 100.0, 151.0, 200.0))
        v3 = make_drawing(rect=(250.0, 100.0, 251.0, 200.0))
        drawings = [v1, v2, v3]
        spans = []

        blocks = detect_solution_spaces(drawings, spans)
        cells = [b for b in blocks if b["kind"] == "cell"]
        assert len(cells) >= 1

    def test_intervening_prose_prevents_merging_tables(self):
        """Rules of two separate exercises separated by question prose are not merged."""
        t1_r1 = make_drawing(rect=(50.0, 450.0, 500.0, 451.0))
        t1_r2 = make_drawing(rect=(50.0, 400.0, 500.0, 401.0))
        t1_r3 = make_drawing(rect=(50.0, 350.0, 500.0, 351.0))

        t2_r1 = make_drawing(rect=(50.0, 300.0, 500.0, 301.0))
        t2_r2 = make_drawing(rect=(50.0, 250.0, 500.0, 251.0))
        t2_r3 = make_drawing(rect=(50.0, 200.0, 500.0, 201.0))

        question_text = make_span(
            text="2. Aşağıdaki soruları cevaplayınız.",
            bbox=(50.0, 320.0, 300.0, 332.0),
            size=10.0,
        )

        drawings = [t1_r1, t1_r2, t1_r3, t2_r1, t2_r2, t2_r3]
        spans = [question_text]

        blocks = detect_solution_spaces(drawings, spans)
        for b in blocks:
            rect = b["rect"]
            assert (rect[3] - rect[1]) < 200.0, f"Table merged across question prose: {rect}"

    def test_dense_mesh_throttled(self):
        """A dense mesh (like graph paper) emits a grid without tens of thousands of cells."""
        drawings = []
        for i in range(25):
            y = 100.0 + i * 5.0
            drawings.append(make_drawing(rect=(50.0, y, 170.0, y + 0.5)))
        for j in range(25):
            x = 50.0 + j * 5.0
            drawings.append(make_drawing(rect=(x, 100.0, x + 0.5, 220.0)))

        blocks = detect_solution_spaces(drawings, [])
        cells = [b for b in blocks if b["kind"] == "cell"]
        assert len(cells) == 0
        grids = [b for b in blocks if b["kind"] == "grid"]
        assert len(grids) >= 1


class TestAnchoredSnapping:
    """Test line-boundary snapping and figure snapping for anchored regions."""

    def test_snaps_to_line_boundaries_not_mid_word(self):
        """Anchored region top and bottom edges snap cleanly to text line edges."""
        oge = PublisherOge(
            id="oge-1",
            title="Question",
            sayfano=1,
            posx=10.0,
            posy=50.0,
            data="data-1",
        )
        line_spans = [
            make_span(text="İlk", bbox=(50.0, 415.0, 80.0, 427.0), size=10.0),
            make_span(text="cümle", bbox=(85.0, 415.0, 120.0, 427.0), size=10.0),
        ]
        next_spans = [
            make_span(text="İkinci", bbox=(50.0, 395.0, 90.0, 407.0), size=10.0),
            make_span(text="cümle", bbox=(95.0, 395.0, 130.0, 407.0), size=10.0),
        ]
        prims = PagePrimitives(
            page_num=1,
            width=595.0,
            height=842.0,
            spans=line_spans + next_spans,
            drawings=[],
            images=[],
        )
        layout = PageLayout(
            columns=[Column(index=0, x0=40.0, x1=550.0, opens_at=750.0, closes_at=50.0)],
            content_box=(40.0, 50.0, 550.0, 750.0),
            body_font_size=10.0,
        )

        act = _create_anchored_region(
            oge=oge,
            page_num=1,
            page_width=595.0,
            page_height=842.0,
            primitives=prims,
            layout=layout,
        )

        assert act.rect[3] >= 427.0
        assert act.rect[1] <= 395.0
