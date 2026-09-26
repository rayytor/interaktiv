"""
What a finger means.

A smartboard is the only screen most of these classrooms have, and a finger is
not a small mouse. Three differences drive everything in this module:

  * **There is no hover.** GTK refuses to show a tooltip at all when the event
    came from a touchscreen (`gtktooltip.c` bails out on
    `GDK_SOURCE_TOUCHSCREEN`), so an icon-only button is unlabelled forever on
    a board. `bind_touch_tooltip` gives those buttons their label back on a
    long press.
  * **A press is not yet a decision.** A finger that lands is on its way
    somewhere -- a tap, a drag to pan, a swipe to turn the page -- and which
    one it was is only knowable at release. Acting on the press is right for a
    mouse, where a button-down is already a commitment, and wrong here.
  * **Something else is usually listening.** `GtkScrolledWindow` claims a touch
    drag as soon as it crosses `gtk-dnd-drag-threshold` and every gesture below
    it is cancelled. Anything that wants to interpret a drag differently has to
    reach a verdict *before* that, from an ancestor's capture phase, which is
    what `SwipeNavigator` does. `app._tune_for_touch` widens that threshold
    from its mouse-sized default, which is also what gives the verdict below
    enough travel to read a direction from.

The slop values are finger-sized rather than pixel-sized. `TAP_SLOP` is roughly
the wobble of a fingertip held still against glass; `SWIPE_TRAVEL` is far
enough that no tap and no scroll can reach it by accident.
"""

from typing import Callable, Optional

from gi.repository import Gdk, GLib, Gtk

# How far a finger may wander and still have been a tap.
TAP_SLOP = 24.0

# How far a swipe must travel sideways before it turns a page. A teacher
# flicking across a board covers this in the first few tens of milliseconds;
# a finger sliding down to scroll never does.
SWIPE_TRAVEL = 72.0

# How much more sideways than up-and-down a drag must be to read as a swipe.
# Anything less decisive belongs to whatever scrolls.
SWIPE_BIAS = 1.2

# The fraction of GTK's drag threshold at which a swipe commits to its verdict.
# It has to be under 1.0: `GtkScrolledWindow` claims the sequence on the first
# motion *past* the threshold, and a verdict reached on that same event but in
# an ancestor's capture phase is the only one that beats it there.
DECISION_FRACTION = 0.75

# How long the label stays up after a long press, and how long the press is.
TOOLTIP_DWELL_MS = 2600


def is_touch(gesture: Gtk.Gesture) -> bool:
    """
    Whether the sequence this gesture is handling came from a finger.

    Asked at press time and then remembered: inside a timeout there is no
    current event to ask about, and a gesture that has already ended reports
    no device.
    """
    device = gesture.get_device()
    if device is not None:
        return device.get_source() == Gdk.InputSource.TOUCHSCREEN
    # No device means no event in flight; fall back to the shape of the
    # sequence, which is only non-NULL for touch.
    if isinstance(gesture, Gtk.GestureSingle):
        return gesture.get_current_sequence() is not None
    return gesture.get_last_updated_sequence() is not None


def drag_threshold(widget: Gtk.Widget) -> float:
    """GTK's own idea of when a press has become a drag, in pixels."""
    display = widget.get_display()
    settings = Gtk.Settings.get_for_display(display) if display is not None else None
    if settings is None:
        return 8.0
    return float(settings.get_property("gtk-dnd-drag-threshold"))


# ----------------------------------------------------------------- swiping


class SwipeNavigator:
    """
    A sideways flick across the reading area, turned into a page.

    Installed on an **ancestor** of the scroller, in the capture phase, for the
    reason given at the top of this file: `GtkScrolledWindow` claims a touch
    drag the moment it passes GTK's drag threshold, and an ancestor's capture
    handler is the only place that sees that motion first. The verdict is
    therefore reached early, off a short sample -- direction only -- and the
    question of whether the flick was *far* enough is left until the finger
    lifts.

    The verdict is deliberately three-valued. Claiming a drag that should have
    scrolled is much worse than missing a swipe, so anything that is not
    clearly sideways, and anything at all while the page is wide enough to pan,
    is handed straight back with `DENIED` -- which lets the scroller have it
    without waiting for us.
    """

    def __init__(
        self,
        host: Gtk.Widget,
        on_swipe: Callable[[int], None],
        *,
        can_pan: Optional[Callable[[], bool]] = None,
        enabled: Optional[Callable[[], bool]] = None,
    ):
        self._host = host
        self._on_swipe = on_swipe
        self._can_pan = can_pan or (lambda: False)
        self._enabled = enabled or (lambda: True)
        self._verdict: Optional[bool] = None

        gesture = Gtk.GestureDrag()
        # A mouse drag over the page is not a page turn -- it is a selection
        # or nothing at all -- and the people with a mouse have the arrow keys.
        gesture.set_touch_only(True)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect("drag-begin", self._on_begin)
        gesture.connect("drag-update", self._on_update)
        gesture.connect("drag-end", self._on_end)
        host.add_controller(gesture)
        self._gesture = gesture

    def _on_begin(self, _gesture, _x: float, _y: float) -> None:
        self._verdict = None

    def _on_update(self, gesture, dx: float, dy: float) -> None:
        if self._verdict is not None:
            return
        decided = decide_swipe(
            dx, dy, drag_threshold(self._host) * DECISION_FRACTION
        )
        if decided is None:
            return
        mine = decided and self._enabled() and not self._can_pan()
        self._verdict = mine
        gesture.set_state(
            Gtk.EventSequenceState.CLAIMED if mine
            else Gtk.EventSequenceState.DENIED
        )

    def _on_end(self, _gesture, dx: float, dy: float) -> None:
        verdict, self._verdict = self._verdict, None
        if not verdict:
            return
        # The page follows the finger: dragging the sheet to the right pulls
        # the previous page into view, which is the direction a paper book
        # moves under the same hand.
        step = swipe_direction(dx, dy)
        if step:
            self._on_swipe(step)


