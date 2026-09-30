"""A number pad large enough for a finger, for jumping to a page."""

from gi.repository import GObject, Gtk

from .. import icons

MAX_DIGITS = 4


class NumberPad(Gtk.Box):
    """
    Digits, a backspace and a "go" key, with the number typed so far above
    them. A board's on-screen keyboard floats over a fifth of the screen; this
    is what a teacher gets instead for the one field that is only ever digits.
    """

    __gtype_name__ = "InteraktivNumberPad"
    __gsignals__ = {
        # The number entered, when "go" is pressed.
        "submitted": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, go_label: str = "Git"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("numpad")
        self._text = ""
        self._hint = ""

        self.display = Gtk.Label()
        self.display.add_css_class("numpad-display")
        self.display.add_css_class("numeric")
        self.append(self.display)

        grid = Gtk.Grid(row_spacing=8, column_spacing=8, halign=Gtk.Align.CENTER)
        for index, digit in enumerate("123456789"):
            grid.attach(self._digit_key(digit), index % 3, index // 3, 1, 1)

        backspace = Gtk.Button(icon_name=icons.BACKSPACE, tooltip_text="Sil")
        backspace.add_css_class("numpad-key")
        backspace.connect("clicked", lambda _b: self.backspace())
        grid.attach(backspace, 0, 3, 1, 1)
        grid.attach(self._digit_key("0"), 1, 3, 1, 1)

        self.go = Gtk.Button(label=go_label)
        self.go.add_css_class("numpad-key")
        self.go.add_css_class("suggested-action")
        self.go.connect("clicked", lambda _b: self.submit())
        grid.attach(self.go, 2, 3, 1, 1)
        self.append(grid)

        self._refresh()

    def _digit_key(self, digit: str) -> Gtk.Button:
        key = Gtk.Button(label=digit)
        key.add_css_class("numpad-key")
        key.connect("clicked", lambda _b: self.type_digit(digit))
        return key

    # -- state ------------------------------------------------------------

    @property
    def value(self) -> int:
        return int(self._text) if self._text else 0

    def set_hint(self, hint: str) -> None:
        """What the display shows before a digit is typed."""
        self._hint = hint
        self._refresh()

    def clear(self) -> None:
        self._text = ""
        self._refresh()

    def type_digit(self, digit: str) -> None:
        if len(self._text) >= MAX_DIGITS or (not self._text and digit == "0"):
            return
        self._text += digit
        self._refresh()

    def backspace(self) -> None:
        self._text = self._text[:-1]
        self._refresh()

    def submit(self) -> None:
        if self._text:
            self.emit("submitted", self.value)

    def _refresh(self) -> None:
        self.display.set_label(self._text or self._hint)
        if self._text:
            self.display.remove_css_class("dim-label")
        else:
            self.display.add_css_class("dim-label")
        self.go.set_sensitive(bool(self._text))
