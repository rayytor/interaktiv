"""
The publisher's interactive activities, indexed the way a sheet can ask for them.

An entry in `kitapogeList` says which activity it is (`data`, a GUID naming an
HTML bundle), what to call it (`baslik`), and where its icon hangs (`posx`/`posy`,
as a percentage of the sheet). What it does *not* say is which sheet of the file
it is on: `sayfano` is the number the page **prints on itself**, and the two
differ by however much front matter the book carries. Translating between them is
the bake's folio map, not this module's business -- callers index by printed page.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence


@dataclass(frozen=True, slots=True)
class Oge:
    """One interactive entry from the publisher's manifest."""

    id: str
    guid: str            # `data` -- names the activity bundle on disk / the CDN
    title: str           # `baslik`
    printed_page: int    # `sayfano`
    posx: float          # % of sheet width
    posy: float          # % of sheet height
    is_installed: bool = False

    @property
    def has_position(self) -> bool:
        return not (self.posx == 0.0 and self.posy == 0.0)

    def name(self) -> str:
        return self.title or f"Page {self.printed_page} activity"


class OgeIndex:
    """Every placed interactive entry in a book, grouped by printed page."""

    __slots__ = ("_by_printed_page", "_by_id")

    def __init__(self, oges: Iterable[Oge] = ()) -> None:
        self._by_printed_page: Dict[int, List[Oge]] = {}
        self._by_id: Dict[str, Oge] = {}
        for oge in oges:
            self._by_printed_page.setdefault(oge.printed_page, []).append(oge)
            self._by_id[oge.id] = oge

    @classmethod
    def from_kitapoge_list(
        cls,
        entries: Sequence[Dict[str, Any]],
        is_installed: Optional[Callable[[str], bool]] = None,
    ) -> "OgeIndex":
        """
        Build an index from a raw `kitapogeList`.

        Two filters, both the publisher's own: `ogeturu == 1` is an interactive
        app rather than a document, and `sayfaustuoge` is the flag for an entry
        that sits on a page. The ones without it carry `sayfano` 0 and position
        0,0 -- they are book-level activities belonging to no sheet, and indexing
        them by `sayfano` would pin them all to the top-left corner of sheet 1.
        """
        oges: List[Oge] = []
        for e in entries or ():
            if e.get("ogeturu") != 1 or not e.get("data"):
                continue
            if not e.get("sayfaustuoge") or not e.get("sayfano"):
                continue
            guid = str(e["data"])
            oges.append(
                Oge(
                    id=str(e.get("id", "")),
                    guid=guid,
                    title=str(e.get("baslik") or ""),
                    printed_page=int(e["sayfano"]),
                    posx=float(e.get("posx") or 0.0),
                    posy=float(e.get("posy") or 0.0),
                    is_installed=bool(is_installed(guid)) if is_installed else False,
                )
            )
        return cls(oges)

    def for_printed_page(self, printed_page: Optional[int]) -> List[Oge]:
        if printed_page is None:
            return []
        return list(self._by_printed_page.get(printed_page, ()))

    def by_id(self, oge_id: str) -> Optional[Oge]:
        return self._by_id.get(str(oge_id))

    def __len__(self) -> int:
        return len(self._by_id)

    def __bool__(self) -> bool:
        return bool(self._by_id)
