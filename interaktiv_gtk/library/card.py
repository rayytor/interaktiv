"""
One book in the catalogue grid.

The card is a port of `dashboard.js:createBookCardHTML`, including which
controls appear when: a download in flight replaces the buttons entirely with
its own progress and a cancel, so the install and preview paths can never both
be running for one book and nothing has to arbitrate between them.

One card is built per book and lives as long as the catalogue draw that made
it, so the widget tree is built once in `__init__` and `bind()` only moves
values into it.
"""

from typing import Optional

from gi.repository import GObject, Gtk

from .. import icons
from ..touch import bind_touch_tooltip
from .model import BookItem

# Standard portrait A4 page aspect ratio (1 : sqrt(2) ≈ 1 : 1.414).
COVER_ASPECT_RATIO = 1.414

# What `Gtk.FlowBox` divides the available width by to pick a column count,
# standing in for `.books-grid`'s `minmax(232px, 1fr)`.
CARD_WIDTH = 232

# At the minimum card width, an A4 page is 328 px tall.
COVER_HEIGHT = int(round(CARD_WIDTH * COVER_ASPECT_RATIO))


class AspectCover(Gtk.Widget):
    """
    Maintains the cover aspect ratio (A4 portrait) height-for-width.

    GTK's AspectFrame cannot be given a child with its own minimum size without
    tripping an assertion failure, so this widget directly implements
    height-for-width size negotiation without relying on GtkAspectFrame.
    """

    __gtype_name__ = "InteraktivAspectCover"

    def __init__(self, ratio: float = COVER_ASPECT_RATIO):
        super().__init__()
        self.ratio = ratio
        self._child: Optional[Gtk.Widget] = None
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def set_child(self, child: Optional[Gtk.Widget]) -> None:
        if self._child is not None:
            self._child.unparent()
        self._child = child
        if child is not None:
            child.set_parent(self)

    def get_child(self) -> Optional[Gtk.Widget]:
        return self._child

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, CARD_WIDTH, -1, -1
        else:
            if for_size != -1:
                h = int(round(for_size * self.ratio))
                return h, h, -1, -1
            return COVER_HEIGHT, COVER_HEIGHT, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        if self._child is not None:
            self._child.allocate(width, height, baseline, None)

    def do_dispose(self) -> None:
        if self._child is not None:
            self._child.unparent()
            self._child = None
        Gtk.Widget.do_dispose(self)


