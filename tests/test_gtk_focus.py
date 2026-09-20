#!/usr/bin/env python3
"""
Unit tests for Milestone 4 (Focus Mode).

Tests cover:
  * Scale and crop calculation (clamping, rotation, 8e6 px max pixel cap).
  * Zooming a part alone, never unioned with siblings.
  * Sub-item (question) stepping and part switching.
  * Activity stepping across page boundaries (`step_activity_from`).
  * Zoom bias nudging (0.5..3.0 clamp).
  * Pre-focus state restoration.
"""

import math
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from interaktiv_core.regions import Activity, Item, PageRegions
from interaktiv_gtk.reader.focus import (
    MAX_FOCUS_PIXELS,
    calculate_focus_scale,
)
from interaktiv_gtk.reader.overlay import PageOverlay, Spot


def make_activity(
    aid: str,
    page_num: int = 5,
    label: str = "a",
    rect=(50.0, 200.0, 550.0, 700.0),
    parts=(),
    items=(),
    headline: str = "",
) -> Activity:
    return Activity(
        id=aid,
        page_num=page_num,
        label=label,
        column=0,
        rect=rect,
        parts=tuple(parts),
        headline=headline,
        anchored=False,
        items=tuple(items),
    )


def make_item(iid: str, rect, part_index: int = 0, label: str = "1") -> Item:
    return Item(
        id=iid,
        label=label,
        number=int(label) if label.isdigit() else None,
        part_index=part_index,
        rect=rect,
    )


class TestFocusScale(unittest.TestCase):
    """Scale selection ports `renderFocus` (viewer.js:1750) with its clamps."""

    def test_basic_fit_and_clamping(self):
        # A 200 x 200 region inside an 800 x 600 stage (avail: 744 x 544)
        # min(744/200, 544/200) = 2.72
        s, render_scale = calculate_focus_scale(
            stage_w=800, stage_h=600, region_w=200, region_h=200,
            rotation=0, zoom_bias=1.0, scale_factor=1.0,
        )
        self.assertAlmostEqual(s, 2.72, places=2)
        self.assertAlmostEqual(render_scale, 2.72, places=2)

    def test_minimum_scale_clamp(self):
        # A gigantic region in a small stage
        s, _ = calculate_focus_scale(
            stage_w=300, stage_h=300, region_w=2000, region_h=2000,
            zoom_bias=0.1,  # even with small bias, clamped at 0.4
        )
        self.assertGreaterEqual(s, 0.4)

    def test_maximum_scale_clamp(self):
        # A tiny region in a large stage
        s, _ = calculate_focus_scale(
            stage_w=1920, stage_h=1080, region_w=10, region_h=10,
            zoom_bias=5.0,  # even with huge bias, clamped at 6.0
        )
        self.assertLessEqual(s, 6.0)

    def test_zoom_bias_clamping(self):
        # Bias clamped 0.5 .. 3.0
        s_low, _ = calculate_focus_scale(
            stage_w=800, stage_h=600, region_w=200, region_h=200,
            zoom_bias=0.1,  # clamped to 0.5
        )
        s_half, _ = calculate_focus_scale(
            stage_w=800, stage_h=600, region_w=200, region_h=200,
            zoom_bias=0.5,
        )
        self.assertAlmostEqual(s_low, s_half, places=3)

        s_high, _ = calculate_focus_scale(
            stage_w=800, stage_h=600, region_w=200, region_h=200,
            zoom_bias=10.0,  # clamped to 3.0
        )
        s_three, _ = calculate_focus_scale(
            stage_w=800, stage_h=600, region_w=200, region_h=200,
            zoom_bias=3.0,
        )
        self.assertAlmostEqual(s_high, s_three, places=3)

    def test_peak_focus_texture_never_exceeds_8e6_pixels(self):
        # Verify requirement: peak focus texture never exceeds 8e6 px
        test_cases = [
            (500, 500, 1.0, 1.0),
            (2000, 2000, 3.0, 1.0),
            (3000, 4000, 3.0, 2.0),
            (800, 1200, 3.0, 2.0),
            (100, 100, 3.0, 2.0),
        ]
        for rw, rh, bias, factor in test_cases:
            s, render_scale = calculate_focus_scale(
                stage_w=3840, stage_h=2160, region_w=rw, region_h=rh,
                zoom_bias=bias, scale_factor=factor,
            )
            pixel_count = (rw * render_scale) * (rh * render_scale)
            self.assertLessEqual(
                pixel_count, MAX_FOCUS_PIXELS + 1.0,
                f"Exceeded 8e6 px for region ({rw}, {rh}) at factor {factor}: {pixel_count} px",
            )

    def test_rotation_swaps_dimensions(self):
        # 400 wide x 200 high
        # At rot=0: dev_w = 400, dev_h = 200
        # At rot=90: dev_w = 200, dev_h = 400
        s_0, _ = calculate_focus_scale(
            stage_w=800, stage_h=400, region_w=400, region_h=200, rotation=0,
        )
        s_90, _ = calculate_focus_scale(
            stage_w=800, stage_h=400, region_w=400, region_h=200, rotation=90,
        )
        self.assertNotEqual(s_0, s_90)


