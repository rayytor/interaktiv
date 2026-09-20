"""
A byte budget for rendered pages.

Counting entries instead of bytes would be the wrong unit here: one fit-page
spread of a school book is about 5 MB of texture and one deep custom zoom is
eight times that, so "keep the last twenty" means anything between 100 MB and
1 GB depending on what the teacher did last. The budget is in bytes and the
eviction is plain LRU.

The textures are `Gdk.Texture`; holding one keeps its pixels alive in the GPU
(or in system memory, on the software renderer that a cheap smartboard will
fall back to), which is why the cache is cleared outright on a mode change and
on leaving the reader rather than being left to age out.
"""

import os
from collections import OrderedDict
from typing import Any, Optional, Tuple

# 64 MB: about twenty-four fit-page sheets of a school book at a board's usual
# scale factor, or six on a HiDPI one. The live set in book mode is four of them
# -- two visible, two prefetched -- so the rest is history to page back through.
#
# The budget is deliberately smaller than it first looks like it could be,
# because a texture costs its bytes twice: once in the `GBytes` the
# `Gdk.MemoryTexture` holds, and again in whatever GSK uploaded it to. Measured
# paging 290 pages of the 443 MB chemistry book, a 96 MB budget left the reader
# holding 91 MB of textures and 457 MB resident; capping at 48 MB took the same
# run to 367 MB and cost exactly one cache hit out of 108, because paging
# forward never reads that far back. 64 MB sits between the two.
#
# Nothing depends on the budget for correctness: the cache never evicts the
# entry just inserted, so even a single texture larger than the whole budget
# still displays.
DEFAULT_BUDGET = 64 * 1024 * 1024


def _budget_from_env() -> int:
    raw = os.environ.get("INTERAKTIV_TEXTURE_BUDGET")
    if not raw:
        return DEFAULT_BUDGET
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_BUDGET
    return max(16 * 1024 * 1024, value)


class TextureCache:
    def __init__(self, budget: Optional[int] = None):
        self.budget = budget if budget is not None else _budget_from_env()
        self._entries: "OrderedDict[Tuple, Tuple[Any, int]]" = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    # -- reading ----------------------------------------------------------

    def get(self, key: Tuple) -> Optional[Any]:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return entry[0]

    def __contains__(self, key: Tuple) -> bool:
        return key in self._entries

    # -- writing ----------------------------------------------------------

    def put(self, key: Tuple, texture: Any, nbytes: int) -> None:
        old = self._entries.pop(key, None)
        if old is not None:
            self._bytes -= old[1]
        self._entries[key] = (texture, nbytes)
        self._bytes += nbytes
        self._evict()

    def _evict(self) -> None:
        # Never evict the entry just inserted, however large it is: a single
        # very deep zoom that exceeds the whole budget should still display.
        while self._bytes > self.budget and len(self._entries) > 1:
            _key, (_texture, nbytes) = self._entries.popitem(last=False)
            self._bytes -= nbytes

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    def drop_pages(self, keep: set) -> None:
        """Keep only the given page numbers -- used when a mode change makes
        every other page's texture unreachable but the visible one current."""
        for key in [k for k in self._entries if k[0] not in keep]:
            _texture, nbytes = self._entries.pop(key)
            self._bytes -= nbytes

    # -- reporting --------------------------------------------------------

    @property
    def nbytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._entries)
