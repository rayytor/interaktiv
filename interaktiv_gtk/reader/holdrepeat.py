"""
Press to turn one page; hold to keep turning.

A port of `bindHoldRepeat` (`viewer.js:2496`) and of the reasoning behind it: on
a smartboard, twenty taps is the wrong gesture for twenty pages. Only after a
pause does holding start to repeat, so a long press does not overshoot before
the teacher has decided how far to go.

Mouse and finger commit at different moments, and this is the one place in the
reader where that difference is visible:

  * **A mouse press is already a decision.** The button goes down on the page
    turn and nowhere else, so the first page turns on the press -- immediately,
    as the web version does.
  * **A finger press is not.** These buttons are the two invisible strips down
    the sides of the reading area, lying over the sheet itself, so a finger
    landing there may be starting a pan or a swipe rather than asking for a
    page. Turning on the press meant that a teacher who touched near the edge
    to drag a zoomed-in page got a page turn instead, and that a swipe begun
    at the edge turned a page before it was recognised as a swipe. So on touch
    the page turns on *release*, and only if the finger stayed put: a tap is
    still exactly one page, and a drag is no pages at all.

Two further divergences from the web version, both deliberate:

  * The trailing activation is swallowed once, not conditionally. In the browser
    the press turns a page and the `click` that follows turns another unless a
    hold intervened, which on a mouse means two pages per tap; here a pointer
    press is the whole gesture and the button's own `clicked` is consumed.
  * Keyboard activation still turns exactly one page. Enter and Space raise
    `clicked` with no pointer sequence in front of it, so nothing is swallowed.
"""

from gi.repository import GLib, Gtk

from ..touch import TAP_SLOP, is_touch

REPEAT_DELAY_MS = 350
REPEAT_INTERVAL_MS = 110


def bind_hold_repeat(button: Gtk.Button, action) -> None:
    state = {
        "delay": 0,
        "repeat": 0,
        "swallow": False,
        "touch": False,
        "moved": False,
        "held": False,
    }

    def stop() -> None:
        for name in ("delay", "repeat"):
            if state[name]:
                GLib.source_remove(state[name])
                state[name] = 0

    def step() -> bool:
        if not button.get_sensitive():
            # Reaching the last page ends the hold rather than spinning on a
            # dead button until the finger lifts.
            stop()
            return GLib.SOURCE_REMOVE
        action()
        return GLib.SOURCE_CONTINUE

    def begin_repeat() -> bool:
        state["delay"] = 0
        if state["moved"]:
            return GLib.SOURCE_REMOVE
        state["held"] = True
        if state["touch"]:
            # The press itself turned nothing; the hold's first page is this
            # one, and it is what tells the teacher the hold has taken.
            step()
        state["repeat"] = GLib.timeout_add(REPEAT_INTERVAL_MS, step)
        return GLib.SOURCE_REMOVE

    def on_begin(gesture, _x, _y) -> None:
        stop()
        state["touch"] = is_touch(gesture)
        state["moved"] = False
        state["held"] = False
        state["swallow"] = True
        if not state["touch"]:
            step()
        state["delay"] = GLib.timeout_add(REPEAT_DELAY_MS, begin_repeat)

    def on_update(_gesture, dx, dy) -> None:
        if state["moved"] or max(abs(dx), abs(dy)) <= TAP_SLOP:
            return
        # The finger is going somewhere: this was a pan or a swipe, and the
        # strip was only the place it started from.
        state["moved"] = True
        stop()

    def on_end(_gesture, _dx, _dy) -> None:
        stop()
        if state["touch"] and not state["moved"] and not state["held"]:
            step()
        _finish()

    def on_cancel(*_args) -> None:
        # Another gesture -- the swipe over the reading area -- took the
        # sequence. Whatever it does with it, it is not a page turn from here.
        state["moved"] = True
        stop()
        _finish()

    def _finish() -> None:
        # The button emits `clicked` synchronously while this event is still
        # being dispatched, so the flag has to outlive the release and be
        # cleared afterwards -- otherwise a press that ends outside the button,
        # which raises no `clicked` at all, would swallow the next keypress.
        GLib.idle_add(_clear, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _clear() -> bool:
        state["swallow"] = False
        return GLib.SOURCE_REMOVE

    def on_clicked(_button) -> None:
        if state["swallow"]:
            state["swallow"] = False
            return
        action()

    # A drag rather than a click: the press, the travel and the release are
    # all needed, and `GtkGestureClick` reports only two of the three.
    gesture = Gtk.GestureDrag()
    gesture.set_button(1)
    # Capture, so the press is seen before the button's own gesture claims it.
    gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    gesture.connect("drag-begin", on_begin)
    gesture.connect("drag-update", on_update)
    gesture.connect("drag-end", on_end)
    gesture.connect("cancel", on_cancel)
    button.add_controller(gesture)
    button.connect("clicked", on_clicked)
    button.connect("unrealize", lambda *_: stop())
