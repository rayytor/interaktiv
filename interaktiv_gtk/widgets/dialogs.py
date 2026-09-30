"""Questions the app asks before doing something that costs time or data."""

from typing import Callable, Optional, Sequence, Tuple

from gi.repository import Adw, Gtk

# (response id, button label, Adw.ResponseAppearance or None)
Response = Tuple[str, str, Optional[Adw.ResponseAppearance]]


def ask(parent: Optional[Gtk.Widget], heading: str, body: str,
        responses: Sequence[Response], on_response: Callable[[str], None],
        default: Optional[str] = None, close: str = "cancel") -> Adw.MessageDialog:
    """Show a modal question over `parent`'s window and report the answer."""
    window = parent.get_root() if parent is not None else None
    dialog = Adw.MessageDialog(
        transient_for=window if isinstance(window, Gtk.Window) else None,
        modal=True,
        heading=heading,
        body=body,
    )
    for response_id, label, appearance in responses:
        dialog.add_response(response_id, label)
        if appearance is not None:
            dialog.set_response_appearance(response_id, appearance)
    if default:
        dialog.set_default_response(default)
    dialog.set_close_response(close)
    dialog.connect("response", lambda _dialog, response: on_response(response))
    dialog.present()
    return dialog
