"""
One book in the catalogue grid: a cover, a title, and one thing a tap does.

The whole card is the button. What it does follows from the book's state: an
installed book opens, a book that is not installed is downloaded, and in edit
mode an installed book is removed. A download in flight shows its progress on
the cover, with a cancel button of its own.

One card is built per book and lives as long as the catalogue draw that made
it, so the widget tree is built once in `__init__` and `bind()` only moves
values into it.
"""

from typing import Optional

from gi.repository import GObject, Gtk, Pango

from .. import icons
from ..touch import bind_touch_tooltip
from .model import BookItem

# Portrait A4: 1 : sqrt(2).
COVER_ASPECT_RATIO = 1.414

# What `Gtk.FlowBox` divides the available width by to pick a column count.
CARD_WIDTH = 196

COVER_HEIGHT = int(round(CARD_WIDTH * COVER_ASPECT_RATIO))


class AspectCover(Gtk.Widget):
    """
    Keeps its child at the cover's aspect ratio, height for width.

    `Gtk.AspectFrame` cannot be given a child with its own minimum size without
    tripping an assertion, so the size negotiation is done here.
    """

    __gtype_name__ = "InteraktivAspectCover"

    def __init__(self, ratio: float = COVER_ASPECT_RATIO, width: int = CARD_WIDTH):
        super().__init__()
        self.ratio = ratio
        self.natural_width = width
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
            return 0, self.natural_width, -1, -1
        width = for_size if for_size != -1 else self.natural_width
        height = int(round(width * self.ratio))
        return height, height, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        if self._child is not None:
            self._child.allocate(width, height, baseline, None)

    def do_dispose(self) -> None:
        if self._child is not None:
            self._child.unparent()
            self._child = None
        Gtk.Widget.do_dispose(self)