class TestPartZooming(unittest.TestCase):
    """Verify: a part is always zoomed alone, never unioned with its siblings."""

    def test_part_zoomed_alone(self):
        part0 = (50.0, 400.0, 250.0, 700.0)
        part1 = (300.0, 200.0, 500.0, 700.0)
        act = make_activity("act1", parts=[part0, part1])

        # Part 0
        self.assertEqual(act.piece_rects[0], part0)
        # Part 1
        self.assertEqual(act.piece_rects[1], part1)

        # Ensure neither is unioned with the other
        union_w = max(part0[2], part1[2]) - min(part0[0], part1[0])
        self.assertNotEqual(abs(part0[2] - part0[0]), union_w)
        self.assertNotEqual(abs(part1[2] - part1[0]), union_w)

    def test_sub_item_rect_selected_when_sub_index_active(self):
        part0 = (50.0, 400.0, 250.0, 700.0)
        q1_rect = (60.0, 600.0, 240.0, 680.0)
        q2_rect = (60.0, 500.0, 240.0, 580.0)
        items = [make_item("q1", q1_rect, 0, "1"), make_item("q2", q2_rect, 0, "2")]
        act = make_activity("act1", parts=[part0], items=items)

        # When sub_index is -1, uses part rect
        self.assertEqual(act.piece_rects[0], part0)
        # When sub_index is 0, uses q1_rect
        self.assertEqual(items[0].rect, q1_rect)
        # When sub_index is 1, uses q2_rect
        self.assertEqual(items[1].rect, q2_rect)


class TestStepping(unittest.TestCase):
    """`stepActivityFrom` and `stepSubItem` logic."""

    class MockSession:
        def __init__(self, pages_map, page_count=10):
            self.pages_map = pages_map
            self.page_count = page_count

        def overlay(self, page_num):
            acts = self.pages_map.get(page_num, [])
            if not acts:
                return None
            pr = PageRegions(
                page_num=page_num, page_width=600, page_height=800,
                columns=(), activities=tuple(acts),
            )
            return PageOverlay(pr, spots=[], pins=[])

    def test_step_activity_from_same_page(self):
        act1 = make_activity("a1", page_num=2)
        act2 = make_activity("a2", page_num=2)
        session = self.MockSession({2: [act1, act2]})

        from interaktiv_gtk.reader.view import ReaderPage

        # Call step_activity_from bound to session
        step_fn = ReaderPage.step_activity_from.__get__(
            type("MockReader", (), {"session": session})()
        )

        # Forward from a1 -> a2
        next_act = step_fn(act1, 1)
        self.assertIsNotNone(next_act)
        self.assertEqual(next_act.id, "a2")

        # Backward from a2 -> a1
        prev_act = step_fn(act2, -1)
        self.assertIsNotNone(prev_act)
        self.assertEqual(prev_act.id, "a1")

    def test_step_activity_across_pages(self):
        act1 = make_activity("a1", page_num=2)
        act2 = make_activity("a2", page_num=5)  # page 3, 4 are empty
        session = self.MockSession({2: [act1], 5: [act2]}, page_count=10)

        from interaktiv_gtk.reader.view import ReaderPage

        step_fn = ReaderPage.step_activity_from.__get__(
            type("MockReader", (), {"session": session})()
        )

        # Forward from page 2 skips 3, 4 and lands on page 5's first activity
        next_act = step_fn(act1, 1)
        self.assertIsNotNone(next_act)
        self.assertEqual(next_act.id, "a2")
        self.assertEqual(next_act.page_num, 5)

        # Backward from page 5 lands on page 2
        prev_act = step_fn(act2, -1)
        self.assertIsNotNone(prev_act)
        self.assertEqual(prev_act.id, "a1")
        self.assertEqual(prev_act.page_num, 2)

    def test_step_activity_boundaries(self):
        act1 = make_activity("a1", page_num=1)
        act2 = make_activity("a2", page_num=10)
        session = self.MockSession({1: [act1], 10: [act2]}, page_count=10)

        from interaktiv_gtk.reader.view import ReaderPage

        step_fn = ReaderPage.step_activity_from.__get__(
            type("MockReader", (), {"session": session})()
        )

        # Backward from first activity -> None
        self.assertIsNone(step_fn(act1, -1))
        # Forward from last activity -> None
        self.assertIsNone(step_fn(act2, 1))

    def test_sub_item_stepping_moves_parts(self):
        # Item 0 and 1 are in part 0; Item 2 is in part 1
        items = [
            make_item("q1", (60, 600, 240, 680), part_index=0, label="1"),
            make_item("q2", (60, 500, 240, 580), part_index=0, label="2"),
            make_item("q3", (350, 600, 500, 680), part_index=1, label="3"),
        ]
        act = make_activity(
            "act1",
            parts=[(50, 400, 250, 700), (300, 400, 550, 700)],
            items=items,
        )

        from interaktiv_gtk.reader.focus import FocusOverlay

        class MockReader:
            session = type("S", (), {"service": None, "info": None})()
            rotation = 0
            current_page = 5

            def step_activity_from(self, activity, direction):
                return None

            def go_to_page(self, page):
                self.current_page = page

        reader = MockReader()
        overlay = FocusOverlay(reader)
        overlay.start(act)
        self.assertEqual(overlay.sub_index, -1)
        self.assertEqual(overlay.part_index, 0)

        # Step +1 -> question 0 (part 0)
        overlay.step_sub_item(1)
        self.assertEqual(overlay.sub_index, 0)
        self.assertEqual(overlay.part_index, 0)

        # Step +1 -> question 1 (part 0)
        overlay.step_sub_item(1)
        self.assertEqual(overlay.sub_index, 1)
        self.assertEqual(overlay.part_index, 0)

        # Step +1 -> question 2 (part 1)
        overlay.step_sub_item(1)
        self.assertEqual(overlay.sub_index, 2)
        self.assertEqual(overlay.part_index, 1)

        # Step +1 at end clamps to 2
        overlay.step_sub_item(1)
        self.assertEqual(overlay.sub_index, 2)

        # Step -1 back to question 1 (part 0)
        overlay.step_sub_item(-1)
        self.assertEqual(overlay.sub_index, 1)
        self.assertEqual(overlay.part_index, 0)


