"""
The library: everything a teacher does before a book is open.

From the top: the app's name, the "continue the lesson" shelf, then the
catalogue in groups. What is touched often -- search, the grade filter, edit
mode -- sits in one bar at the bottom of the screen, where a hand reaches it on
a board; the on-screen keyboard floats at the top right and does not cover it.

The catalogue is a `Gtk.FlowBox` per group and not a `Gtk.GridView`: a grid
view measures itself as a single column, which is right inside a scrolled
window of its own and wrong inside a box, where that number becomes the
scrollable height. A flow box is height-for-width, so the page is exactly as
tall as the books on it. Nothing needs virtualizing: the catalogue is 56 books.
"""

import os
from typing import Dict, List, Optional

from gi.repository import Adw, GLib, GObject, Gtk

from .. import __version__, icons
from ..theme import THEMES
from ..touch import bind_touch_tooltip
from ..widgets import SegmentedControl, ThemePicker, ask
from .card import BookCard
from .covers import CoverLoader
from .downloads import DownloadWatcher
from .model import FILTER_LABELS, FILTERS, BookItem, LibraryModel, Section
from .shelf import ContinueShelf, resolve_entries

# Higher than any board will ever fit; the column width is the card's.
MAX_COLUMNS = 12
CARD_GAP = 14

WEBSITE = "https://github.com/rayytor/interaktiv"
AUTHOR_URL = "https://github.com/rayytor"
MADE_BY = "Made by Rayyan Tor"


def _link_label(root: Gtk.Widget, text: str, url: str) -> bool:
    """
    Turn the label under `root` that shows `text` into a link to `url`.

    The about window's first page shows the developer's name as plain text and
    has no property for a link there; where the label cannot be found, the
    name stays as it is and the link on the details page is the only one.
    """
    if isinstance(root, Gtk.Label) and root.get_text() == text:
        root.set_markup(f'<a href="{url}">{GLib.markup_escape_text(text)}</a>')
        return True
    child = root.get_first_child()
    while child is not None:
        if _link_label(child, text, url):
            return True
        child = child.get_next_sibling()
    return False


