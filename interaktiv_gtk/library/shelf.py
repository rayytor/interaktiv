"""
"Derse devam et": the book that was open last, and the ones before it.

A lesson usually starts where the last one stopped. The shelf puts that book,
at that page, one tap away at the top of the library, with the few books read
before it beside it.
"""

from typing import List, Optional, Tuple

from gi.repository import GObject, Gtk, Pango

from .. import icons
from .card import COVER_ASPECT_RATIO, AspectCover
from .model import BookItem

HERO_COVER_WIDTH = 150
RECENT_COVER_WIDTH = 104

# How many earlier books are shown beside the last one.
RECENT_SHOWN = 4

# (book, page it was left on, its page count or 0 if unknown)
Entry = Tuple[BookItem, int, int]


def page_text(page: int, pages: int) -> str:
    return f"Sayfa {page} / {pages}" if pages else f"Sayfa {page}"


class _Cover(AspectCover):
    """A cover picture that fills in when its texture arrives."""

    def __init__(self, covers, item: BookItem, width: int):
        super().__init__(COVER_ASPECT_RATIO, width)
        self.add_css_class("book-cover")
        self.set_size_request(width, -1)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)

        overlay = Gtk.Overlay()
        placeholder = Gtk.Image.new_from_icon_name(icons.BOOK)
        placeholder.set_pixel_size(32)
        placeholder.add_css_class("book-cover-placeholder")
        overlay.set_child(placeholder)
        picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN)
        picture.set_visible(False)
        overlay.add_overlay(picture)
        self.set_child(overlay)

        def show(texture) -> None:
            if texture is not None:
                picture.set_paintable(texture)
                picture.set_visible(True)

        cached = covers.cached(item.id)
        if cached is not None:
            show(cached)
        else:
            covers.load(item.id, show)


class ContinueShelf(Gtk.Box):
    __gtype_name__ = "InteraktivContinueShelf"
    __gsignals__ = {
        "open-book": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, covers):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self.add_css_class("continue-shelf")
        self.covers = covers

    def set_entries(self, entries: List[Entry]) -> None:
        """Show these books, newest first. No entries hides the shelf."""
        child = self.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.remove(child)
            child = following

        self.set_visible(bool(entries))
        if not entries:
            return
        self.append(self._hero(*entries[0]))
        earlier = entries[1:1 + RECENT_SHOWN]
        if earlier:
            self.append(self._recents(earlier))

    # -- the last book ----------------------------------------------------

    def _hero(self, item: BookItem, page: int, pages: int) -> Gtk.Widget:
        button = Gtk.Button(halign=Gtk.Align.START)
        button.add_css_class("flat")
        button.add_css_class("hero-card")
        button.connect("clicked", lambda _b: self.emit("open-book", item))

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        row.append(_Cover(self.covers, item, HERO_COVER_WIDTH))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       valign=Gtk.Align.CENTER, hexpand=True)
        eyebrow = Gtk.Label(label="DERSE DEVAM ET", xalign=0.0)
        eyebrow.add_css_class("eyebrow")
        text.append(eyebrow)

        title = Gtk.Label(
            label=item.title, xalign=0.0, wrap=True, lines=2, max_width_chars=22,
            ellipsize=Pango.EllipsizeMode.END,
        )
        title.add_css_class("hero-title")
        text.append(title)

        where = Gtk.Label(label=self._where(item, page, pages), xalign=0.0)
        where.add_css_class("hero-where")
        where.add_css_class("numeric")
        text.append(where)

        if pages:
            progress = Gtk.ProgressBar(
                fraction=min(1.0, max(0.0, page / pages)), halign=Gtk.Align.START,
            )
            progress.add_css_class("hero-progress")
            text.append(progress)

        action = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                         halign=Gtk.Align.START)
        action.add_css_class("hero-action")
        action.append(Gtk.Label(label="Devam et"))
        action.append(Gtk.Image.new_from_icon_name(icons.NEXT_PAGE))
        text.append(action)

        row.append(text)
        button.set_child(row)
        return button

    @staticmethod
    def _where(item: BookItem, page: int, pages: int) -> str:
        parts = [part for part in (item.grade_label, page_text(page, pages)) if part]
        return " · ".join(parts)

    # -- the ones before it -----------------------------------------------

    def _recents(self, entries: List[Entry]) -> Gtk.Widget:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         valign=Gtk.Align.START)
        heading = Gtk.Label(label="SON OKUNANLAR", xalign=0.0)
        heading.add_css_class("eyebrow")
        heading.add_css_class("eyebrow-muted")
        column.append(heading)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for item, page, pages in entries:
            row.append(self._recent(item, page, pages))
        column.append(row)
        return column

    def _recent(self, item: BookItem, page: int, pages: int) -> Gtk.Widget:
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("recent-card")
        button.connect("clicked", lambda _b: self.emit("open-book", item))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.append(_Cover(self.covers, item, RECENT_COVER_WIDTH))
        where = Gtk.Label(label=page_text(page, pages), xalign=0.0)
        where.add_css_class("caption")
        where.add_css_class("dim-label")
        where.add_css_class("numeric")
        body.append(where)
        button.set_child(body)
        return button


def resolve_entries(recent: list, lookup, openable) -> List[Entry]:
    """
    Turn the remembered list into books that can still be opened.

    `recent` is `Settings.recent_books()`, `lookup` maps a book id to its
    `BookItem` or None, and `openable` says whether a book is on this machine.
    """
    entries: List[Entry] = []
    for record in recent:
        item: Optional[BookItem] = lookup(record.get("id"))
        if item is None or not openable(item):
            continue
        entries.append((item, int(record.get("page") or 1), int(record.get("pages") or 0)))
    return entries