class BookCard(Gtk.Button):
    __gtype_name__ = "InteraktivBookCard"
    __gsignals__ = {
        # Each carries the BookItem the card is showing.
        "open-book": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "install-book": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "cancel-download": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "uninstall-book": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, covers, library_mode: bool = False, show_confidence: bool = False):
        super().__init__()
        self.add_css_class("flat")
        self.add_css_class("book-card")
        self.set_size_request(CARD_WIDTH, -1)
        self.covers = covers
        self.library_mode = library_mode
        # How sure the activity detector was of this book: a developer's
        # number, not something a teacher can act on.
        self.show_confidence = show_confidence
        self.editing = False
        self.item: Optional[BookItem] = None
        self._changed_handler = 0
        self._cover_token = 0

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.append(self._build_cover())
        body.append(self._build_text())
        self.set_child(body)
        self.connect("clicked", self._on_clicked)

    # -- construction -----------------------------------------------------

    def _build_cover(self) -> Gtk.Widget:
        self.cover_frame = AspectCover()
        self.cover_frame.add_css_class("book-cover")

        self.cover = Gtk.Overlay()

        placeholder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8,
            valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
        )
        placeholder.add_css_class("book-cover-placeholder")
        self.placeholder = placeholder
        image = Gtk.Image.new_from_icon_name(icons.BOOK)
        image.set_pixel_size(40)
        placeholder.append(image)
        self.placeholder_title = Gtk.Label(
            wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=16,
        )
        self.placeholder_title.add_css_class("caption")
        placeholder.append(self.placeholder_title)
        self.cover.set_child(placeholder)

        # Contain, not cover: a cover is never cropped.
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN)
        self.picture.add_css_class("book-cover-picture")
        self.picture.set_visible(False)
        self.cover.add_overlay(self.picture)

        self.cover.add_overlay(self._build_download_chip())
        self.cover.add_overlay(self._build_progress())
        self.cover.add_overlay(self._build_remove_badge())

        self.confidence_label = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.START)
        self.confidence_label.add_css_class("caption")
        self.confidence_label.add_css_class("book-confidence")
        self.confidence_label.set_visible(False)
        self.cover.add_overlay(self.confidence_label)

        self.cover_frame.set_child(self.cover)
        return self.cover_frame

    def _build_download_chip(self) -> Gtk.Widget:
        chip = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
        )
        chip.add_css_class("cover-chip")
        chip.append(Gtk.Image.new_from_icon_name(icons.INSTALL))
        chip.append(Gtk.Label(label="İndir"))
        self.download_chip = chip
        return chip

    def _build_progress(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.END)
        box.add_css_class("cover-progress")
        self.progress_bar = Gtk.ProgressBar()
        box.append(self.progress_bar)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.progress_label = Gtk.Label(
            xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
        )
        self.progress_label.add_css_class("caption")
        self.progress_label.add_css_class("numeric")
        row.append(self.progress_label)
        self.btn_cancel = Gtk.Button(icon_name=icons.CANCEL, tooltip_text="İndirmeyi iptal et")
        self.btn_cancel.add_css_class("cover-button")
        bind_touch_tooltip(self.btn_cancel)
        self.btn_cancel.connect("clicked", self._emit_for_item, "cancel-download")
        row.append(self.btn_cancel)
        box.append(row)
        self.progress_box = box
        return box

    def _build_remove_badge(self) -> Gtk.Widget:
        badge = Gtk.Image.new_from_icon_name(icons.UNINSTALL)
        badge.set_halign(Gtk.Align.END)
        badge.set_valign(Gtk.Align.START)
        badge.add_css_class("cover-remove")
        self.remove_badge = badge
        return badge

    def _build_text(self) -> Gtk.Widget:
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.add_css_class("book-info")

        self.title_label = Gtk.Label(
            xalign=0.0, yalign=0.0, wrap=True, lines=2, max_width_chars=20,
            ellipsize=Pango.EllipsizeMode.END,
        )
        self.title_label.add_css_class("book-title")
        text.append(self.title_label)

        self.meta_label = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        self.meta_label.add_css_class("caption")
        self.meta_label.add_css_class("dim-label")
        text.append(self.meta_label)
        return text

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

    def set_editing(self, editing: bool) -> None:
        """In edit mode a tap on an installed book removes it."""
        if editing == self.editing:
            return
        self.editing = editing
        self.refresh()

    def _load_cover(self, item: BookItem) -> None:
        self._cover_token += 1
        token = self._cover_token

        cached = self.covers.cached(item.id)
        if cached is not None:
            self._show_cover(cached)
            return

        def done(texture):
            if token != self._cover_token:
                return  # The card was bound to another book meanwhile.
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
        self.placeholder_title.set_label(item.title)
        self.meta_label.set_label(item.grade_label)

        label = item.confidence_label if self.show_confidence else None
        self.confidence_label.set_visible(bool(label))
        if label:
            self.confidence_label.set_label(label)

        available = item.is_installed or self.library_mode
        downloading = item.is_downloading

        self.progress_box.set_visible(downloading)
        if downloading:
            self.progress_bar.set_fraction(item.progress)
            self.progress_label.set_label(self._progress_text(item))
        self.download_chip.set_visible(not available and not downloading)
        removable = self.editing and item.is_installed and not self.library_mode
        self.remove_badge.set_visible(removable)

        for name, on in (("not-installed", not available), ("removable", removable)):
            if on:
                self.add_css_class(name)
            else:
                self.remove_css_class(name)

    @staticmethod
    def _progress_text(item: BookItem) -> str:
        info = item.download or {}
        percent = round(info.get("progress") or 0)
        total = info.get("total_bytes") or 0
        if total:
            done_mb = (info.get("downloaded_bytes") or 0) / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            return f"%{percent} · {done_mb:.0f} / {total_mb:.0f} MB"
        return f"%{percent}"

    # -- events -----------------------------------------------------------

    def _emit_for_item(self, _button, signal: str) -> None:
        if self.item is not None:
            self.emit(signal, self.item)

    def _on_clicked(self, _button) -> None:
        item = self.item
        if item is None or item.is_downloading:
            return
        if self.editing and item.is_installed and not self.library_mode:
            self.emit("uninstall-book", item)
        elif item.is_installed or self.library_mode:
            self.emit("open-book", item)
        else:
            self.emit("install-book", item)
