"""
One book in the catalogue grid.

The card is a port of `dashboard.js:createBookCardHTML`, including which
controls appear when: a download in flight replaces the buttons entirely with
its own progress and a cancel, so the install and preview paths can never both
be running for one book and nothing has to arbitrate between them.

Cards are recycled by `Gtk.GridView`, so the widget tree is built once in
`__init__` and `bind()` only moves values into it.
"""

from typing import Optional

from gi.repository import GObject, Gtk

from .. import icons
from .model import BookItem

# `.book-cover` is `aspect-ratio: 1 / 1.414` in the web dashboard -- a portrait
# A4 page -- and the image covers it. GTK's AspectFrame cannot be given a child
# with its own minimum size without tripping an assertion, so the height is
# fixed instead: 320 px is A4 at the 238 px a card's cover gets inside a 280 px
# grid column. The picture still covers, so a cover is cropped at the sides
# rather than through its title.
COVER_HEIGHT = 320

# What `Gtk.GridView` divides the available width by to pick a column count,
# standing in for `.books-grid`'s `minmax(260px, 1fr)`.
CARD_WIDTH = 260


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
        self.add_css_class("book-card")
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
        self.placeholder_title.add_css_class("book-cover-placeholder-title")
        placeholder.append(self.placeholder_title)
        self.cover.set_child(placeholder)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER)
        self.picture.set_visible(False)
        self.cover.add_overlay(self.picture)

        self.cover.set_size_request(-1, COVER_HEIGHT)
        self.append(self.cover)

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
        self.title_label.add_css_class("book-title")
        self.title_label.set_cursor_from_name("pointer")
        title_click = Gtk.GestureClick()
        title_click.connect("released", self._on_cover_clicked)
        self.title_label.add_controller(title_click)
        text.append(self.title_label)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        meta.add_css_class("book-meta")
        self.meta_label = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=3)
        meta.append(self.meta_label)
        self.confidence_label = Gtk.Label()
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
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("book-actions")

        self.btn_open = self._action_button(icons.OPEN, "Aç", "open-book")
        self.btn_open.add_css_class("suggested-action")
        self.btn_open.set_hexpand(True)
        box.append(self.btn_open)

        self.btn_preview = self._action_button(icons.PREVIEW, "Önizle", "preview-book")
        self.btn_preview.set_hexpand(True)
        self.btn_preview.set_tooltip_text("Kitabı indirip açar")
        box.append(self.btn_preview)

        self.btn_install = self._icon_button(icons.INSTALL, "Kitabı İndir", "install-book")
        box.append(self.btn_install)

        self.btn_uninstall = self._icon_button(icons.UNINSTALL, "Kitabı Kaldır", "uninstall-book")
        self.btn_uninstall.add_css_class("destructive-action")
        box.append(self.btn_uninstall)
        return box

    def _build_progress(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("download-progress-box")
        self.progress_bar = Gtk.ProgressBar()
        box.append(self.progress_bar)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.progress_label = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=3)
        self.progress_label.add_css_class("download-progress-meta")
        row.append(self.progress_label)
        self.btn_cancel = self._icon_button(icons.CANCEL, "İptal", "cancel-download")
        self.btn_cancel.add_css_class("btn-cancel-download")
        row.append(self.btn_cancel)
        box.append(row)
        return box

    def _action_button(self, icon_name: str, label: str, signal: str) -> Gtk.Button:
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.CENTER)
        content.append(Gtk.Image.new_from_icon_name(icon_name))
        content.append(Gtk.Label(label=label))
        button = Gtk.Button(child=content)
        button.add_css_class("btn-card-action")
        button.connect("clicked", self._emit_for_item, signal)
        return button

    def _icon_button(self, icon_name: str, tooltip: str, signal: str) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip)
        button.add_css_class("btn-card-icon")
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
