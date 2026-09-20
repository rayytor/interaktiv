"""
One book, open.

A session is what the reader is looking at: the file, the process rendering it,
and the things derived from the file that outlive any one page turn -- the bake
(`RegionsBook`), the publisher's interactive index (`OgeIndex`), and the joined
overlays built from the two.

Navigation state -- which page, which mode, which zoom -- deliberately does not
live here. It belongs to the view, because reopening the same book in a second
window (which a board will never do, but the shape should still be honest)
should not share a scroll position.

The bake is read once and held; `regions.json` runs to several hundred
kilobytes and parsing it per page turn would be the most expensive thing the
reader does. Individual sheets are materialised on demand inside `RegionsBook`,
and the joined overlay on top of that is cached for the handful of pages the
reader can actually be looking at.
"""

from collections import OrderedDict
import threading
from typing import Callable, Iterator, List, Optional, Tuple

from ..render import RenderService
from .overlay import PageOverlay, activity_label, build_overlay

# Book mode needs two sheets and prefetch a third; stepping through activities
# walks forward one at a time. Eight is comfortably more than the live set and
# still nothing next to one page's texture.
_OVERLAY_CACHE = 8


def _call_once(fn) -> bool:
    """Run a callback on the main loop exactly once."""
    fn()
    return False


class DocumentSession:
    def __init__(self, book_id: str, title: str, path: str,
                 manager=None, confidence_gate: bool = False):
        self.book_id = book_id
        self.title = title
        self.path = path
        self.manager = manager
        self.confidence_gate = confidence_gate
        self.info = None
        self.service: Optional[RenderService] = None
        self.error: Optional[str] = None

        self.regions = None
        self.oges = None
        self.activities_ready = False
        self.bake_state = "none"   # none | stale | baking | ready | mismatch
        self._overlays: "OrderedDict[int, Optional[PageOverlay]]" = OrderedDict()

    # -- lifecycle --------------------------------------------------------

    def open(
        self,
        on_ready: Callable,
        on_result: Callable,
        on_error: Optional[Callable] = None,
        on_pressure: Optional[Callable] = None,
    ) -> None:
        self.load_activities()

        def ready(info):
            self.info = info
            self.validate_activities(info.page_count)
            on_ready(info)

        def failed(message):
            self.error = message
            if on_error is not None:
                on_error(message)

        self.service = RenderService(
            self.path,
            on_ready=ready,
            on_result=on_result,
            on_error=failed,
            on_pressure=on_pressure,
        )
        self.service.start()

    def close(self) -> None:
        if self.service is not None:
            self.service.close()
            self.service = None
        self._overlays.clear()
        if self.regions is not None:
            self.regions.drop_cache()

    # -- activities -------------------------------------------------------

    def load_activities(self) -> None:
        """
        Read the bake and the publisher's manifest.

        Both are ordinary "may not be there" facts rather than errors: a book
        nobody has baked yet, or one the publisher shipped without interactive
        entries, opens as a plain PDF.
        """
        from interaktiv_core.oges import OgeIndex
        from interaktiv_core.regions import RegionsBook

        if self.manager is None:
            self.oges = OgeIndex()
            return
        try:
            self.regions = RegionsBook.load(self.manager.get_regions_path(self.book_id))
        except Exception:
            self.regions = None
        try:
            self.oges = OgeIndex.from_kitapoge_list(
                self.manager.get_book_oges(self.book_id),
                is_installed=self.manager.is_activity_installed,
            )
        except Exception:
            self.oges = OgeIndex()

    def validate_activities(self, page_count: int) -> None:
        """
        Decide whether this bake describes the file that was actually opened.

        A bake made for a different edition under the same catalogue id would
        line up on nothing: the page ordinals differ, so every rect would land
        on the wrong sheet. Drawing it would be worse than drawing nothing, so
        the whole overlay goes rather than being drawn slightly wrong.
        """
        book = self.regions
        if book is None:
            self.bake_state = "none"
        elif not book.matches(page_count):
            self.bake_state = "mismatch"
        elif not book.enabled:
            self.bake_state = "ready"
        else:
            self.bake_state = "ready"
        self.activities_ready = bool(
            book is not None and book.enabled and book.matches(page_count)
        )
        self._overlays.clear()

    @property
    def confidence(self) -> Optional[str]:
        """The calibration confidence, or None when the gate is switched off."""
        if not self.confidence_gate or self.regions is None:
            return None
        return self.regions.confidence

    def overlay(self, page_num: int) -> Optional[PageOverlay]:
        """
        The joined overlay for one sheet, or None when there is nothing to draw.

        The per-sheet dimension check is the page-level analogue of the
        page-count guard: one sheet with an offset MediaBox loses its overlay
        and the rest of the book keeps its own.
        """
        if not self.activities_ready or self.info is None:
            return None
        if page_num in self._overlays:
            self._overlays.move_to_end(page_num)
            return self._overlays[page_num]

        built = self._build_overlay(page_num)
        self._overlays[page_num] = built
        while len(self._overlays) > _OVERLAY_CACHE:
            self._overlays.popitem(last=False)
        return built

    def _build_overlay(self, page_num: int) -> Optional[PageOverlay]:
        page = self.regions.page(page_num)
        oges = self.oges.for_printed_page(self.regions.printed_page(page_num))
        if page is None:
            # No regions on this sheet, but the publisher may still have hung an
            # activity on it -- those are drawn as pins and must not be lost
            # just because nothing lettered was detected here.
            if not oges:
                return None
            from interaktiv_core.regions import PageRegions

            width, height = self.info.size(page_num)
            page = PageRegions(
                page_num=page_num, page_width=width, page_height=height,
                columns=(), activities=(),
            )
        else:
            width, height = self.info.size(page_num)
            if not self.regions.matches_page(page_num, width, height):
                return None
        overlay = build_overlay(
            page, oges,
            confidence=self.confidence,
            is_installed=self.manager.is_activity_installed if self.manager else None,
        )
        return None if overlay.is_empty else overlay

    # -- freshness --------------------------------------------------------

    def check_bake(self, on_change: Optional[Callable] = None) -> None:
        """
        Re-bake in the background when the bake does not describe this file.

        The fingerprint comparison reads three 256 KB slices of the PDF, and the
        bake itself is a `scan.py` child process on one book -- never a sweep,
        never in this process. Both go on a worker thread so opening a book is
        not held up by a disk that has to spin up first.

        `on_change()` is called on the main loop when the bake has been replaced
        and the overlays need rebuilding.
        """
        if self.manager is None or self.bake_state == "baking":
            return

        def _work():
            from gi.repository import GLib

            from interaktiv_core import jobs

            try:
                stale = jobs.needs_bake(self.manager, self.book_id, self.regions)
            except Exception:
                stale = False
            if not stale:
                return
            self.bake_state = "baking"
            if on_change is not None:
                GLib.idle_add(_call_once, on_change)

            def done(_book_id, ok):
                GLib.idle_add(self._bake_landed, ok, on_change)

            if not jobs.trigger_bake(self.manager, self.book_id, on_done=done):
                self.bake_state = "stale"

        threading.Thread(target=_work, daemon=True).start()

    def _bake_landed(self, ok: bool, on_change: Optional[Callable]) -> bool:
        from gi.repository import GLib

        if ok:
            self.load_activities()
            if self.info is not None:
                self.validate_activities(self.info.page_count)
        else:
            self.bake_state = "stale"
        if on_change is not None:
            on_change()
        return GLib.SOURCE_REMOVE

    def drop_overlays(self) -> None:
        self._overlays.clear()
        if self.regions is not None:
            self.regions.drop_cache()

    # -- the book's activities, for the sidebar ---------------------------

    def activity_summaries(self) -> Iterator[Tuple[int, int, str, int, bool]]:
        """
        Every activity in the book as `(page, act_index, name, items, live)`.

        One pass over the sheets that carry regions -- 93 of 289 in the sampled
        book -- building each overlay and keeping only the line the sidebar
        shows. The overlays themselves are not retained: the list is a few
        hundred short tuples, where the pages behind it are megabytes.
        """
        if not self.activities_ready:
            return
        for page_num in self.regions.pages_with_activities():
            overlay = self._build_overlay(page_num)
            if overlay is None:
                continue
            for act_index, activity in enumerate(overlay.activities):
                spot = overlay.first_spot_of(act_index)
                if spot is None:
                    continue
                items = len(activity.items)
                name = activity_label(activity) or activity.name()
                yield (page_num, act_index, name, items, spot.interactive)
        self.regions.drop_cache()

    def pages_with_activities(self) -> List[int]:
        if not self.activities_ready:
            return []
        return list(self.regions.pages_with_activities())

    # -- shape ------------------------------------------------------------

    @property
    def page_count(self) -> int:
        return self.info.page_count if self.info else 0

    @property
    def is_open(self) -> bool:
        return self.info is not None
