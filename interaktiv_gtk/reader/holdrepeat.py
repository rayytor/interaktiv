"""
Press to turn one page; hold to keep turning.

A port of `bindHoldRepeat` (`viewer.js:2496`) and of the reasoning behind it: on
a smartboard, twenty taps is the wrong gesture for twenty pages. The pointer
lands and the first page turns immediately -- a tap must still be exactly one
page -- and only after a pause does holding start to repeat, so a long press
does not overshoot before the teacher has decided how far to go.

Two divergences from the web version, both deliberate:

  * The trailing activation is swallowed once, not conditionally. In the browser
    the press turns a page and the `click` that follows turns another unless a
    hold intervened, which on a mouse means two pages per tap; here a pointer
    press is the whole gesture and the button's own `clicked` is consumed.
  * Keyboard activation still turns exactly one page. Enter and Space raise
    `clicked` with no pointer sequence in front of it, so nothing is swallowed.
"""

from gi.repository import GLib, Gtk

REPEAT_DELAY_MS = 350
REPEAT_INTERVAL_MS = 110


def bind_hold_repeat(button: Gtk.Button, action) -> None:
    state = {"delay": 0, "repeat": 0, "swallow": False}

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
        state["repeat"] = GLib.timeout_add(REPEAT_INTERVAL_MS, step)
        return GLib.SOURCE_REMOVE

    def on_pressed(_gesture, _n, _x, _y) -> None:
        stop()
        state["swallow"] = True
        step()
        state["delay"] = GLib.timeout_add(REPEAT_DELAY_MS, begin_repeat)

    def on_finished(*_args) -> None:
        stop()
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

    gesture = Gtk.GestureClick()
    gesture.set_button(1)
    # Capture, so the press is seen before the button's own gesture claims it.
    gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    gesture.connect("pressed", on_pressed)
    gesture.connect("released", on_finished)
    gesture.connect("cancel", on_finished)
    button.add_controller(gesture)
    button.connect("clicked", on_clicked)
    button.connect("unrealize", lambda *_: stop())
