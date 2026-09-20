"""
Reading a baked book.

`tools/hotspot_extraction/scan.py` writes one `regions.json` per book and this
reads it back. The schema's authority is
`tools/hotspot_extraction/scanner/serializer.py`; this module deliberately
mirrors it rather than sharing code with it, so the reader never has to import
the scanner package (and, through it, pymupdf, psutil and the whole detector).

Two things are load-bearing and easy to get wrong:

  * every rect is in **PDF user space, y-up, origin bottom-left**. Nothing here
    converts; `interaktiv_core.geometry` does, once.
  * `pages` is **sparse** -- only sheets that carry activities appear, keyed by
    the 1-based page number as a string. A 289-page book typically has 93 keys.
    Pages are therefore materialised on demand and held behind a small cache,
    because the raw file runs to several hundred kilobytes.
"""

from collections import OrderedDict
from dataclasses import dataclass, field
import json
import os
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# The bake format this reader understands. `scan.py` writes the same number; a
# bake from an older detector is ignored rather than trusted.
BAKE_VERSION = 2
_ACCEPTED_VERSIONS = (1, BAKE_VERSION)

# How many materialised pages to keep. Book mode needs two, scroll mode a
# handful, and stepping through activities walks forward one at a time.
_PAGE_CACHE_SIZE = 16

Rect = Tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Item:
    """One numbered question inside an activity."""

    id: str
    label: Optional[str]
    number: Optional[int]
    part_index: int
    rect: Rect
    text: str = ""


@dataclass(frozen=True, slots=True)
class Activity:
    """
    One activity on one sheet.

    `parts` are the pieces of a region that flows across a column break. They
    are kept separate and never unioned: each stays inside its own column
    instead of a single bounding box covering -- and stealing clicks from -- its
    neighbours. `rect` is the region's extent and is only used when `parts` is
    empty.
    """

    id: str
    page_num: int
    label: Optional[str]
    column: int
    rect: Rect
    parts: Tuple[Rect, ...]
    headline: str = ""
    anchored: bool = False
    oge_id: Optional[str] = None
    items: Tuple[Item, ...] = ()

    @property
    def piece_rects(self) -> Tuple[Rect, ...]:
        return self.parts if self.parts else (self.rect,)

    def items_in_part(self, part_index: int) -> List[Item]:
        return [q for q in self.items if q.part_index == part_index]

    def name(self) -> str:
        """
        What to call this in the chrome.

        A lettered region is named by its letter. A region grown from the
        publisher's icon has no letter to be named by -- that is the whole
        reason it was anchored -- so it goes by the headline read off its own
        first line.
        """
        if self.label:
            return f"Activity {self.label}"
        return self.headline or "Activity"


@dataclass(frozen=True, slots=True)
class PageRegions:
    """One sheet's worth of bake."""

    page_num: int
    page_width: float
    page_height: float
    columns: Tuple[Tuple[float, float], ...]
    activities: Tuple[Activity, ...]


def _rect(seq: Sequence[float]) -> Rect:
    return (float(seq[0]), float(seq[1]), float(seq[2]), float(seq[3]))