class LibraryPage(Gtk.Box):
    __gtype_name__ = "InteraktivLibraryPage"
    __gsignals__ = {
        # (BookItem, path) -- the book is on disk at `path` and should open.
        "open-book": (GObject.SignalFlags.RUN_FIRST, None, (object, str)),
    }

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("library")
        self.app = app
        self.manager = app.manager
        self.settings = app.settings
        self.library_mode = bool(getattr(self.manager, "library_mode", False))

        self.model = LibraryModel(self.manager)
        self.covers = CoverLoader(self.manager)
        self.watcher = DownloadWatcher(
            self.manager, self._on_download_tick, self._on_downloads_settled
        )

        self.active_filter = self._restore_filter()
        self.query = ""
        self.editing = False
        self._cards: List[BookCard] = []
        # Books to open as soon as their download finishes.
        self._open_when_done: set = set()

        self._build()
        self.reload()

    def _restore_filter(self) -> str:
        stored = self.settings.get("library_filter", "all")
        return stored if stored in FILTERS else "all"

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        self.toasts = Adw.ToastOverlay(vexpand=True)

        content = Gtk.Overlay()
        content.set_child(self._build_content())
        content.add_overlay(self._build_dock())

        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        view.append(self._build_header())
        content.set_vexpand(True)
        view.append(content)

        self.toasts.set_child(view)
        self.append(self.toasts)

    def _build_header(self) -> Gtk.Widget:
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.add_css_class("brand-bar")
        header.set_title_widget(Gtk.Box())

        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        brand.add_css_class("brand")
        # From the file, not by name: an older icon of the same name in the
        # user's icon theme would otherwise win.
        mark = Gtk.Image.new_from_file(icons.app_icon_path())
        mark.set_pixel_size(32)
        brand.append(mark)
        wordmark = Gtk.Label(label="Rayyan Ekitap")
        wordmark.add_css_class("brand-wordmark")
        brand.append(wordmark)
        header.pack_start(brand)

        about = Gtk.Button(icon_name=icons.INFO, tooltip_text="Hakkında")
        about.add_css_class("tool-btn")
        bind_touch_tooltip(about)
        about.connect("clicked", lambda _b: self.show_about())
        header.pack_end(about)

        header.pack_end(self._build_theme_button())

        refresh = Gtk.Button(icon_name=icons.REFRESH, tooltip_text="Kitaplığı yenile")
        refresh.add_css_class("tool-btn")
        bind_touch_tooltip(refresh)
        refresh.connect("clicked", lambda _b: self.reload())
        header.pack_end(refresh)
        return header

    def _build_theme_button(self) -> Gtk.Widget:
        self.theme_picker = ThemePicker(self._current_theme())
        self.theme_picker.connect("theme-chosen", self._on_theme_chosen)

        popover = Gtk.Popover()
        popover.add_css_class("sheet-popover")
        popover.set_child(self.theme_picker)

        button = Gtk.MenuButton(icon_name=icons.THEME, tooltip_text="Tema", popover=popover)
        button.add_css_class("tool-btn")
        button.add_css_class("flat")
        return button

    def _build_content(self) -> Gtk.Widget:
        self.shelf = ContinueShelf(self.covers)
        self.shelf.connect("open-book", lambda _s, item: self.open_book(item))

        self.sections_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=30)

        self.empty_page = Adw.StatusPage(icon_name=icons.BOOK, title="Kitap yok", vexpand=True)
        self.empty_page.set_visible(False)

        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=36)
        holder.add_css_class("catalog")
        holder.append(self.shelf)
        holder.append(self.sections_box)
        holder.append(self.empty_page)

        # `tightening-threshold` defaults to 400 px, above which a clamp lets
        # its child grow more slowly than the window: right for prose, wrong
        # for a grid, where it costs a wide board two columns.
        clamp = Adw.Clamp(maximum_size=1720, tightening_threshold=1720, child=holder)
        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
            child=clamp,
        )
        return self.scroller

    def _build_dock(self) -> Gtk.Widget:
        dock = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
        )
        dock.add_css_class("dock")
        dock.add_css_class("library-dock")

        self.search_entry = Gtk.SearchEntry(placeholder_text="Kitap ara…")
        self.search_entry.add_css_class("dock-search")
        self.search_entry.set_size_request(240, -1)
        self.search_entry.connect("search-changed", self._on_search_changed)
        dock.append(self.search_entry)

        self.toggles = SegmentedControl()
        for name in FILTERS:
            self.toggles.add(name, label=FILTER_LABELS[name])
        self.toggles.set_active_name(self.active_filter)
        self.toggles.connect("changed", self._on_filter_changed)

        # The chips are wider than a narrow window; let them slide instead of
        # forcing the window's minimum width up.
        chips = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_width=True,
            propagate_natural_height=True,
        )
        chips.set_child(self.toggles)
        dock.append(chips)

        self.btn_edit = Gtk.ToggleButton(tooltip_text="Kitapları kaldırmak için düzenle")
        self.btn_edit.add_css_class("dock-btn")
        self.btn_edit.add_css_class("dock-btn-labelled")
        edit_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        edit_content.append(Gtk.Image.new_from_icon_name(icons.EDIT))
        edit_content.append(Gtk.Label(label="Düzenle"))
        self.btn_edit.set_child(edit_content)
        self.btn_edit.set_visible(not self.library_mode)
        self.btn_edit.connect("toggled", self._on_edit_toggled)
        dock.append(self.btn_edit)
        return dock

    # -- data -------------------------------------------------------------

    def reload(self) -> None:
        """Re-read the catalogue and redraw. Cheap: 56 rows off local disk."""
        self.model.reload()
        self.toggles.set_label(
            "installed", f"{FILTER_LABELS['installed']} ({self.model.installed_count})"
        )
        self.render()

    def render(self) -> None:
        sections = self.model.sections(self.active_filter, self.query)

        # Every card holds a handler on its `BookItem`; a redraw that only
        # dropped the widgets would leave those behind.
        for card in self._cards:
            card.unbind()
        self._cards.clear()

        child = self.sections_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.sections_box.remove(child)
            child = following

        self._render_shelf()

        if not sections:
            self.empty_page.set_title(
                "Yüklü kitap yok" if self.active_filter == "installed"
                else "Sonuç bulunamadı"
            )
            self.empty_page.set_description(self.model.empty_message(self.active_filter))
            self.empty_page.set_visible(True)
            return

        self.empty_page.set_visible(False)
        for section in sections:
            self.sections_box.append(self._build_section(section))

    def _render_shelf(self) -> None:
        # The shelf belongs to the unfiltered library; a search or a grade tab
        # is a question about the catalogue.
        if self.query or self.active_filter not in ("all", "installed"):
            self.shelf.set_entries([])
            return
        self.shelf.set_entries(resolve_entries(
            self.settings.recent_books(), self.model.get, self._local_path,
        ))

    def _build_section(self, section: Section) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("section-header")
        title = Gtk.Label(label=section.title, xalign=0.0)
        title.add_css_class("section-title")
        header.append(title)
        count = Gtk.Label(label=section.count_text, xalign=0.0, hexpand=True,
                          valign=Gtk.Align.BASELINE)
        count.add_css_class("dim-label")
        header.append(count)
        box.append(header)

        grid = Gtk.FlowBox(
            orientation=Gtk.Orientation.HORIZONTAL,
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            min_children_per_line=1,
            max_children_per_line=MAX_COLUMNS,
            row_spacing=CARD_GAP,
            column_spacing=CARD_GAP,
            valign=Gtk.Align.START,
        )
        grid.add_css_class("books-grid")
        for item in section.items:
            grid.append(self._build_card(item))
        # The cards are the buttons; the flow box's own children only wrap them.
        child = grid.get_first_child()
        while child is not None:
            child.set_focusable(False)
            child = child.get_next_sibling()
        box.append(grid)
        return box

    def _build_card(self, item: BookItem) -> Gtk.Widget:
        card = BookCard(
            self.covers, library_mode=self.library_mode,
            show_confidence=getattr(self.app, "debug", False) is True,
        )
        card.set_editing(self.editing)
        card.connect("open-book", lambda _c, i: self.open_book(i))
        card.connect("install-book", lambda _c, i: self.confirm_install(i))
        card.connect("cancel-download", lambda _c, i: self.cancel_download(i))
        card.connect("uninstall-book", lambda _c, i: self.confirm_uninstall(i))
        card.bind(item)
        self._cards.append(card)
        return card

    # -- filters ----------------------------------------------------------

    def _on_filter_changed(self, *_args) -> None:
        name = self.toggles.get_active_name() or "all"
        if name == self.active_filter:
            return
        self.active_filter = name
        self.settings.set("library_filter", name)
        self.render()

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip()
        if query == self.query:
            return
        self.query = query
        self.render()

    def _on_edit_toggled(self, button: Gtk.ToggleButton) -> None:
        self.editing = button.get_active()
        for card in self._cards:
            card.set_editing(self.editing)

    def focus_search(self) -> None:
        self.search_entry.grab_focus()

    # -- theme and about --------------------------------------------------

    def _current_theme(self) -> str:
        theme = self.settings.get("theme") or "dark"
        return theme if theme in THEMES else "dark"

    def _on_theme_chosen(self, _picker, theme: str) -> None:
        self.settings.set("theme", theme)
        window = self.get_root()
        if window is not None and hasattr(window, "apply_theme"):
            window.apply_theme(theme)

    def sync_theme(self) -> None:
        """The theme may have been changed in the reader."""
        self.theme_picker.set_active(self._current_theme())

    def show_about(self) -> None:
        window = self.get_root()
        about = Adw.AboutWindow(
            transient_for=window if isinstance(window, Gtk.Window) else None,
            modal=True,
            application_name="Rayyan Ekitap",
            application_icon=icons.APP,
            version=__version__,
            developer_name=MADE_BY,
            comments="Akıllı tahta için etkileşimli ders kitabı okuyucu.\n\n"
                     f'<a href="{AUTHOR_URL}">{MADE_BY}</a>',
            website=WEBSITE,
            license_type=Gtk.License.AGPL_3_0,
        )
        about.add_legal_section(
            "Ders kitapları",
            "T.C. Millî Eğitim Bakanlığı",
            Gtk.License.CUSTOM,
            "Ders kitapları Millî Eğitim Bakanlığının yayınıdır ve OGM Materyal "
            "üzerinden indirilir. Rayyan Ekitap kitapların içeriğini değiştirmez.",
        )
        about.add_legal_section("Inter yazı tipi", "The Inter Project Authors",
                                Gtk.License.CUSTOM, "SIL Open Font License 1.1")
        _link_label(about, MADE_BY, AUTHOR_URL)
        about.present()

    # -- opening ----------------------------------------------------------

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def _local_path(self, item: BookItem) -> Optional[str]:
        from interaktiv_core import jobs

        return self.manager.get_local_path(item.id) or jobs.cached_preview(item.id)

    def open_book(self, item: BookItem) -> None:
        path = self._local_path(item)
        if not path:
            self.confirm_install(item)
            return
        self.emit("open-book", item, path)

    # -- downloading ------------------------------------------------------

    def confirm_install(self, item: BookItem) -> None:
        """Ask before a download: a book is tens to hundreds of megabytes."""
        if item.is_installed:
            self.open_book(item)
            return
        if self.library_mode:
            self.toast("Bu kitaplıkta indirme yapılamaz.")
            return

        dialog = ask(
            self,
            heading="Kitap indirilsin mi?",
            body=self._install_body(item, None),
            responses=[
                ("cancel", "Vazgeç", None),
                ("download", "İndir ve Aç", Adw.ResponseAppearance.SUGGESTED),
            ],
            default="download",
            on_response=lambda response: self._on_install_response(response, item),
        )
        self._probe_size(dialog, item)

    @staticmethod
    def _install_body(item: BookItem, size: Optional[int]) -> str:
        """
        What the teacher is agreeing to. The catalogue's books run from 16 MB
        to 392 MB, so the exact size is asked for in the background and
        dropped in when it arrives.
        """
        cost = f"{size / (1024 * 1024):.0f} MB" if size else "genellikle 50–400 MB"
        return (
            f"“{item.title}” bu bilgisayara indirilecek ({cost}). "
            "İndirme bitince kitap açılır ve internet olmadan da kullanılabilir."
        )

    def _probe_size(self, dialog: Adw.MessageDialog, item: BookItem) -> None:
        """Ask the CDN how big the book is, without blocking the dialog on it."""
        import threading

        from interaktiv_core import jobs

        url = (self.manager.books_by_id.get(item.id) or {}).get("url")
        if not url:
            return

        def worker():
            size = jobs.probe_download_size(url)
            if size:
                GLib.idle_add(self._apply_size, dialog, item, size)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_size(self, dialog, item: BookItem, size: int) -> bool:
        dialog.set_body(self._install_body(item, size))
        return GLib.SOURCE_REMOVE

    def _on_install_response(self, response: str, item: BookItem) -> None:
        if response == "download":
            self.install_book(item, open_when_done=True)

    def install_book(self, item: BookItem, open_when_done: bool = False) -> None:
        from interaktiv_core import jobs

        cached = jobs.cached_preview(item.id)
        if cached:
            # Already on disk from an earlier preview: installing is a rename.
            ok, message = self._promote_preview(item, cached)
            if not ok:
                self.toast(message)
                return
            self.reload()
            if open_when_done:
                self.open_book(item)
            return

        ok, message = self.manager.start_download(item.id)
        if not ok:
            self.toast(message)
            return
        if open_when_done:
            self._open_when_done.add(item.id)
        item.set_download({"status": "downloading", "progress": 0,
                           "downloaded_bytes": 0, "total_bytes": 0, "kind": "install"})
        self.watcher.start()

    def _promote_preview(self, item: BookItem, cached: str):
        target = os.path.join(self.manager.books_dir, f"{item.id}.pdf")
        try:
            os.makedirs(self.manager.books_dir, exist_ok=True)
            os.replace(cached, target)
        except OSError as error:
            return False, f"Kitap kaydedilemedi: {error}"
        return True, ""

    def cancel_download(self, item: BookItem) -> None:
        self._open_when_done.discard(item.id)
        ok, message = self.manager.cancel_download(item.id)
        if not ok:
            self.toast(message)
        item.set_download(None)

    # -- removing ---------------------------------------------------------

    def confirm_uninstall(self, item: BookItem) -> None:
        ask(
            self,
            heading="Kitap kaldırılsın mı?",
            body=(f"“{item.title}” bilgisayardan kaldırılacak{self._freed_space(item)}. "
                  "İstediğiniz zaman yeniden indirebilirsiniz."),
            responses=[
                ("cancel", "Vazgeç", None),
                ("delete", "Kaldır", Adw.ResponseAppearance.DESTRUCTIVE),
            ],
            on_response=lambda response: self._on_uninstall_response(response, item),
        )

    @staticmethod
    def _freed_space(item: BookItem) -> str:
        if not item.file_size:
            return ""
        return f" ve {item.file_size / (1024 * 1024):.0f} MB yer açılacak"

    def _on_uninstall_response(self, response: str, item: BookItem) -> None:
        if response != "delete":
            return
        ok, message = self.manager.uninstall_book(item.id)
        self.toast(message if not ok else f"“{item.title}” kaldırıldı.")
        if ok:
            self.settings.forget_book(item.id)
            self.reload()

    # -- download polling -------------------------------------------------

    def _on_download_tick(self, statuses: Dict[str, dict]) -> None:
        self.model.apply_download_statuses(statuses)

    def _on_downloads_settled(self) -> None:
        self.model.apply_download_statuses({})
        self.reload()
        waiting, self._open_when_done = self._open_when_done, set()
        for book_id in waiting:
            item = self.model.get(book_id)
            if item is not None and item.is_installed:
                self.open_book(item)
                break

    # -- lifecycle --------------------------------------------------------

    def refresh_shelf(self) -> None:
        """Called when a book is closed: its place on the shelf has changed."""
        self._render_shelf()
        self.sync_theme()

    def shutdown(self) -> None:
        self.watcher.stop()
        self.covers.shutdown()
        for card in self._cards:
            card.unbind()
        self._cards.clear()
