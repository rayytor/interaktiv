"""
The reader sidebar: thumbnails, bookmarks (TOC), and activities.

Layout uses an `Adw.ViewStack` and `Adw.InlineViewSwitcher` with three tabs:
  1. Thumbnails (`ThumbnailsPanel`): page thumbnails with spread grouping in book
     mode (`initBookThumbnails`, `viewer.js:2183`) and single pages in single/scroll mode.
     Recycling in `Gtk.GridView` / `Gtk.ListView` is the lazy loader.
  2. Bookmarks (`BookmarksPanel`): hierarchical outline from `doc.get_toc()`
     rendered using `Gtk.TreeListModel` + `Gtk.TreeExpander` + `Gtk.ListView`.
  3. Activities (`ActivitiesSidebar`): lettered & interactive activities list.
"""

from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from ..render.service import LANE_THUMBNAIL

BATCH = 40

# The page area of one thumbnail row, in pixels -- a spread included, so the
# two sheets of a spread get half of it each. Everything else about the row
# (its height, the scale it is rendered at, and through those the width the
# sidebar asks for) is derived from this and the page's own aspect ratio.
THUMB_WIDTH = 140
SPREAD_GAP = 4
# Rendered a little above display size: a thumbnail is read at arm's length on
# a board, and a soft one is worse than a slightly dearer render.
THUMB_OVERSAMPLE = 1.5
# Portrait A4 as the stand-in for a page whose size is not known yet.
DEFAULT_RATIO = 1.414


# ==============================================================================
# 1. Activities Tab
# ==============================================================================

class ActivityItem(GObject.Object):
    __gtype_name__ = "InteraktivActivityItem"

    def __init__(self, page: int, act_index: int, name: str,
                 items: int, interactive: bool):
        super().__init__()
        self.page = page
        self.act_index = act_index
        self.name = name
        self.items = items
        self.interactive = interactive

    @property
    def key(self) -> Tuple[int, int]:
        return (self.page, self.act_index)