class BookCard(Gtk.Box):
    """A cover, a title, and whichever actions the book's state allows."""

    __gtype_name__ = "InteraktivBookCard"
    __gsignals__ = {
        # Each carries the BookItem the button belongs to.
        "open-book": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "preview-book": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "install-book": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "cancel-download": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "uninstall-book": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, covers, library_mode: bool = False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # `.card` is libadwaita's own surface: it carries the elevation, the
        # corner radius and the border that the light, dark and high-contrast
        # styles each want, so none of the three is hand-drawn here. The card
        # clips, which is what lets the cover run to its top corners.
        self.add_css_class("card")
        self.add_css_class("book-card")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_size_request(CARD_WIDTH, -1)
        self.covers = covers
        self.library_mode = library_mode
        self.item: Optional[BookItem] = None
        self._changed_handler = 0
        self._cover_token = 0

        self._build_cover()
        self._build_info()

    # -- construction -----------------------------------------------------

    def _build_cover(self) -> None:
        self.cover_frame = AspectCover(COVER_ASPECT_RATIO)

        self.cover = Gtk.Overlay()
        self.cover.add_css_class("book-cover")
        self.cover.set_overflow(Gtk.Overflow.HIDDEN)

        placeholder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8,
            valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
        )
        placeholder.add_css_class("book-cover-placeholder")
        self.placeholder = placeholder
        image = Gtk.Image.new_from_icon_name(icons.BOOK)
        image.set_pixel_size(40)
        placeholder.append(image)
        self.placeholder_title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=18)
        self.placeholder_title.add_css_class("caption")
        self.placeholder_title.add_css_class("book-cover-placeholder-title")
        placeholder.append(self.placeholder_title)
        self.cover.set_child(placeholder)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN)
        self.picture.set_visible(False)
        self.cover.add_overlay(self.picture)

        self.cover_frame.set_child(self.cover)
        self.append(self.cover_frame)

        click = Gtk.GestureClick()
        click.connect("released", self._on_cover_clicked)
        self.cover.add_controller(click)
        self.cover.set_cursor_from_name("pointer")

    def _build_info(self) -> None:
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        info.add_css_class("book-info")

        # Every cell in a grid row is as tall as the tallest card in it, and a
        # two-line title makes one card taller than its neighbours. The text
        # takes the slack so the buttons stay on one line across the row --
        # a teacher aiming at "Aç" should not have to aim at a different height
        # for every book.
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       vexpand=True, valign=Gtk.Align.START)

        self.title_label = Gtk.Label(
            xalign=0.0, wrap=True, lines=2, max_width_chars=24,
            ellipsize=3,  # Pango.EllipsizeMode.END
        )
        self.title_label.add_css_class("heading")
        self.title_label.add_css_class("book-title")
        self.title_label.set_cursor_from_name("pointer")
        title_click = Gtk.GestureClick()
        title_click.connect("released", self._on_cover_clicked)
        self.title_label.add_controller(title_click)
        text.append(self.title_label)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        meta.add_css_class("book-meta")
        self.meta_label = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=3)
        self.meta_label.add_css_class("caption")
        self.meta_label.add_css_class("dim-label")
        meta.append(self.meta_label)
        self.confidence_label = Gtk.Label()
        self.confidence_label.add_css_class("caption")
        self.confidence_label.add_css_class("book-confidence")
        self.confidence_label.set_visible(False)
        meta.append(self.confidence_label)
        text.append(meta)
        info.append(text)

        # One of two faces: the buttons, or a download in progress.
        self.actions_stack = Gtk.Stack(vhomogeneous=False, valign=Gtk.Align.END)
        self.actions_stack.add_named(self._build_actions(), "actions")
        self.actions_stack.add_named(self._build_progress(), "progress")
        info.append(self.actions_stack)

        self.append(info)

    def _build_actions(self) -> Gtk.Widget:
        """
        One labelled button and one icon button per card.

        The suggested and destructive styles are deliberately absent. The HIG
        allows a view a single button in either style, and a catalogue of
        fifty-six books would otherwise show fifty-six accent-filled "Aç"
        buttons and fifty-six red bins -- a wall of colour in which nothing is
        emphasised because everything is. The card's own emphasis is the cover;
        the warning about removing a book belongs to the dialog that asks.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("book-actions")

        self.btn_open = self._action_button("Aç", "open-book")
        self.btn_open.set_tooltip_text("Kitabı aç")
        self.btn_open.set_hexpand(True)
        box.append(self.btn_open)

        self.btn_preview = self._action_button("Önizle", "preview-book")
        self.btn_preview.set_hexpand(True)
        self.btn_preview.set_tooltip_text("Kitabı indirip açar")
        box.append(self.btn_preview)

        self.btn_install = self._icon_button(icons.INSTALL, "Kitabı İndir", "install-book")
        box.append(self.btn_install)

        self.btn_uninstall = self._icon_button(icons.UNINSTALL, "Kitabı Kaldır", "uninstall-book")
        box.append(self.btn_uninstall)
        return box

    def _build_progress(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("download-progress-box")
        self.progress_bar = Gtk.ProgressBar()
        box.append(self.progress_bar)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.progress_label = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=3)
        self.progress_label.add_css_class("caption")
        self.progress_label.add_css_class("dim-label")
        row.append(self.progress_label)
        self.btn_cancel = self._icon_button(icons.CANCEL, "İptal", "cancel-download")
        self.btn_cancel.add_css_class("btn-cancel-download")
        row.append(self.btn_cancel)
        box.append(row)
        return box

    def _action_button(self, label: str, signal: str) -> Gtk.Button:
        """A label, no icon: outside a header bar the HIG asks for one or the
        other, and the word is what is read from the back of a classroom."""
        button = Gtk.Button(label=label)
        button.add_css_class("btn-card-action")
        button.connect("clicked", self._emit_for_item, signal)
        return button

    def _icon_button(self, icon_name: str, tooltip: str, signal: str) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip)
        button.add_css_class("flat")
        button.add_css_class("btn-card-icon")
        # A finger never sees a tooltip; a long press is how it asks.
        bind_touch_tooltip(button)
        button.connect("clicked", self._emit_for_item, signal)
        return button

    # -- binding ----------------------------------------------------------

    def bind(self, item: BookItem) -> None:
        self.unbind()
        self.item = item
        self._changed_handler = item.connect("changed", lambda _i: self.refresh())
        self.refresh()
        self._load_cover(item)

    def unbind(self) -> None:
        if self.item is not None and self._changed_handler:
            self.item.disconnect(self._changed_handler)
        self._changed_handler = 0
        self.item = None
        self._cover_token += 1
        self.picture.set_paintable(None)
        self.picture.set_visible(False)
        self.placeholder.set_visible(True)

    def _load_cover(self, item: BookItem) -> None:
        self._cover_token += 1
        token = self._cover_token

        cached = self.covers.cached(item.id)
        if cached is not None:
            self._show_cover(cached)
            return

        def done(texture):
            if token != self._cover_token:
                return  # The card was recycled onto another book meanwhile.
            self._show_cover(texture)

        self.covers.load(item.id, done)

    def _show_cover(self, texture) -> None:
        if texture is None:
            self.picture.set_visible(False)
            self.placeholder.set_visible(True)
            return
        self.picture.set_paintable(texture)
        self.picture.set_visible(True)
        self.placeholder.set_visible(False)

    def refresh(self) -> None:
        item = self.item
        if item is None:
            return

        self.title_label.set_label(item.title)
        self.title_label.set_tooltip_text(item.title)
        self.placeholder_title.set_label(item.title)
        self.meta_label.set_label(item.meta_text)

        label = item.confidence_label
        if label:
            self.confidence_label.set_label(label)
            self.confidence_label.set_tooltip_text(f"Etkinlik Tespiti: {label}")
            for name in ("conf-strong", "conf-weak", "conf-none"):
                self.confidence_label.remove_css_class(name)
            self.confidence_label.add_css_class(f"conf-{item.confidence}")
            self.confidence_label.set_visible(True)
        else:
            self.confidence_label.set_visible(False)

        if item.is_downloading:
            self.actions_stack.set_visible_child_name("progress")
            self.progress_bar.set_fraction(item.progress)
            self.progress_label.set_label(self._progress_text(item))
            return

        self.actions_stack.set_visible_child_name("actions")
        installed = item.is_installed
        # A packaged book is opened, never installed or removed.
        self.btn_open.set_visible(installed or self.library_mode)
        self.btn_preview.set_visible(not installed and not self.library_mode)
        self.btn_install.set_visible(not installed and not self.library_mode)
        self.btn_uninstall.set_visible(installed and not self.library_mode)

    @staticmethod
    def _progress_text(item: BookItem) -> str:
        info = item.download or {}
        percent = round(info.get("progress") or 0)
        verb = "Önizleme indiriliyor" if item.download_kind == "preview" else "İndiriliyor"
        total = info.get("total_bytes") or 0
        if total:
            done_mb = (info.get("downloaded_bytes") or 0) / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            return f"{verb}: %{percent} ({done_mb:.0f} / {total_mb:.0f} MB)"
        return f"{verb}: %{percent}"

    # -- events -----------------------------------------------------------

    def _emit_for_item(self, _button, signal: str) -> None:
        if self.item is not None:
            self.emit(signal, self.item)

    def _on_cover_clicked(self, gesture, n_press: int, _x: float, _y: float) -> None:
        if n_press != 1 or self.item is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if self.item.is_downloading:
            return
        # The cover opens an installed book and previews one that is not, which
        # is what a click on the cover does in the web dashboard.
        if self.item.is_installed or self.library_mode:
            self.emit("open-book", self.item)
        else:
            self.emit("preview-book", self.item)
