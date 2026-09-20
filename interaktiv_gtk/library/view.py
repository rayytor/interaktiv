"""
The catalogue page: search, grade filters, and a grid of books per group.

The web dashboard lays this out as `auto-fill minmax(260px, 1fr)`.
`Gtk.GridView` takes a column count rather than a column width, but it derives
that count the same way: available width divided by the natural width of a
child, clamped to `max-columns`. Giving a card a natural width and leaving the
cap generous therefore reproduces `auto-fill` exactly -- measured at 1 column
at 520 px through to 6 at 1600 px -- with no resize handler of our own.

Each group gets a `Gtk.GridView` of its own rather than one big list with
section headers, because only `Gtk.ListView` can draw section headers and the
whole catalogue is 56 books: nothing here needs virtualizing, and per-group
grids are what `renderCatalogSection` already builds.
"""

from typing import Dict, List, Optional

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .. import icons
from .card import BookCard
from .covers import CoverLoader
from .downloads import DownloadWatcher
from .model import FILTER_LABELS, FILTERS, BookItem, LibraryModel, Section

# The cap only has to be higher than any board will ever fit; the column width
# itself is the card's (`BookCard.CARD_WIDTH`).
MAX_COLUMNS = 12


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
        self._grids: List[Gtk.GridView] = []
        self._factory = Gtk.SignalListItemFactory()
        self._factory.connect("setup", self._on_setup)
        self._factory.connect("bind", self._on_bind)
        self._factory.connect("unbind", self._on_unbind)

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
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Kitaplık", subtitle=""))
        self.header_title = header.get_title_widget()

        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Kitap veya sınıf ara…",
            hexpand=True,
        )
        self.search_entry.add_css_class("dashboard-search-input")
        self.search_entry.connect("search-changed", self._on_search_changed)
        clamp = Adw.Clamp(maximum_size=420, tightening_threshold=420,
                          child=self.search_entry)
        header.pack_end(clamp)

        refresh = Gtk.Button(icon_name=icons.REFRESH, tooltip_text="Kitaplığı yenile")
        refresh.connect("clicked", lambda _b: self.reload())
        header.pack_start(refresh)
        return header

    def _build_filter_bar(self) -> Gtk.Widget:
        self.toggles = Adw.ToggleGroup()
        self.toggles.add_css_class("dashboard-filter-bar")
        self.toggles.set_halign(Gtk.Align.CENTER)
        for name in FILTERS:
            toggle = Adw.Toggle(name=name, label=FILTER_LABELS[name])
            self.toggles.add(toggle)
        self.toggles.set_active_name(self.active_filter)
        self.toggles.connect("notify::active-name", self._on_filter_changed)

        # Seven board-sized tabs are wider than a narrow window; let them slide
        # rather than forcing the window minimum up.
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            hexpand=True,
        )
        scroller.set_child(self.toggles)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.add_css_class("toolbar")
        bar.append(scroller)
        return bar

    def _build_content(self) -> Gtk.Widget:
        self.sections_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=26)
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

        child = self.sections_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.sections_box.remove(child)
            child = nxt
        self._grids.clear()

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

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header.add_css_class("dashboard-section-header")
        title = Gtk.Label(label=section.title, xalign=0.0, hexpand=True)
        title.add_css_class("dashboard-section-title")
        header.append(title)
        count = Gtk.Label(label=section.count_text)
        count.add_css_class("dashboard-section-count")
        header.append(count)
        box.append(header)

        store = Gio.ListStore(item_type=BookItem)
        for item in section.items:
            store.append(item)

        grid = Gtk.GridView(
            model=Gtk.NoSelection(model=store),
            factory=self._factory,
            max_columns=MAX_COLUMNS,
            min_columns=1,
            single_click_activate=False,
        )
        grid.add_css_class("books-grid")
        grid.set_vscroll_policy(Gtk.ScrollablePolicy.NATURAL)
        self._grids.append(grid)
        box.append(grid)
        return box

    # -- grid factory -----------------------------------------------------

    def _on_setup(self, _factory, list_item: Gtk.ListItem) -> None:
        card = BookCard(self.covers, library_mode=self.library_mode)
        card.connect("open-book", lambda _c, item: self.open_book(item))
        card.connect("preview-book", lambda _c, item: self.preview_book(item))
        card.connect("install-book", lambda _c, item: self.install_book(item))
        card.connect("cancel-download", lambda _c, item: self.cancel_download(item))
        card.connect("uninstall-book", lambda _c, item: self.confirm_uninstall(item))
        list_item.set_child(card)

    def _on_bind(self, _factory, list_item: Gtk.ListItem) -> None:
        list_item.get_child().bind(list_item.get_item())

    def _on_unbind(self, _factory, list_item: Gtk.ListItem) -> None:
        list_item.get_child().unbind()

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