class ActivitiesSidebar(Gtk.Box):
    __gtype_name__ = "InteraktivActivitiesSidebar"

    __gsignals__ = {
        # A row was chosen: page number and the activity's position on it.
        "activity-chosen": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("activities-sidebar")
        self._fill: Optional[Iterator] = None
        self._fill_source = 0
        self._selecting = False

        self.heading = Gtk.Label(label="Etkinlikler", xalign=0.0)
        self.heading.add_css_class("sidebar-heading")
        self.subheading = Gtk.Label(label="", xalign=0.0)
        self.subheading.add_css_class("sidebar-subheading")
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        head.add_css_class("sidebar-head")
        head.append(self.heading)
        head.append(self.subheading)
        self.append(head)
        self.append(Gtk.Separator())

        self.store = Gio.ListStore(item_type=ActivityItem)
        self.selection = Gtk.SingleSelection(model=self.store)
        self.selection.set_autoselect(False)
        self.selection.set_can_unselect(True)
        self.selection.connect("notify::selected", self._on_selected)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        self.list = Gtk.ListView(model=self.selection, factory=factory)
        self.list.add_css_class("activities-list")
        self.list.add_css_class("navigation-sidebar")

        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True
        )
        scroller.set_child(self.list)

        self.empty = Adw.StatusPage(
            icon_name="edit-find-symbolic",
            title="Etkinlik yok",
            description="Bu kitap için hazırlanmış etkinlik bulunamadı.",
            vexpand=True,
        )
        self.empty.add_css_class("compact")

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(scroller, "list")
        self.stack.set_visible_child_name("empty")
        self.append(self.stack)

    # -- filling ----------------------------------------------------------

    def load(self, summaries: Iterator[Tuple[int, int, str, int, bool]]) -> None:
        """Start (or restart) filling the list from a summaries generator."""
        self.cancel_fill()
        self.store.remove_all()
        self._fill = iter(summaries)
        self._fill_source = GLib.idle_add(self._fill_batch)

    def cancel_fill(self) -> None:
        if self._fill_source:
            GLib.source_remove(self._fill_source)
            self._fill_source = 0
        self._fill = None

    def _fill_batch(self) -> bool:
        added = 0
        try:
            for _ in range(BATCH):
                page, act_index, name, items, interactive = next(self._fill)
                self.store.append(
                    ActivityItem(page, act_index, name, items, interactive)
                )
                added += 1
        except StopIteration:
            self._fill_source = 0
            self._fill = None
            self._update_heading()
            return GLib.SOURCE_REMOVE
        except Exception:
            self._fill_source = 0
            self._fill = None
            self._update_heading()
            return GLib.SOURCE_REMOVE
        if added:
            self._update_heading()
        return GLib.SOURCE_CONTINUE

    def _update_heading(self) -> None:
        count = self.store.get_n_items()
        live = sum(
            1 for i in range(count) if self.store.get_item(i).interactive
        )
        self.subheading.set_label(
            f"{count} etkinlik · {live} etkileşimli" if count else ""
        )
        self.stack.set_visible_child_name("list" if count else "empty")

    # -- rows -------------------------------------------------------------

    def _on_setup(self, _factory, list_item) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.add_css_class("activity-row")

        page = Gtk.Label(xalign=1.0)
        page.add_css_class("activity-row-page")
        page.set_width_chars(4)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        name = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class("activity-row-name")
        detail = Gtk.Label(xalign=0.0)
        detail.add_css_class("activity-row-detail")
        text.append(name)
        text.append(detail)

        bolt = Gtk.Label(label="⚡")
        bolt.add_css_class("activity-row-bolt")

        row.append(page)
        row.append(text)
        row.append(bolt)
        list_item.set_child(row)
        list_item.page_label = page
        list_item.name_label = name
        list_item.detail_label = detail
        list_item.bolt = bolt

    def _on_bind(self, _factory, list_item) -> None:
        item = list_item.get_item()
        list_item.page_label.set_label(str(item.page))
        list_item.name_label.set_label(item.name or "Etkinlik")
        list_item.detail_label.set_label(
            f"{item.items} soru" if item.items else "—"
        )
        list_item.bolt.set_visible(item.interactive)

    # -- selection --------------------------------------------------------

    def _on_selected(self, *_args) -> None:
        if self._selecting:
            return
        item = self.selection.get_selected_item()
        if item is None:
            return
        self.emit("activity-chosen", item.page, item.act_index)

    def select(self, page: int, act_index: int) -> None:
        for i in range(self.store.get_n_items()):
            item = self.store.get_item(i)
            if item.page == page and item.act_index == act_index:
                self._selecting = True
                self.selection.set_selected(i)
                self._selecting = False
                self.list.scroll_to(i, Gtk.ListScrollFlags.NONE, None)
                return

    def shutdown(self) -> None:
        self.cancel_fill()
        self.store.remove_all()


# ==============================================================================
# 2. Thumbnails Tab
# ==============================================================================

class ThumbnailItem(GObject.Object):
    __gtype_name__ = "InteraktivThumbnailItem"

    def __init__(self, pages: Tuple[int, ...], label: str):
        super().__init__()
        self.pages = tuple(pages)
        self.start_page = pages[0]
        self.label = label