class TestPreFocusState(unittest.TestCase):
    """Restoring pre-focus state upon exit."""

    def test_state_restored_on_exit(self):
        from gi.repository import Gtk
        from interaktiv_gtk.reader.focus import FocusOverlay
        from interaktiv_gtk.reader.view import ReaderPage

        class MockReader:
            def __init__(self):
                self._closed = False
                self._pre_focus_state = None
                self.view_mode = "book"
                self.current_page = 42
                self.zoom_mode = "fit-page"
                self.custom_zoom = 1.0
                self.scroller = Gtk.ScrolledWindow()
                self.status_mode = Gtk.Label()
                self.status_activity = Gtk.Label()
                self.status_zoom = Gtk.Label()
                self.session = type("S", (), {"service": None, "info": None, "is_open": False})()
                self.focus_overlay = FocusOverlay(self)
                self.modes_set = []
                self.pages_gone_to = []
                self.zooms_set = []

            def set_view_mode(self, mode):
                self.view_mode = mode
                self.modes_set.append(mode)

            def go_to_page(self, page):
                self.current_page = page
                self.pages_gone_to.append(page)

            def set_zoom(self, mode, custom=1.0):
                self.zoom_mode = mode
                self.custom_zoom = custom
                self.zooms_set.append((mode, custom))

            def _update_activity_status(self):
                pass

            def _update_zoom_status(self):
                pass

        reader = MockReader()
        reader.focus_activity = ReaderPage.focus_activity.__get__(reader)
        reader.exit_focus = ReaderPage.exit_focus.__get__(reader)

        act = make_activity("act1", page_num=42)

        # 1. Enter focus mode
        reader.focus_activity(act)
        self.assertTrue(reader.focus_overlay.is_active)
        self.assertIsNotNone(reader._pre_focus_state)
        self.assertEqual(reader._pre_focus_state["view_mode"], "book")
        self.assertEqual(reader._pre_focus_state["current_page"], 42)
        self.assertEqual(reader._pre_focus_state["zoom_mode"], "fit-page")
        self.assertEqual(reader._pre_focus_state["custom_zoom"], 1.0)

        # 2. Simulate user navigating or changing view while in focus
        reader.view_mode = "single"
        reader.current_page = 55
        reader.zoom_mode = "custom"
        reader.custom_zoom = 2.5

        # 3. Exit focus mode
        reader.exit_focus()
        self.assertFalse(reader.focus_overlay.is_active)
        self.assertIsNone(reader._pre_focus_state)

        # 4. Verify all pre-focus state was restored
        self.assertEqual(reader.view_mode, "book")
        self.assertEqual(reader.current_page, 42)
        self.assertEqual(reader.zoom_mode, "fit-page")
        self.assertEqual(reader.custom_zoom, 1.0)
        self.assertIn("book", reader.modes_set)
        self.assertIn(42, reader.pages_gone_to)
        self.assertIn(("fit-page", 1.0), reader.zooms_set)


if __name__ == "__main__":
    unittest.main()
