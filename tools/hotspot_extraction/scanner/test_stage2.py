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
    PageGeometry,
    snap_edges,
    clean_page_activities,
    detect_solution_spaces,
    cuts,
    rect_overlap,
    rect_area,
    MIN_HOTSPOT,
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