class ThumbnailsPanel(Gtk.Box):
    __gtype_name__ = "InteraktivThumbnailsPanel"

    __gsignals__ = {
        "page-chosen": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.add_css_class("thumbnails-panel")

        self.service = None
        self.page_count = 0
        self.page_sizes: Tuple[Tuple[float, float], ...] = ()
        self.mode = "book"
        self.rotation = 0
        self._selecting = False
        self._thumb_cache: Dict[int, object] = {}  # page -> Gdk.Texture
        self._bound_pictures: Dict[int, List[Gtk.Picture]] = {}

        self.store = Gio.ListStore(item_type=ThumbnailItem)
        self.selection = Gtk.SingleSelection(model=self.store)
        self.selection.set_autoselect(False)
        self.selection.set_can_unselect(True)
        self.selection.connect("notify::selected", self._on_selected)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)
        factory.connect("unbind", self._on_unbind)
        self._factory = factory

        self.grid = Gtk.GridView(model=self.selection, factory=factory)
        self.grid.add_css_class("thumbnails-grid")
        self.grid.set_max_columns(1)
        self.grid.set_min_columns(1)

        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )
        scroller.set_child(self.grid)
        self.scroller = scroller
        self.append(scroller)

    def load(self, page_count: int, mode: str, rotation: int, service=None,
             page_sizes: Sequence[Tuple[float, float]] = ()) -> None:
        self.page_count = page_count
        self.page_sizes = tuple(tuple(size) for size in page_sizes)
        self.mode = mode
        self.rotation = rotation
        if service is not None:
            self.service = service
        self._rebuild_items()

    def set_view_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        # A spread gives each sheet half the width a single page gets, so the
        # cached pixels are at the wrong scale for the mode we just entered.
        self._thumb_cache.clear()
        self._rebuild_items()

    def set_rotation(self, rotation: int) -> None:
        rotation %= 360
        if rotation == self.rotation:
            return
        self.rotation = rotation
        self._thumb_cache.clear()
        self._rebuild_items()

    def _rebuild_items(self) -> None:
        self.store.remove_all()
        if self.page_count <= 0:
            return

        if self.mode == "book":
            # Spreads: [1] (Cover), [2, 3], [4, 5], ...
            self.store.append(ThumbnailItem((1,), "Sayfa 1"))
            for p in range(2, self.page_count + 1, 2):
                if p + 1 <= self.page_count:
                    self.store.append(ThumbnailItem((p, p + 1), f"Sayfa {p}–{p + 1}"))
                else:
                    self.store.append(ThumbnailItem((p,), f"Sayfa {p}"))
        else:
            # Single / Scroll
            for p in range(1, self.page_count + 1):
                self.store.append(ThumbnailItem((p,), f"Sayfa {p}"))

    # -- geometry ---------------------------------------------------------

    def _page_ratio(self, page: int) -> float:
        """Height over width for `page`, as it will be drawn at `rotation`."""
        if 1 <= page <= len(self.page_sizes):
            width, height = self.page_sizes[page - 1]
        else:
            return DEFAULT_RATIO
        if width <= 0 or height <= 0:
            return DEFAULT_RATIO
        if self.rotation % 180:
            width, height = height, width
        return height / width

    def _page_width_pt(self, page: int) -> float:
        if 1 <= page <= len(self.page_sizes):
            width, height = self.page_sizes[page - 1]
            if self.rotation % 180:
                width = height
            if width > 0:
                return width
        return 0.0

    @staticmethod
    def _slot_width(page_count: int) -> int:
        """How wide one sheet of a row is: the whole row, or half of a spread."""
        if page_count > 1:
            return max(1, (THUMB_WIDTH - SPREAD_GAP) // 2)
        return THUMB_WIDTH

    # -- factory callbacks ------------------------------------------------

    def _on_setup(self, _factory, list_item) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("thumbnail-item")

        container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=SPREAD_GAP)
        container.add_css_class("thumb-canvas-container")
        # Hug the sheets rather than stretching across the row: the sidebar is
        # only as wide as it needs to be, and the box reads as the paper.
        container.set_halign(Gtk.Align.CENTER)

        pic1 = Gtk.Picture()
        pic1.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic1.set_can_shrink(True)
        pic1.add_css_class("thumb-pic")

        pic2 = Gtk.Picture()
        pic2.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic2.set_can_shrink(True)
        pic2.add_css_class("thumb-pic")

        container.append(pic1)
        container.append(pic2)

        label = Gtk.Label()
        label.add_css_class("thumb-number")

        box.append(container)
        box.append(label)

        list_item.set_child(box)
        list_item.pic1 = pic1
        list_item.pic2 = pic2
        list_item.label = label

    def _on_bind(self, _factory, list_item) -> None:
        item = list_item.get_item()
        if item is None:
            return
        list_item.label.set_text(item.label)

        pics = [list_item.pic1, list_item.pic2]
        slot = self._slot_width(len(item.pages))
        for i, page in enumerate(item.pages):
            pic = pics[i]
            pic.set_visible(True)
            # The row is sized from the paper, not from whatever pixels have
            # arrived: a page that has not been drawn yet holds its own space,
            # so the list does not jump as thumbnails land.
            pic.set_size_request(slot, max(1, round(slot * self._page_ratio(page))))
            self._bound_pictures.setdefault(page, []).append(pic)
            if page in self._thumb_cache:
                pic.set_paintable(self._thumb_cache[page])
            else:
                pic.set_paintable(None)
                self._request_thumbnail(page, slot)

        if len(item.pages) < 2:
            list_item.pic2.set_visible(False)
            list_item.pic2.set_paintable(None)

    def _on_unbind(self, _factory, list_item) -> None:
        item = list_item.get_item()
        if item is not None:
            for page in item.pages:
                if page in self._bound_pictures:
                    if list_item.pic1 in self._bound_pictures[page]:
                        self._bound_pictures[page].remove(list_item.pic1)
                    if list_item.pic2 in self._bound_pictures[page]:
                        self._bound_pictures[page].remove(list_item.pic2)
                    if not self._bound_pictures[page]:
                        del self._bound_pictures[page]
        list_item.pic1.set_paintable(None)
        list_item.pic2.set_paintable(None)

    def _request_thumbnail(self, page: int, slot_width: int) -> None:
        if self.service is None:
            return
        width_pt = self._page_width_pt(page)
        # Drawn to the size it is shown at rather than to a fixed fraction of
        # the sheet: a textbook page and a worksheet are not the same number of
        # points across, and a thumbnail lane render is the cheapest one to get
        # wrong in both directions.
        scale = 0.25 if width_pt <= 0 else (slot_width * THUMB_OVERSAMPLE) / width_pt
        self.service.submit(
            page, scale=max(0.05, min(0.5, scale)), rotation=self.rotation,
            lane=LANE_THUMBNAIL, generation=self.service.generation,
        )

    def on_thumbnail_result(self, result) -> None:
        if result.request.lane != LANE_THUMBNAIL:
            return
        if result.request.rotation != self.rotation % 360:
            # Drawn for a rotation the reader has since left; the pages it
            # belongs to were re-requested when the rotation changed.
            return
        page = result.request.page
        self._thumb_cache[page] = result.texture
        for pic in self._bound_pictures.get(page, []):
            pic.set_paintable(result.texture)

    # -- selection & navigation -------------------------------------------

    def _on_selected(self, *_args) -> None:
        if self._selecting:
            return
        item = self.selection.get_selected_item()
        if item is None:
            return
        self.emit("page-chosen", item.start_page)

    def update_active_page(self, page: int) -> None:
        for i in range(self.store.get_n_items()):
            item = self.store.get_item(i)
            if page in item.pages:
                if self.selection.get_selected() != i:
                    self._selecting = True
                    self.selection.set_selected(i)
                    self._selecting = False
                    self.grid.scroll_to(i, Gtk.ListScrollFlags.NONE, None)
                return

    def shutdown(self) -> None:
        self.store.remove_all()
        self._thumb_cache.clear()
        self._bound_pictures.clear()
        self.service = None