def decide_swipe(dx: float, dy: float, sample: float) -> Optional[bool]:
    """
    Whether a drag that has got this far is sideways, or `None` for too early.

    Split out from the gesture so the one rule that has to be right can be
    tested without a display. The verdict is deliberately reached off a short
    sample -- see `SwipeNavigator` for why it cannot wait.
    """
    if max(abs(dx), abs(dy)) < sample:
        return None
    return abs(dx) > abs(dy) * SWIPE_BIAS


def swipe_direction(dx: float, dy: float, *, travel: float = SWIPE_TRAVEL) -> int:
    """
    The page step a finished drag of (dx, dy) means: -1, +1, or 0 for neither.
    """
    if abs(dx) < travel or abs(dx) <= abs(dy) * SWIPE_BIAS:
        return 0
    return -1 if dx > 0 else 1


class EdgePan:
    """
    Panning from over a control that is covering the thing being panned.

    The two page-turn strips lie on top of the reading area rather than beside
    it, which is what makes "next page" a tap anywhere down the side of the
    board. The cost is that they are the pick target there: a drag beginning
    inside one never reaches the scroller underneath, because that scroller is
    not an ancestor of the strip but its sibling. Before this, that drag turned
    a page; with the press deferred it did nothing at all, which on a zoomed-in
    page is 96 px down each edge where a teacher cannot pan.

    So the drag is carried across by hand. It is only armed when the press
    landed on one of those overlay children -- a drag starting on the sheet
    itself is the scroller's, and is left alone -- and it moves the same
    adjustments `GtkScrolledWindow` would. There is no kinetic throw at the
    end: a page is dragged to a place and left there, not flicked like a list.
    """

    def __init__(
        self,
        host: Gtk.Widget,
        scroller: Gtk.ScrolledWindow,
        *,
        covers: Callable[[Optional[Gtk.Widget]], bool],
    ):
        self._host = host
        self._scroller = scroller
        self._covers = covers
        self._origin: Optional[tuple] = None

        gesture = Gtk.GestureDrag()
        gesture.set_touch_only(True)
        gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        gesture.connect("drag-begin", self._on_begin)
        gesture.connect("drag-update", self._on_update)
        gesture.connect("drag-end", self._on_end)
        host.add_controller(gesture)

    def _on_begin(self, _gesture, x: float, y: float) -> None:
        self._origin = None
        # `Gtk.Gesture.get_last_target` is not in every PyGObject build, and
        # picking the host at the press point answers the same question: which
        # widget was under the finger when it landed.
        if not self._covers(self._host.pick(x, y, Gtk.PickFlags.DEFAULT)):
            return
        hadj = self._scroller.get_hadjustment()
        vadj = self._scroller.get_vadjustment()
        if hadj is None or vadj is None:
            return
        self._origin = (hadj.get_value(), vadj.get_value())

    def _on_update(self, gesture, dx: float, dy: float) -> None:
        if self._origin is None:
            return
        if max(abs(dx), abs(dy)) < drag_threshold(self._scroller):
            return
        # Taken only once it is a drag, and only if it is going somewhere the
        # page can actually follow -- otherwise the sideways swipe, which has
        # already had its say on this event, keeps it.
        moved = False
        axes = (
            (self._scroller.get_hadjustment(), self._origin[0], dx),
            (self._scroller.get_vadjustment(), self._origin[1], dy),
        )
        for adjustment, start, delta in axes:
            room = adjustment.get_upper() - adjustment.get_page_size()
            if room <= 1.0:
                continue
            adjustment.set_value(max(0.0, min(room, start - delta)))
            moved = True
        if moved:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_end(self, _gesture, _dx: float, _dy: float) -> None:
        self._origin = None


# ---------------------------------------------------------------- tooltips


def bind_touch_tooltip(widget: Gtk.Widget) -> None:
    """
    Give an icon-only control its tooltip back on a board.

    GTK will not show one for a touchscreen at all, so a teacher facing a row
    of symbols has nothing to read. A long press pops the same text as a
    label, and claims the sequence so that the press which asked what the
    button *is* does not also press it.

    The popover does not autohide: an autohiding popover takes a grab, and the
    next thing the teacher touches would be spent dismissing this instead of
    doing what they meant. It goes away on its own.
    """
    gesture = Gtk.GestureLongPress()
    gesture.set_touch_only(True)
    gesture.connect("pressed", _show_touch_tooltip)
    widget.add_controller(gesture)


def _show_touch_tooltip(gesture, x: float, y: float) -> None:
    widget = gesture.get_widget()
    if widget is None:
        return
    text = widget.get_tooltip_text()
    if not text:
        return
    gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    popover = Gtk.Popover(autohide=False, has_arrow=True)
    popover.add_css_class("touch-tooltip")
    popover.set_position(Gtk.PositionType.BOTTOM)
    popover.set_child(Gtk.Label(label=text, wrap=True, max_width_chars=28))
    popover.set_parent(widget)

    state = {"source": 0}

    def dismiss() -> bool:
        state["source"] = 0
        if popover.get_parent() is not None:
            popover.popdown()
            popover.unparent()
        return GLib.SOURCE_REMOVE

    def abandon(*_args) -> None:
        # A control can be rebuilt, or the page popped, while the label is up.
        if state["source"]:
            GLib.source_remove(state["source"])
        dismiss()

    state["source"] = GLib.timeout_add(TOOLTIP_DWELL_MS, dismiss)
    widget.connect("unrealize", abandon)
    popover.popup()
