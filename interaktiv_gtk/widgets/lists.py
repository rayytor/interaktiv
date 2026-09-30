"""Helpers for `Gtk.ListView` and `Gtk.GridView`."""

from typing import Callable

from gi.repository import GLib, Gtk


def scroll_to_item(view: Gtk.Widget, position: int) -> None:
    """Bring the item at `position` into view."""
    # `scroll_to()` arrived in GTK 4.12; the action has been there since 4.0.
    view.activate_action("list.scroll-to-item", GLib.Variant.new_uint32(position))


def after_layout(widget: Gtk.Widget, callback: Callable[[], None]) -> None:
    """
    Run `callback` once `widget` has been laid out again.

    A list that has just been shown, or whose rows have just changed size, has
    no positions to scroll to until its next layout.
    """
    frames = [0]

    def tick(_widget, _clock) -> bool:
        # The first tick is ahead of this frame's layout; the second follows it.
        frames[0] += 1
        if frames[0] < 2:
            return GLib.SOURCE_CONTINUE
        callback()
        return GLib.SOURCE_REMOVE

    widget.add_tick_callback(tick)
