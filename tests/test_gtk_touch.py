#!/usr/bin/env python3
"""
The touch layer of the School Edition: what a finger is taken to have meant.

Nothing here opens a window. What is tested is the arithmetic a gesture runs
on -- when a drag has become a swipe, which way it points, and how big a target
has to be -- because those are the rules that decide whether a teacher standing
at a smartboard gets the page they asked for or the one after it.

The two-stage shape of the swipe rule is the part worth guarding. A verdict has
to be reached early, off a few pixels of travel, because `GtkScrolledWindow`
claims a touch drag the moment it passes GTK's drag threshold and everything
below it is cancelled; but whether the flick was *far* enough is only knowable
when the finger lifts. Getting the first stage wrong steals scrolling; getting
the second wrong turns pages by accident. They are tested separately.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from unittest.mock import MagicMock, patch  # noqa: E402

# Imported before `gi.repository`: the package pins the GTK, Gdk and Adw
# versions, and naming them afterwards is what the warning is about.
from interaktiv_gtk import touch  # noqa: E402
from interaktiv_gtk.reader import holdrepeat  # noqa: E402

from gi.repository import GLib, Gtk  # noqa: E402


class TestSwipeVerdict(unittest.TestCase):
    """`decide_swipe` -- sideways, not sideways, or not yet."""

    SAMPLE = 12.0

    def decide(self, dx, dy):
        return touch.decide_swipe(dx, dy, self.SAMPLE)

    def test_a_drag_that_has_barely_moved_is_undecided(self):
        # Neither axis has reached the sample, so there is nothing to read a
        # direction from yet. Answering here would be a coin toss.
        self.assertIsNone(self.decide(4.0, 3.0))
        self.assertIsNone(self.decide(-11.9, 0.0))

    def test_either_axis_reaching_the_sample_forces_a_verdict(self):
        # The scroller claims at the threshold whichever way the finger went,
        # so a vertical drag has to be answered then too -- with "not mine".
        self.assertFalse(self.decide(0.0, 20.0))
        self.assertTrue(self.decide(20.0, 0.0))

    def test_sideways_travel_reads_as_a_swipe(self):
        self.assertTrue(self.decide(30.0, 5.0))
        self.assertTrue(self.decide(-30.0, -5.0))

    def test_a_drag_down_the_page_is_left_to_the_scroller(self):
        self.assertFalse(self.decide(5.0, 30.0))
        self.assertFalse(self.decide(-5.0, -30.0))

    def test_a_diagonal_drag_is_left_to_the_scroller(self):
        # Equal parts sideways and down is not a flick, it is a smear. The
        # bias means it has to be clearly more one than the other.
        self.assertFalse(self.decide(20.0, 20.0))
        self.assertFalse(self.decide(-22.0, 20.0))

    def test_the_bias_is_where_the_verdict_turns(self):
        dy = 20.0
        just_under = dy * touch.SWIPE_BIAS - 0.5
        just_over = dy * touch.SWIPE_BIAS + 0.5
        self.assertFalse(self.decide(just_under, dy))
        self.assertTrue(self.decide(just_over, dy))


class TestSwipeDirection(unittest.TestCase):
    """`swipe_direction` -- how far is far enough, and which way."""

    def test_a_short_flick_turns_nothing(self):
        # A tap that slid a little, or a scroll that wandered sideways.
        self.assertEqual(touch.swipe_direction(20.0, 0.0), 0)
        self.assertEqual(touch.swipe_direction(-touch.SWIPE_TRAVEL + 1, 0.0), 0)

    def test_the_page_follows_the_finger(self):
        # Dragging the sheet to the right pulls the previous page into view,
        # the way a paper book moves under the same hand.
        self.assertEqual(touch.swipe_direction(touch.SWIPE_TRAVEL + 10, 0.0), -1)
        self.assertEqual(touch.swipe_direction(-touch.SWIPE_TRAVEL - 10, 0.0), 1)

    def test_a_long_but_mostly_vertical_drag_turns_nothing(self):
        # Reaching the travel sideways is not enough on its own: a diagonal
        # drag across a big board covers it while plainly meaning "scroll".
        far = touch.SWIPE_TRAVEL + 10
        self.assertEqual(touch.swipe_direction(far, far), 0)

    def test_the_travel_is_out_of_reach_of_a_tap(self):
        # The two thresholds must not overlap, or a wobbling fingertip held
        # against the glass could turn a page on release.
        self.assertGreater(touch.SWIPE_TRAVEL, touch.TAP_SLOP)


class TestDecisionWindow(unittest.TestCase):
    """The constant that makes the whole arrangement work."""

    def test_the_verdict_lands_before_the_scroller_claims(self):
        # `GtkScrolledWindow` claims on the first motion *past* the drag
        # threshold. A swipe that decided at or after that point would already
        # have been cancelled, so the fraction has to be under one.
        self.assertLess(touch.DECISION_FRACTION, 1.0)
        self.assertGreater(touch.DECISION_FRACTION, 0.0)

    def test_the_sample_is_long_enough_to_have_a_direction(self):
        # Under a few pixels the ratio between dx and dy is jitter rather than
        # intent. The app widens GTK's mouse-sized threshold for exactly this:
        # the two numbers together are what the verdict is read from.
        from interaktiv_gtk.app import TOUCH_DRAG_THRESHOLD
        sample = TOUCH_DRAG_THRESHOLD * touch.DECISION_FRACTION
        self.assertGreaterEqual(sample, 8.0)


class TestHoldRepeat(unittest.TestCase):
    """
    `bind_hold_repeat` -- when a page turn commits.

    The gesture is driven by emitting its own signals rather than by faking
    input events, which GTK4 has no way to inject. That reaches the whole of
    the state machine, which is the part that has to be right: the two edge
    strips lie over the sheet itself, so every rule here is what keeps a
    teacher's pan or swipe from being counted as a page.
    """

    def setUp(self):
        self._real_is_touch = holdrepeat.is_touch
        self.addCleanup(self._restore)

    def _restore(self):
        holdrepeat.is_touch = self._real_is_touch

    def _bind(self, touching: bool):
        holdrepeat.is_touch = lambda _gesture: touching
        button = Gtk.Button()
        turns = []
        holdrepeat.bind_hold_repeat(button, lambda: turns.append(1))
        gesture = next(
            c for c in button.observe_controllers()
            if isinstance(c, Gtk.GestureDrag)
        )
        # Kept alive: the controller holds no strong reference to the button,
        # and an unrealized button stops the timers on its way out.
        self._button = button
        return gesture, turns

    @staticmethod
    def _spin(ms: int) -> None:
        loop = GLib.MainLoop()
        GLib.timeout_add(ms, lambda: (loop.quit(), GLib.SOURCE_REMOVE)[1])
        loop.run()

    def test_a_mouse_press_turns_the_page_at_once(self):
        # A button going down is already a decision, and the web version
        # commits there. Nothing more happens on the way back up.
        gesture, turns = self._bind(touching=False)
        gesture.emit("drag-begin", 10.0, 10.0)
        self.assertEqual(len(turns), 1)
        gesture.emit("drag-end", 0.0, 0.0)
        self.assertEqual(len(turns), 1)

    def test_a_tap_turns_one_page_on_release(self):
        # A finger landing is not yet a decision: it may be starting a pan or
        # a swipe. The page turns when it lifts, and turns exactly once.
        gesture, turns = self._bind(touching=True)
        gesture.emit("drag-begin", 10.0, 10.0)
        self.assertEqual(len(turns), 0)
        gesture.emit("drag-end", 0.0, 0.0)
        self.assertEqual(len(turns), 1)

    def test_a_finger_that_travelled_turns_nothing(self):
        # The strip was only where the pan or the swipe began. This is the
        # defect the touch path exists for: turning on the press meant a
        # teacher dragging a zoomed-in page got a page turn instead.
        gesture, turns = self._bind(touching=True)
        gesture.emit("drag-begin", 10.0, 10.0)
        gesture.emit("drag-update", touch.TAP_SLOP + 60.0, 5.0)
        gesture.emit("drag-end", touch.TAP_SLOP + 60.0, 5.0)
        self.assertEqual(len(turns), 0)

    def test_a_wobble_within_the_slop_is_still_a_tap(self):
        gesture, turns = self._bind(touching=True)
        gesture.emit("drag-begin", 10.0, 10.0)
        gesture.emit("drag-update", touch.TAP_SLOP - 1.0, 2.0)
        gesture.emit("drag-end", touch.TAP_SLOP - 1.0, 2.0)
        self.assertEqual(len(turns), 1)

    def test_a_sequence_claimed_elsewhere_turns_nothing(self):
        # The swipe over the reading area took it. Whatever it does with the
        # drag, it is not also a page turn from the strip it started on.
        gesture, turns = self._bind(touching=True)
        gesture.emit("drag-begin", 10.0, 10.0)
        gesture.emit("cancel", None)
        gesture.emit("drag-end", 100.0, 0.0)
        self.assertEqual(len(turns), 0)

    def test_holding_a_finger_down_repeats(self):
        # Twenty taps is the wrong gesture for twenty pages. The hold's first
        # page arrives at the delay -- the press turned nothing -- and the
        # rest follow until the finger lifts.
        gesture, turns = self._bind(touching=True)
        gesture.emit("drag-begin", 10.0, 10.0)
        self._spin(holdrepeat.REPEAT_DELAY_MS - 60)
        self.assertEqual(len(turns), 0)
        self._spin(120)
        self.assertGreaterEqual(len(turns), 1)
        self._spin(holdrepeat.REPEAT_INTERVAL_MS * 3)
        held = len(turns)
        self.assertGreater(held, 1)
        gesture.emit("drag-end", 0.0, 0.0)
        self._spin(holdrepeat.REPEAT_INTERVAL_MS * 3)
        # The release must not add a tap's page on top of the hold's.
        self.assertEqual(len(turns), held)

    def test_keyboard_activation_turns_exactly_one_page(self):
        # Enter and Space raise `clicked` with no pointer sequence in front of
        # it, so there is nothing to swallow and nothing to defer to a release.
        gesture, turns = self._bind(touching=True)
        self._button.emit("clicked")
        self.assertEqual(len(turns), 1)

    def test_a_tap_does_not_also_turn_a_page_through_clicked(self):
        # The button raises its own `clicked` on the way up, right after the
        # release turned the page. Exactly one of the two may count.
        gesture, turns = self._bind(touching=True)
        gesture.emit("drag-begin", 10.0, 10.0)
        gesture.emit("drag-end", 0.0, 0.0)
        self._button.emit("clicked")
        self.assertEqual(len(turns), 1)

    def test_a_hold_that_turns_into_a_drag_stops_repeating(self):
        gesture, turns = self._bind(touching=True)
        gesture.emit("drag-begin", 10.0, 10.0)
        self._spin(holdrepeat.REPEAT_DELAY_MS + 80)
        held = len(turns)
        self.assertGreater(held, 0)
        gesture.emit("drag-update", touch.TAP_SLOP + 60.0, 0.0)
        self._spin(holdrepeat.REPEAT_INTERVAL_MS * 4)
        self.assertEqual(len(turns), held)


class TestEdgePan(unittest.TestCase):
    """
    `EdgePan` -- dragging a page from over the strip that covers it.

    The strips are the scroller's siblings, not its children, so a drag that
    starts inside one never reaches it on its own. What is tested is that the
    drag is carried across when it began on a strip, and is left alone when it
    began anywhere else -- handing the scroller's own drags back to it matters
    as much as taking the ones it cannot see.
    """

    def _rig(self, *, upper=2000.0, page=500.0):
        host = Gtk.Overlay()
        scroller = Gtk.ScrolledWindow()
        host.set_child(scroller)
        edge = Gtk.Button()
        host.add_overlay(edge)

        for adjustment in (scroller.get_hadjustment(), scroller.get_vadjustment()):
            adjustment.set_lower(0.0)
            adjustment.set_upper(upper)
            adjustment.set_page_size(page)
            adjustment.set_value(600.0)

        # Picking needs a realized, allocated window; what these tests are
        # about is what happens *after* the press has been placed, so where it
        # landed is stated directly.
        covered = {"on_edge": True}
        self._edge = edge
        touch.EdgePan(
            host, scroller,
            covers=lambda _widget: covered["on_edge"],
        )
        gesture = next(
            c for c in host.observe_controllers()
            if isinstance(c, Gtk.GestureDrag)
        )
        self._host = host
        return gesture, scroller, covered

    def test_a_drag_from_the_strip_moves_the_page(self):
        gesture, scroller, _ = self._rig()
        vadj = scroller.get_vadjustment()
        gesture.emit("drag-begin", 10.0, 10.0)
        gesture.emit("drag-update", 0.0, -120.0)
        # The page follows the finger: dragging up moves further down the sheet.
        self.assertEqual(vadj.get_value(), 720.0)

    def test_a_drag_that_has_not_become_one_yet_moves_nothing(self):
        gesture, scroller, _ = self._rig()
        vadj = scroller.get_vadjustment()
        gesture.emit("drag-begin", 10.0, 10.0)
        gesture.emit("drag-update", 0.0, -2.0)
        self.assertEqual(vadj.get_value(), 600.0)

    def test_a_drag_from_the_page_itself_is_left_to_the_scroller(self):
        # Taking this would be worse than missing it: the scroller handles it
        # properly, with the throw and the overshoot GTK gives a touch drag.
        gesture, scroller, covered = self._rig()
        covered["on_edge"] = False
        vadj = scroller.get_vadjustment()
        gesture.emit("drag-begin", 10.0, 10.0)
        gesture.emit("drag-update", 0.0, -120.0)
        self.assertEqual(vadj.get_value(), 600.0)

    def test_an_axis_with_no_room_does_not_move(self):
        # A page that fits the frame sideways has nowhere to go sideways, and
        # the sideways swipe is what that drag is for.
        gesture, scroller, _ = self._rig()
        hadj = scroller.get_hadjustment()
        hadj.set_upper(500.0)
        hadj.set_value(0.0)
        gesture.emit("drag-begin", 10.0, 10.0)
        gesture.emit("drag-update", -120.0, 0.0)
        self.assertEqual(hadj.get_value(), 0.0)

    def test_the_page_stops_at_its_edges(self):
        gesture, scroller, _ = self._rig()
        vadj = scroller.get_vadjustment()
        gesture.emit("drag-begin", 10.0, 10.0)
        gesture.emit("drag-update", 0.0, 9000.0)
        self.assertEqual(vadj.get_value(), 0.0)
        gesture.emit("drag-update", 0.0, -9000.0)
        self.assertEqual(vadj.get_value(), 1500.0)


class TestReaderTouchWiring(unittest.TestCase):
    """
    The reader's end of the touch contract, on a page built for real.

    A `ReaderPage` is assembled against a mocked session -- no PDF, no render
    thread -- which is enough to reach every gesture the page installs and the
    zoom state a double tap moves through.
    """

    def _page(self):
        from interaktiv_gtk.reader.view import ReaderPage
        item = MagicMock()
        item.id = "test-book"
        item.title = "Test Book"
        app = MagicMock()
        app.settings.get.return_value = None
        app.manager = MagicMock()

        with patch("interaktiv_gtk.reader.view.DocumentSession") as session_cls, \
             patch("interaktiv_gtk.reader.view.ReaderPage._install_shortcuts"):
            session = MagicMock()
            session.is_open = True
            session.page_count = 10
            session.manager = app.manager
            session_cls.return_value = session
            return ReaderPage(app, item, "/fake/path.pdf")

    def test_a_double_tap_zooms_in_and_back_to_the_same_fit(self):
        # The second tap is the whole zoom control for a teacher standing at
        # the board, so it has to return to the fit the class was reading at
        # rather than to a default.
        from interaktiv_gtk.reader.view import TAP_ZOOM
        page = self._page()
        page.set_zoom("fit-width")
        self.assertEqual(page.zoom_mode, "fit-width")

        page.toggle_tap_zoom()
        self.assertEqual(page.zoom_mode, "custom")
        self.assertEqual(page.custom_zoom, TAP_ZOOM)

        page.toggle_tap_zoom()
        self.assertEqual(page.zoom_mode, "fit-width")

    def test_a_double_tap_out_of_a_stepped_zoom_returns_to_a_fit(self):
        # Zooming with +/- leaves the page on "custom" with no fit remembered.
        # The tap must still have somewhere to go back to.
        page = self._page()
        page.set_zoom("custom", 1.4)
        page.toggle_tap_zoom()
        self.assertIn(page.zoom_mode, ("fit-page", "fit-width"))

    def test_the_page_forwards_a_double_tap_on_bare_paper(self):
        # Wired from the `PageView` that was tapped, through whichever canvas
        # is on screen, to the reader. Both canvases carry it: a book read in
        # scroll mode is still read on a board.
        page = self._page()
        page.toggle_tap_zoom = MagicMock()
        page.spread.emit("zoom-toggled")
        page.scroll_view.emit("zoom-toggled")
        self.assertEqual(page.toggle_tap_zoom.call_count, 2)

    def test_a_swipe_turns_the_page_the_way_the_finger_went(self):
        page = self._page()
        page.next_page = MagicMock()
        page.prev_page = MagicMock()

        page._on_swipe(1)
        page.next_page.assert_called_once()
        page.prev_page.assert_not_called()

        page._on_swipe(-1)
        page.prev_page.assert_called_once()

    def test_both_edge_strips_are_bound_for_touch(self):
        # The strips are the one control a teacher reaches without looking, so
        # the rewrite has to have left the gesture on both of them.
        page = self._page()
        for button in (page.btn_prev, page.btn_next):
            drags = [
                c for c in button.observe_controllers()
                if isinstance(c, Gtk.GestureDrag)
            ]
            self.assertEqual(len(drags), 1)
            self.assertEqual(
                drags[0].get_propagation_phase(), Gtk.PropagationPhase.CAPTURE
            )


if __name__ == "__main__":
    unittest.main()