# ==============================================================================
# 3. Bookmarks (TOC) Tab
# ==============================================================================

class TocItem(GObject.Object):
    __gtype_name__ = "InteraktivTocItem"

    def __init__(self, title: str, page: int):
        super().__init__()
        self.title = title
        self.page = page
        self.children = Gio.ListStore(item_type=TocItem)


def build_toc_tree(raw_toc: Sequence) -> Gio.ListStore:
    root_store = Gio.ListStore(item_type=TocItem)
    stack = [(0, None, root_store)]
    for entry in raw_toc:
        if len(entry) < 3:
            continue
        lvl, title, page = entry[0], str(entry[1]), int(entry[2])
        item = TocItem(title, page)
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        parent_store = stack[-1][2]
        parent_store.append(item)
        stack.append((lvl, item, item.children))
    return root_store


class BookmarksPanel(Gtk.Box):
    __gtype_name__ = "InteraktivBookmarksPanel"

    __gsignals__ = {
        "page-chosen": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.add_css_class("bookmarks-panel")
        self._selecting = False

        self.empty = Adw.StatusPage(
            icon_name="user-bookmarks-symbolic",
            title="Yer imi yok",
            description="Bu PDF'te yer imi bulunamadı.",
            vexpand=True,
        )
        self.empty.add_css_class("compact")

        self.list_container = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vexpand=True,
        )

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.list_container, "list")
        self.stack.set_visible_child_name("empty")
        self.append(self.stack)

    def load(self, raw_toc: Sequence) -> None:
        if not raw_toc:
            self.stack.set_visible_child_name("empty")
            return

        root_store = build_toc_tree(raw_toc)
        if root_store.get_n_items() == 0:
            self.stack.set_visible_child_name("empty")
            return

        def create_model(item):
            if item.children.get_n_items() > 0:
                return item.children
            return None

        tree_model = Gtk.TreeListModel.new(root_store, False, True, create_model)
        selection = Gtk.SingleSelection(model=tree_model)
        selection.set_autoselect(False)
        selection.set_can_unselect(True)
        selection.connect("notify::selected", self._on_selected)
        self.selection = selection

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)

        list_view = Gtk.ListView(model=selection, factory=factory)
        list_view.add_css_class("bookmarks-list")
        list_view.add_css_class("navigation-sidebar")
        self.list_container.set_child(list_view)
        self.stack.set_visible_child_name("list")

    def _on_setup(self, _factory, list_item) -> None:
        expander = Gtk.TreeExpander()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("bookmark-row")

        title = Gtk.Label(xalign=0.0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("bookmark-title")

        page = Gtk.Label(xalign=1.0)
        page.add_css_class("bookmark-page")

        row.append(title)
        row.append(page)
        expander.set_child(row)
        list_item.set_child(expander)
        list_item.expander = expander
        list_item.title = title
        list_item.page = page

    def _on_bind(self, _factory, list_item) -> None:
        tree_row = list_item.get_item()
        list_item.expander.set_list_row(tree_row)
        item = tree_row.get_item()
        if item is not None:
            list_item.title.set_text(item.title)
            list_item.page.set_text(str(item.page) if item.page > 0 else "")

    def _on_selected(self, *_args) -> None:
        if self._selecting:
            return
        tree_row = self.selection.get_selected_item()
        if tree_row is None:
            return
        item = tree_row.get_item()
        if item and item.page > 0:
            self.emit("page-chosen", item.page)

    def shutdown(self) -> None:
        self.list_container.set_child(None)
        self.stack.set_visible_child_name("empty")


# ==============================================================================
# 4. Top-level Reader Sidebar
# ==============================================================================

class ReaderSidebar(Gtk.Box):
    __gtype_name__ = "InteraktivReaderSidebar"

    __gsignals__ = {
        "page-chosen": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "activity-chosen": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("reader-sidebar")

        self.thumbnails = ThumbnailsPanel()
        self.thumbnails.connect("page-chosen", lambda _w, p: self.emit("page-chosen", p))

        self.bookmarks = BookmarksPanel()
        self.bookmarks.connect("page-chosen", lambda _w, p: self.emit("page-chosen", p))

        self.activities = ActivitiesSidebar()
        self.activities.connect("activity-chosen", lambda _w, p, a: self.emit("activity-chosen", p, a))

        self.view_stack = Adw.ViewStack(vexpand=True)
        self.view_stack.add_titled_with_icon(
            self.thumbnails, "thumbnails", "Küçük Resimler", "view-grid-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.bookmarks, "bookmarks", "İçindekiler", "user-bookmarks-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.activities, "activities", "Etkinlikler", "selection-mode-symbolic"
        )

        switcher = Adw.InlineViewSwitcher()
        switcher.set_stack(self.view_stack)
        # Icons, not labels: three Turkish tab names want three hundred pixels
        # of header, and the sidebar is a hundred narrower than that. The
        # switcher keeps each page's title as the button's tooltip.
        switcher.set_display_mode(Adw.InlineViewSwitcherDisplayMode.ICONS)
        switcher.add_css_class("sidebar-switcher")

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head.add_css_class("sidebar-header-box")
        head.append(switcher)
        head.append(Gtk.Separator())

        self.append(head)
        self.append(self.view_stack)

    def load_book(self, info, service, rotation: int, view_mode: str) -> None:
        self.thumbnails.load(
            info.page_count, view_mode, rotation, service=service,
            page_sizes=info.page_sizes,
        )
        self.bookmarks.load(info.toc)

    def load_activities(self, summaries: Iterator[Tuple[int, int, str, int, bool]]) -> None:
        self.activities.load(summaries)

    def set_view_mode(self, mode: str) -> None:
        self.thumbnails.set_view_mode(mode)

    def set_rotation(self, rotation: int) -> None:
        self.thumbnails.set_rotation(rotation)

    def update_active_page(self, page: int) -> None:
        self.thumbnails.update_active_page(page)

    def select_activity(self, page: int, act_index: int) -> None:
        self.activities.select(page, act_index)

    def on_thumbnail_result(self, result) -> None:
        self.thumbnails.on_thumbnail_result(result)

    def shutdown(self) -> None:
        self.thumbnails.shutdown()
        self.bookmarks.shutdown()
        self.activities.shutdown()
