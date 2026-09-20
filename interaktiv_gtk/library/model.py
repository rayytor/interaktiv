"""
The catalogue as the library view needs it.

`BooksManager.get_all_books()` returns plain dicts rebuilt on every call. The
view needs objects with a stable identity -- a card bound to a book must keep
pointing at the same book across a reload, so that a download's progress lands
on the card the teacher is watching -- so each record is folded into a
long-lived `BookItem` instead of replacing it.

The filter and grouping rules are a port of `js/dashboard.js:render()`, kept
deliberately literal so that both editions answer a filter the same way.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gi.repository import GObject

# The grade groups a catalogue is shown in, in order. `0` is the publisher's
# catch-all for elective courses.
GRADES = [
    (9, "9. Sınıf"),
    (10, "10. Sınıf"),
    (11, "11. Sınıf"),
    (12, "12. Sınıf"),
    (0, "Seçmeli Dersler"),
]

# The seven filter tabs, in the order `index.html` lists them.
FILTERS = ["all", "installed", "9", "10", "11", "12", "0"]

FILTER_LABELS = {
    "all": "Tümü",
    "installed": "Yüklü",
    "9": "9. Sınıf",
    "10": "10. Sınıf",
    "11": "11. Sınıf",
    "12": "12. Sınıf",
    "0": "Seçmeli",
}

CONFIDENCE_LABELS = {"strong": "Güçlü", "weak": "Zayıf", "none": "Yok"}

# Searching a Turkish catalogue with `lower()` alone does not work, and the web
# dashboard does exactly that. Python and JavaScript both lowercase "İ" to an
# "i" followed by a combining dot above, so typing "BİYOLOJİ" matches nothing;
# and a teacher on a keyboard without the Turkish letters types "cografya" for
# "Coğrafya" and "TARIH" for "Tarih". Folding the dotted and dotless i together
# with the other Turkish diacritics makes all of those find the book. This is a
# deliberate divergence from `dashboard.js`: the rule there is not one worth
# being faithful to.
_FOLD = str.maketrans({
    "İ": "i", "I": "i", "ı": "i", "i": "i", "Î": "i", "î": "i",
    "Ş": "s", "ş": "s",
    "Ğ": "g", "ğ": "g",
    "Ç": "c", "ç": "c",
    "Ö": "o", "ö": "o",
    "Ü": "u", "ü": "u", "Û": "u", "û": "u",
    "Â": "a", "â": "a",
})


def fold(text: str) -> str:
    """Lowercase for searching, with Turkish diacritics folded away."""
    return (text or "").translate(_FOLD).lower()

EMPTY_NO_INSTALLS = (
    "Henüz indirilmiş kitap bulunmuyor. Kitapları çevrimdışı okumak için "
    "“İndir” düğmesini kullanabilirsiniz."
)
EMPTY_NO_MATCHES = "Arama kriterlerine uygun kitap bulunamadı."


class BookItem(GObject.Object):
    """One catalogue entry, with a lifetime longer than one `get_all_books()`."""

    __gtype_name__ = "InteraktivBookItem"
    __gsignals__ = {
        # The record behind this item changed: install state, file size, or an
        # in-flight download's progress.
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, record: Dict[str, Any]):
        super().__init__()
        self.id = record["id"]
        self._record: Dict[str, Any] = {}
        self.absorb(record)

    def absorb(self, record: Dict[str, Any]) -> bool:
        """Take a fresh record; report whether anything the UI shows moved."""
        changed = record != self._record
        self._record = dict(record)
        if changed:
            self.emit("changed")
        return changed

    # -- the fields a card draws ------------------------------------------

    @property
    def title(self) -> str:
        return self._record.get("title") or self.id

    @property
    def grade(self) -> int:
        return self._record.get("grade") or 0

    @property
    def grade_label(self) -> str:
        return self._record.get("gradeLabel") or ""

    @property
    def subject(self) -> Optional[str]:
        return self._record.get("subject")

    @property
    def is_installed(self) -> bool:
        return bool(self._record.get("isInstalled"))

    @property
    def file_size(self) -> Optional[int]:
        return self._record.get("fileSize")

    @property
    def confidence(self) -> Optional[str]:
        return self._record.get("confidence")

    @property
    def has_thumbnail(self) -> bool:
        return bool(self._record.get("hasThumbnail"))

    @property
    def download(self) -> Optional[Dict[str, Any]]:
        return self._record.get("download")

    @property
    def is_downloading(self) -> bool:
        info = self.download
        return bool(info and info.get("status") == "downloading")

    @property
    def download_kind(self) -> str:
        return (self.download or {}).get("kind") or "install"

    @property
    def progress(self) -> float:
        """0.0 - 1.0, for a `Gtk.ProgressBar` fraction."""
        info = self.download or {}
        return max(0.0, min(1.0, float(info.get("progress") or 0) / 100.0))

    def set_download(self, info: Optional[Dict[str, Any]]) -> None:
        if self._record.get("download") == info:
            return
        self._record["download"] = info
        self.emit("changed")

    # -- derived ----------------------------------------------------------

    @property
    def meta_text(self) -> str:
        """`gradeLabel` plus the file size, as `createBookCardHTML` composes it."""
        text = self.grade_label
        if self.is_installed and self.file_size:
            text += f" • {self.file_size / (1024 * 1024):.0f} MB"
        return text

    @property
    def confidence_label(self) -> Optional[str]:
        if not self.confidence:
            return None
        return CONFIDENCE_LABELS.get(self.confidence, self.confidence)

    def matches(self, query: str) -> bool:
        """
        `dashboard.js` searches the title and the grade label, nothing else.

        `query` is expected already folded by `fold()`; the haystack is folded
        here so that a search survives Turkish casing.
        """
        if not query:
            return True
        return query in fold(self.title) or query in fold(self.grade_label)


@dataclass
class Section:
    """One headed group of books, as the catalogue is laid out."""

    key: str
    title: str
    items: List[BookItem] = field(default_factory=list)

    @property
    def count_text(self) -> str:
        return f"{len(self.items)} kitap"


class LibraryModel:
    """The catalogue, filtered the way the dashboard filters it."""

    def __init__(self, manager):
        self.manager = manager
        self.items: List[BookItem] = []
        self._by_id: Dict[str, BookItem] = {}

    # -- loading ----------------------------------------------------------

    def reload(self) -> None:
        records = self.manager.get_all_books()
        seen = set()
        items: List[BookItem] = []
        for record in records:
            book_id = record["id"]
            seen.add(book_id)
            item = self._by_id.get(book_id)
            if item is None:
                item = BookItem(record)
                self._by_id[book_id] = item
            else:
                item.absorb(record)
            items.append(item)
        for gone in set(self._by_id) - seen:
            del self._by_id[gone]
        self.items = items

    def apply_download_statuses(self, statuses: Dict[str, Any]) -> None:
        """Push one poll's worth of download progress onto the affected items."""
        for item in self.items:
            item.set_download(statuses.get(item.id))

    def get(self, book_id: str) -> Optional[BookItem]:
        return self._by_id.get(book_id)

    @property
    def installed_count(self) -> int:
        return sum(1 for item in self.items if item.is_installed)

    # -- filtering --------------------------------------------------------

    def sections(self, active_filter: str, query: str) -> List[Section]:
        """
        The groups to draw, in order, for one filter and search box.

        A literal port of `dashboard.js:render` /
        `renderInstalledSection` / `renderCatalogSection`:

          * search narrows first, on title and grade label;
          * `installed` keeps only installed books and shows no grade groups;
          * a grade tab keeps only that grade, installed or not;
          * `all` lifts the installed books into a group of their own at the
            top and groups the rest by grade.
        """
        query = fold((query or "").strip())
        visible = [item for item in self.items if item.matches(query)]

        if active_filter == "installed":
            visible = [item for item in visible if item.is_installed]
        elif active_filter != "all":
            try:
                grade = int(active_filter)
            except ValueError:
                grade = 0
            visible = [item for item in visible if item.grade == grade]

        sections: List[Section] = []

        if active_filter in ("all", "installed"):
            installed = [item for item in visible if item.is_installed]
            if installed:
                sections.append(Section("installed", "Yüklü Kitaplar", installed))

        if active_filter == "installed":
            return sections

        rest = [item for item in visible if not item.is_installed] \
            if active_filter == "all" else visible

        for grade, label in GRADES:
            group = [item for item in rest if item.grade == grade]
            if group:
                sections.append(Section(f"grade-{grade}", label, group))

        return sections

    def empty_message(self, active_filter: str) -> str:
        """What to say when `sections()` came back empty."""
        if active_filter == "installed":
            return EMPTY_NO_INSTALLS
        return EMPTY_NO_MATCHES
