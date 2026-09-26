"""
The catalogue page: search, grade filters, and a grid of books per group.

The web dashboard lays this out as `auto-fill minmax(232px, 1fr)`, and
`Gtk.FlowBox` is the widget that means the same thing: give a card a natural
width, let the box fit as many per line as the allocation holds, and the column
count falls out of the width with no resize handler of our own.

It is a `Gtk.FlowBox` and not a `Gtk.GridView` for one reason. `GtkGridView` is
a scrollable that measures itself as a single column -- ask it how tall it is
and it answers `rows x height` for one-per-line, whatever width it is given --
which is right inside a `GtkScrolledWindow` of its own and badly wrong inside a
`GtkBox`, where that number becomes the scrollable height. Six sections of it
made the catalogue scroll several screens past its last card. `GtkFlowBox` is
height-for-width, so the page is exactly as tall as the books on it.

Nothing here needs virtualizing either way: the whole catalogue is 56 books,
and per-group boxes are what `renderCatalogSection` already builds.
"""

from typing import Dict, List, Optional

from gi.repository import Adw, GLib, GObject, Gtk

from .. import icons
from ..touch import bind_touch_tooltip
from .card import BookCard
from .covers import CoverLoader
from .downloads import DownloadWatcher
from .model import FILTER_LABELS, FILTERS, BookItem, LibraryModel, Section

# The cap only has to be higher than any board will ever fit; the column width
# itself is the card's (`BookCard.CARD_WIDTH`).
MAX_COLUMNS = 12

# Matches `.books-grid`'s `gap` in the web dashboard.
CARD_GAP = 16