@dataclass
class RegionsBook:
    """A whole book's bake, with its pages materialised lazily."""

    path: str
    version: int
    book_id: str
    fingerprint: str
    page_count: int
    calibration: Dict[str, Any]
    folio_offset: Optional[int]
    _folio_by_page: Dict[str, int]
    _raw_pages: Dict[str, Any]
    anchors: Dict[str, List[str]] = field(default_factory=dict)
    built_at: str = ""
    _cache: "OrderedDict[int, PageRegions]" = field(
        default_factory=OrderedDict, repr=False
    )

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[str]) -> Optional["RegionsBook"]:
        """
        Read a bake, or return None if there isn't a usable one.

        A missing file is the ordinary "not baked yet" answer, not an error; so
        is a bake written by a detector this reader does not understand.
        """
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        return cls.from_dict(data, path=path)

    @classmethod
    def from_dict(cls, data: Any, path: str = "") -> Optional["RegionsBook"]:
        if not isinstance(data, dict):
            return None
        if data.get("version") not in _ACCEPTED_VERSIONS:
            return None
        pages = data.get("pages")
        if not isinstance(pages, dict):
            return None
        folio = data.get("folio") or {}
        return cls(
            path=path,
            version=int(data["version"]),
            book_id=str(data.get("bookId", "")),
            fingerprint=str(data.get("fingerprint", "")),
            page_count=int(data.get("pageCount") or 0),
            calibration=dict(data.get("calibration") or {}),
            folio_offset=folio.get("offset") if isinstance(folio.get("offset"), int) else None,
            _folio_by_page=dict(folio.get("byPage") or {}),
            _raw_pages=pages,
            anchors=dict(data.get("anchors") or {}),
            built_at=str(data.get("builtAt", "")),
        )

    # -- guards ----------------------------------------------------------

    def matches(self, page_count: int) -> bool:
        """
        Whether this bake describes the file that was opened.

        A different edition under the same catalogue id would not line up: the
        page ordinals differ, so every rect would land on the wrong sheet. The
        web reader makes the same check before trusting a bake.
        """
        return self.page_count == page_count

    def matches_page(self, page_num: int, width: float, height: float, tol: float = 1.0) -> bool:
        """
        Whether one sheet's dimensions agree with what was baked for it.

        The page-count guard catches a wholesale edition mismatch; this catches
        the single sheet with an offset MediaBox, where only that page's
        overlay has to be dropped.
        """
        page = self.page(page_num)
        if page is None:
            return True
        return abs(page.page_width - width) <= tol and abs(page.page_height - height) <= tol

    @property
    def enabled(self) -> bool:
        """True when the bake says this book has activities worth drawing."""
        return bool(self.calibration.get("enabled"))

    @property
    def confidence(self) -> Optional[str]:
        """"strong", "weak", "none", or None when the bake does not say."""
        c = self.calibration.get("confidence")
        return str(c) if c else None

    # -- pages -----------------------------------------------------------

    def has_page(self, page_num: int) -> bool:
        return str(page_num) in self._raw_pages

    def pages_with_activities(self) -> Iterator[int]:
        """Every sheet that carries at least one region, in reading order."""
        for key in sorted(self._raw_pages, key=lambda k: int(k)):
            yield int(key)

    def page(self, page_num: int) -> Optional[PageRegions]:
        """One sheet, materialised on first ask and then cached."""
        cached = self._cache.get(page_num)
        if cached is not None:
            self._cache.move_to_end(page_num)
            return cached
        raw = self._raw_pages.get(str(page_num))
        if raw is None:
            return None
        page = self._materialise(page_num, raw)
        self._cache[page_num] = page
        while len(self._cache) > _PAGE_CACHE_SIZE:
            self._cache.popitem(last=False)
        return page

    def _materialise(self, page_num: int, raw: Dict[str, Any]) -> PageRegions:
        activities: List[Activity] = []
        for a in raw.get("activities", ()):
            rect = _rect(a["rect"])
            parts = tuple(_rect(p) for p in (a.get("parts") or ()))
            items = tuple(
                Item(
                    id=str(q.get("id", "")),
                    label=q.get("label"),
                    number=q.get("number"),
                    part_index=int(q.get("partIndex") or 0),
                    rect=_rect(q["rect"]),
                    text=str(q.get("text") or ""),
                )
                for q in (a.get("items") or ())
            )
            activities.append(
                Activity(
                    id=str(a["id"]),
                    page_num=page_num,
                    label=a.get("label"),
                    column=int(a.get("column") or 0),
                    rect=rect,
                    parts=parts,
                    headline=str(a.get("headline") or ""),
                    anchored=bool(a.get("anchored")),
                    oge_id=(str(a["ogeId"]) if a.get("ogeId") else None),
                    items=items,
                )
            )
        return PageRegions(
            page_num=page_num,
            page_width=float(raw.get("pageWidth") or 0.0),
            page_height=float(raw.get("pageHeight") or 0.0),
            columns=tuple(
                (float(c[0]), float(c[1])) for c in (raw.get("columns") or ())
            ),
            activities=tuple(activities),
        )

    # -- folio -----------------------------------------------------------

    def printed_page(self, page_num: int) -> Optional[int]:
        """
        The number this sheet prints on itself, which is how the publisher's
        manifest indexes its activities.

        The per-page map is preferred because every sheet in the book voted for
        it; the offset is the fallback for a sheet the vote did not cover.
        """
        printed = self._folio_by_page.get(str(page_num))
        if isinstance(printed, int):
            return printed
        if self.folio_offset is not None:
            return page_num - self.folio_offset
        return None

    def drop_cache(self) -> None:
        self._cache.clear()
