"""
Search engine and controller for the reader.

Supports:
  1. Background text pass (chunked to <= 8 pages per queue item, running at
     LANE_SCAN priority), caching page texts for instant filtering.
  2. PDF search_for() rect retrieval, prioritizing currently visible pages at
     LANE_VISIBLE (0) and remaining pages in LANE_SCAN (3).
  3. Match navigation (next, prev, jump_to) with active match tracking and
     signals for canvas highlights and UI status.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from gi.repository import GLib, GObject

from ..render.service import LANE_SCAN, LANE_VISIBLE

CHUNK_SIZE = 8


@dataclass(frozen=True, slots=True)
class SearchMatch:
    page: int
    match_index: int
    rect: Tuple[float, float, float, float]
    global_index: int = 0


class SearchController(GObject.Object):
    __gtype_name__ = "InteraktivSearchController"

    __gsignals__ = {
        "results-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "active-match-changed": (GObject.SignalFlags.RUN_FIRST, None, (int, object)),
        "search-cleared": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self.service = None
        self.page_count = 0
        self.query = ""
        self._page_texts: Dict[int, str] = {}
        self._text_pass_complete = False
        self._search_token = 0
        self._matches: List[SearchMatch] = []
        self._page_matches: Dict[int, List[SearchMatch]] = {}
        self._current_index = -1
        self._pending_chunks = 0

    def attach(self, service, info) -> None:
        self.service = service
        self.page_count = info.page_count
        self.clear()
        self._page_texts.clear()
        self._text_pass_complete = False
        self.start_background_text_pass()

    def shutdown(self) -> None:
        self.clear()
        self.service = None
        self._page_texts.clear()

    # -- background text pass ---------------------------------------------

    def start_background_text_pass(self) -> None:
        """Queue text extraction for all pages in chunks of <= 8 pages in LANE_SCAN."""
        if self.service is None or self.page_count <= 0:
            return
        pages = list(range(1, self.page_count + 1))
        for i in range(0, len(pages), CHUNK_SIZE):
            chunk = pages[i : i + CHUNK_SIZE]
            self.service.submit_text_pass(
                chunk,
                self._on_text_chunk,
                lane=LANE_SCAN,
            )

    def _on_text_chunk(self, texts: Dict[int, str]) -> None:
        if not texts:
            return
        for k, v in texts.items():
            self._page_texts[int(k)] = v
        if len(self._page_texts) >= self.page_count:
            self._text_pass_complete = True

    # -- search execution -------------------------------------------------

    def execute_search(
        self, query: str, visible_pages: Sequence[int] = ()
    ) -> None:
        query = (query or "").strip()
        if not query:
            self.clear()
            return

        self.query = query
        self._search_token += 1
        token = self._search_token
        self._matches.clear()
        self._page_matches.clear()
        self._current_index = -1
        self._pending_chunks = 0
        self.emit("results-changed", 0)

        if self.service is None or self.page_count <= 0:
            return

        needle = query.lower()

        # If text pass is complete, filter only pages containing the needle
        if self._text_pass_complete:
            pages_to_search = [
                p
                for p in range(1, self.page_count + 1)
                if needle in self._page_texts.get(p, "").lower()
            ]
        else:
            pages_to_search = list(range(1, self.page_count + 1))

        if not pages_to_search:
            self.emit("results-changed", 0)
            return

        # Prioritize visible pages if they need to be searched
        visible_set = set(visible_pages)
        vis_pages = [p for p in pages_to_search if p in visible_set]
        other_pages = [p for p in pages_to_search if p not in visible_set]

        # Submit visible pages at LANE_VISIBLE (0) for instant feedback
        if vis_pages:
            self._pending_chunks += 1
            self.service.submit_search(
                query,
                vis_pages,
                lambda res, tok=token: self._on_search_chunk(tok, res),
                lane=LANE_VISIBLE,
            )

        # Submit remaining pages in chunks of <= 8 at LANE_SCAN (3)
        for i in range(0, len(other_pages), CHUNK_SIZE):
            chunk = other_pages[i : i + CHUNK_SIZE]
            self._pending_chunks += 1
            self.service.submit_search(
                query,
                chunk,
                lambda res, tok=token: self._on_search_chunk(tok, res),
                lane=LANE_SCAN,
            )

    def _on_search_chunk(self, token: int, results: Dict) -> None:
        if token != self._search_token:
            return

        self._pending_chunks = max(0, self._pending_chunks - 1)

        new_matches_found = False
        for page_key, rects in results.items():
            page_num = int(page_key)
            if not rects:
                continue
            new_matches_found = True
            page_list = []
            for idx, r in enumerate(rects):
                page_list.append(
                    SearchMatch(
                        page=page_num,
                        match_index=idx,
                        rect=tuple(r),
                        global_index=0,
                    )
                )
            self._page_matches[page_num] = page_list

        if new_matches_found or self._pending_chunks == 0:
            self._rebuild_global_matches()

    def _rebuild_global_matches(self) -> None:
        flat = []
        for p in sorted(self._page_matches.keys()):
            # Reading order: top to bottom, then left to right. The rects are
            # in PDF user space, where y grows upward, so the top of the page
            # is the largest y and the sort on it is descending.
            matches = sorted(
                self._page_matches[p], key=lambda m: (-m.rect[3], m.rect[0])
            )
            for local_idx, m in enumerate(matches):
                flat.append(
                    SearchMatch(
                        page=m.page,
                        match_index=local_idx,
                        rect=m.rect,
                        global_index=len(flat),
                    )
                )
        self._matches = flat

        # Update page_matches with updated global indices
        self._page_matches.clear()
        for m in self._matches:
            self._page_matches.setdefault(m.page, []).append(m)

        count = len(self._matches)
        self.emit("results-changed", count)

        if count > 0 and self._current_index == -1:
            self.jump_to_match(0)
        elif self._current_index >= count:
            self.jump_to_match(max(0, count - 1))

    # -- match navigation -------------------------------------------------

    def next_match(self) -> Optional[SearchMatch]:
        if not self._matches:
            return None
        idx = (self._current_index + 1) % len(self._matches)
        return self.jump_to_match(idx)

    def prev_match(self) -> Optional[SearchMatch]:
        if not self._matches:
            return None
        idx = (self._current_index - 1 + len(self._matches)) % len(self._matches)
        return self.jump_to_match(idx)

    def jump_to_match(self, index: int) -> Optional[SearchMatch]:
        if not self._matches or index < 0 or index >= len(self._matches):
            return None
        self._current_index = index
        match = self._matches[index]
        self.emit("active-match-changed", index, match)
        return match

    def clear(self) -> None:
        self.query = ""
        self._search_token += 1
        self._matches.clear()
        self._page_matches.clear()
        self._current_index = -1
        self._pending_chunks = 0
        self.emit("search-cleared")

    # -- accessors --------------------------------------------------------

    @property
    def total_matches(self) -> int:
        return len(self._matches)

    @property
    def current_match_index(self) -> int:
        return self._current_index

    @property
    def current_match(self) -> Optional[SearchMatch]:
        if 0 <= self._current_index < len(self._matches):
            return self._matches[self._current_index]
        return None

    def matches_for_page(self, page: int) -> List[Tuple[float, float, float, float]]:
        matches = self._page_matches.get(page, [])
        return [m.rect for m in matches]

    def active_match_for_page(self, page: int) -> Optional[int]:
        current = self.current_match
        if current is not None and current.page == page:
            return current.match_index
        return None

    def all_page_matches(self) -> Dict[int, List[Tuple[float, float, float, float]]]:
        return {
            p: [m.rect for m in matches] for p, matches in self._page_matches.items()
        }