class LibraryPage(Adw.NavigationPage):
    """Everything a teacher does before a book is open."""

    __gtype_name__ = "InteraktivLibraryPage"
    __gsignals__ = {
        # (BookItem, path) -- the book is on disk at `path` and should open.
        "open-book": (GObject.SignalFlags.RUN_FIRST, None, (object, str)),
    }

    def __init__(self, app):
        super().__init__(title="Kitaplık", tag="library")
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
        self._cards: List[BookCard] = []

        self._build()
        self.reload()

    def _restore_filter(self) -> str:
        stored = self.settings.get("library_filter", "all")
        return stored if stored in FILTERS else "all"

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        self.toasts = Adw.ToastOverlay()

        view = Adw.ToolbarView()
        view.add_top_bar(self._build_header())
        view.add_top_bar(self._build_filter_bar())
        view.set_content(self._build_content())

        self.toasts.set_child(view)
        self.set_child(self.toasts)

    def _build_header(self) -> Gtk.Widget:
        """
        Title, subtitle, and one button.

        The HIG asks a header bar to hold a small number of controls and to
        keep blank space free for dragging the window; the search entry used
        to sit here at four hundred pixels wide, hard against the close
        button. It now lives in the filter row below, where the catalogue's
        two ways of narrowing itself -- a word and a grade -- are side by side.
        """
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Kitaplık", subtitle=""))
        self.header_title = header.get_title_widget()

        refresh = Gtk.Button(icon_name=icons.REFRESH, tooltip_text="Kitaplığı yenile")
        bind_touch_tooltip(refresh)
        refresh.connect("clicked", lambda _b: self.reload())
        header.pack_start(refresh)
        return header

    def _build_filter_bar(self) -> Gtk.Widget:
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Kitap veya sınıf ara…",
        )
        self.search_entry.add_css_class("dashboard-search-input")
        self.search_entry.connect("search-changed", self._on_search_changed)
        # Wide enough for a book title, narrow enough to leave the grade tabs
        # the middle of the bar to themselves.
        self.search_entry.set_size_request(280, -1)

        self.toggles = Adw.ToggleGroup()
        self.toggles.add_css_class("dashboard-filter-bar")
        # libadwaita's own pill shape for a toggle group, rather than a
        # radius written here: the tabs then round the way every other
        # control in the window does, including after a theme change.
        self.toggles.add_css_class("round")
        self.toggles.set_halign(Gtk.Align.CENTER)
        for name in FILTERS:
            toggle = Adw.Toggle(name=name, label=FILTER_LABELS[name])
            self.toggles.add(toggle)
        self.toggles.set_active_name(self.active_filter)
        self.toggles.connect("notify::active-name", self._on_filter_changed)

        # Seven board-sized tabs are wider than a narrow window; let them slide
        # rather than forcing the window minimum up. Both natural sizes have to
        # be propagated: a centre box gives its centre child the natural width
        # it asks for, and a scroller that does not propagate one asks for
        # nothing -- which is exactly the width the tabs then got.
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_width=True,
            propagate_natural_height=True,
        )
        scroller.set_child(self.toggles)

        # A centre box, so the grade tabs stay on the middle of the screen
        # whatever the search entry beside them is doing.
        bar = Gtk.CenterBox()
        bar.add_css_class("toolbar")
        bar.add_css_class("library-filter-bar")
        bar.set_start_widget(self.search_entry)
        bar.set_center_widget(scroller)
        return bar

    def _build_content(self) -> Gtk.Widget:
        self.sections_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.sections_box.add_css_class("catalog")

        self.empty_page = Adw.StatusPage(
            icon_name=icons.BOOK,
            title="Kitap yok",
            vexpand=True,
        )
        self.empty_page.set_visible(False)

        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder.append(self.sections_box)
        holder.append(self.empty_page)

        # `tightening-threshold` defaults to 400 px, above which a clamp lets
        # its child grow more slowly than the window. That is right for a
        # column of prose and wrong for a grid: it would cost a 1600 px board
        # two columns. Tie it to the maximum so the grid gets the full width.
        clamp = Adw.Clamp(maximum_size=1600, tightening_threshold=1600, child=holder)
        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
            child=clamp,
        )
        return self.scroller

    # -- data -------------------------------------------------------------

    def reload(self) -> None:
        """Re-read the catalogue and redraw. Cheap: 56 rows off local disk."""
        self.model.reload()
        self._update_installed_badge()
        self.render()

    def _update_installed_badge(self) -> None:
        toggle = self.toggles.get_toggle_by_name("installed")
        if toggle is not None:
            toggle.set_label(f"{FILTER_LABELS['installed']} ({self.model.installed_count})")
        if self.header_title is not None:
            total = len(self.model.items)
            self.header_title.set_subtitle(
                f"{self.model.installed_count} / {total} kitap yüklü"
            )

    def render(self) -> None:
        sections = self.model.sections(self.active_filter, self.query)

        # Every card holds a handler on its `BookItem`; a redraw that only
        # dropped the widgets would leave those behind, and a download tick
        # would then refresh cards that are no longer on screen.
        for card in self._cards:
            card.unbind()
        self._cards.clear()

        child = self.sections_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.sections_box.remove(child)
            child = nxt

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

    def _build_section(self, section: Section) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.add_css_class("grade-group")

        # Title and count read as one line -- "Yüklü Kitaplar, 10 kitap" --
        # so the count sits next to the words it counts rather than a metre
        # away at the far edge of the grid.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        header.add_css_class("dashboard-section-header")
        title = Gtk.Label(label=section.title, xalign=0.0)
        title.add_css_class("title-4")
        header.append(title)
        count = Gtk.Label(label=section.count_text, xalign=0.0, hexpand=True)
        count.add_css_class("dim-label")
        count.add_css_class("dashboard-section-count")
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
        box.append(grid)
        return box

    # -- cards ------------------------------------------------------------

    def _build_card(self, item: BookItem) -> Gtk.Widget:
        card = BookCard(self.covers, library_mode=self.library_mode)
        card.connect("open-book", lambda _c, i: self.open_book(i))
        card.connect("preview-book", lambda _c, i: self.preview_book(i))
        card.connect("install-book", lambda _c, i: self.install_book(i))
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
        # `LibraryModel.sections` folds the query; keep the raw text so the
        # comparison happens in exactly one place.
        query = entry.get_text().strip()
        if query == self.query:
            return
        self.query = query
        self.render()

    def focus_search(self) -> None:
        self.search_entry.grab_focus()

    # -- actions ----------------------------------------------------------

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def open_book(self, item: BookItem) -> None:
        path = self.manager.get_local_path(item.id)
        if not path:
            from interaktiv_core import jobs

            path = jobs.cached_preview(item.id)
        if not path:
            self.preview_book(item)
            return
        self.emit("open-book", item, path)

    def preview_book(self, item: BookItem) -> None:
        """
        Open a book that is not installed.

        The web edition streams byte ranges off the CDN and opens page 1 within
        a second. There is no native equivalent: PyMuPDF cannot open a URL, its
        `stream=` argument wants the whole file, and a partially-ranged PDF is
        not parseable. A preview is therefore the whole book, downloaded once
        into a cache so that installing it afterwards is a rename.
        """
        from interaktiv_core import jobs

        if item.is_installed:
            self.open_book(item)
            return

        cached = jobs.cached_preview(item.id)
        if cached:
            self.emit("open-book", item, cached)
            return

        if self.library_mode:
            self.toast("Bu kitaplıkta indirme yapılamaz.")
            return

        dialog = Adw.AlertDialog(
            heading="Kitap indirilsin mi?",
            body=self._preview_body(item, None),
        )
        dialog.add_response("cancel", "Vazgeç")
        dialog.add_response("download", "İndir ve Aç")
        dialog.set_response_appearance("download", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("download")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_preview_response, item)
        dialog.present(self)
        self._probe_preview_size(dialog, item)

    @staticmethod
    def _preview_body(item: BookItem, size: Optional[int]) -> str:
        """
        What the teacher is agreeing to.

        The catalogue's books run from 16 MB to 392 MB, so "the whole book" is
        not a number anyone can guess; the exact one is asked for in the
        background and dropped in when it arrives.
        """
        cost = (f"{size / (1024 * 1024):.0f} MB" if size
                else "genellikle 50–400 MB")
        return (
            f"“{item.title}” henüz indirilmedi. Görüntülemek için kitabın "
            f"tamamı indirilir ({cost}). İndirme bitince kitap açılır, ve "
            "daha sonra “İndir” derseniz yeniden indirilmez."
        )

    def _probe_preview_size(self, dialog: Adw.AlertDialog, item: BookItem) -> None:
        """Ask the CDN how big the book is, without blocking the dialog on it."""
        import threading

        from interaktiv_core import jobs

        url = (self.manager.books_by_id.get(item.id) or {}).get("url")
        if not url:
            return

        def worker():
            size = jobs.probe_download_size(url)
            if size:
                GLib.idle_add(self._apply_preview_size, dialog, item, size)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_preview_size(self, dialog, item: BookItem, size: int) -> bool:
        dialog.set_body(self._preview_body(item, size))
        return GLib.SOURCE_REMOVE

    def _on_preview_response(self, _dialog, response: str, item: BookItem) -> None:
        if response != "download":
            return
        from interaktiv_core import appdirs, jobs

        appdirs.ensure(appdirs.preview_cache_dir())
        target = jobs.preview_path(item.id)
        ok, message = self.manager.download_to(
            item.id, target, on_done=lambda path: GLib.idle_add(
                self._on_preview_done, item.id, path
            )
        )
        if not ok:
            self.toast(message)
            return
        item.set_download({"status": "downloading", "progress": 0,
                           "downloaded_bytes": 0, "total_bytes": 0, "kind": "preview"})
        self.watcher.start()

    def _on_preview_done(self, book_id: str, path: Optional[str]) -> bool:
        from interaktiv_core import jobs

        item = self.model.get(book_id)
        if path and item is not None:
            jobs.prune_previews(keep=path)
            self.covers.forget(book_id)
            self.emit("open-book", item, path)
        return GLib.SOURCE_REMOVE

    def install_book(self, item: BookItem) -> None:
        from interaktiv_core import jobs

        cached = jobs.cached_preview(item.id)
        if cached:
            # Already downloaded once for a preview: installing is a rename.
            ok, message = self._promote_preview(item, cached)
            self.toast(message)
            if ok:
                self.reload()
            return

        ok, message = self.manager.start_download(item.id)
        if not ok:
            self.toast(message)
            return
        item.set_download({"status": "downloading", "progress": 0,
                           "downloaded_bytes": 0, "total_bytes": 0, "kind": "install"})
        self.watcher.start()

    def _promote_preview(self, item: BookItem, cached: str):
        import os

        target = os.path.join(self.manager.books_dir, f"{item.id}.pdf")
        try:
            os.makedirs(self.manager.books_dir, exist_ok=True)
            os.replace(cached, target)
        except OSError as err:
            return False, f"Kitap kaydedilemedi: {err}"
        return True, f"“{item.title}” kitaplığa eklendi."

    def cancel_download(self, item: BookItem) -> None:
        ok, message = self.manager.cancel_download(item.id)
        if not ok:
            self.toast(message)
        item.set_download(None)

    def confirm_uninstall(self, item: BookItem) -> None:
        dialog = Adw.AlertDialog(
            heading="Kitap silinsin mi?",
            body=f"“{item.title}” bilgisayardan kaldırılacak. İstediğiniz zaman yeniden indirebilirsiniz.",
        )
        dialog.add_response("cancel", "Vazgeç")
        dialog.add_response("delete", "Sil")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_uninstall_response, item)
        dialog.present(self)

    def _on_uninstall_response(self, _dialog, response: str, item: BookItem) -> None:
        if response != "delete":
            return
        ok, message = self.manager.uninstall_book(item.id)
        self.toast(message if not ok else f"“{item.title}” kaldırıldı.")
        if ok:
            self.reload()

    # -- download polling -------------------------------------------------

    def _on_download_tick(self, statuses: Dict[str, dict]) -> None:
        self.model.apply_download_statuses(statuses)

    def _on_downloads_settled(self) -> None:
        self.model.apply_download_statuses({})
        self.reload()

    # -- lifecycle --------------------------------------------------------

    def shutdown(self) -> None:
        self.watcher.stop()
        self.covers.shutdown()
        for card in self._cards:
            card.unbind()
        self._cards.clear()
