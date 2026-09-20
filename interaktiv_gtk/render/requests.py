"""
What crosses the line between the main loop and the render thread.

Both of these are plain frozen data with no GTK and no MuPDF in them, which is
what makes them safe to hand across: a request is built on the main loop and
read on the render thread, a result the other way round. The only mutable thing
either side shares is the generation counter, and that is an int behind a lock.
"""

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

Rect = Tuple[float, float, float, float]

# The most pixels one render may produce, before the widget's scale factor is
# applied. A page of a school book at fit-page on a 4K board is well under it;
# the cap exists for deep custom zoom and for focus mode's crops, where the
# requested scale is otherwise unbounded. Going over it does not fail -- the
# scale is reduced and the result is painted up, slightly soft, which is what
# the web reader's `dpr -= 0.25` back-off achieved by a different route.
MAX_RENDER_PIXELS = 8_000_000


@dataclass(frozen=True, slots=True)
class DocumentInfo:
    """What the main loop needs to lay a book out without opening it itself."""

    path: str
    page_count: int
    # (width, height) in PDF points, per page, with the document's own /Rotate
    # already applied. A book is not necessarily uniform: the 443 MB biology
    # book mixes 570 pt singles with 1168 pt pre-spread pages, and a layout that
    # assumed page 1's size would place every hotspot on those pages wrongly.
    page_sizes: Sequence[Tuple[float, float]] = ()
    toc: Sequence = ()

    def size(self, page: int) -> Tuple[float, float]:
        """1-based, clamped -- a page number out of range gets page 1's size."""
        if not self.page_sizes:
            return (595.0, 842.0)
        index = min(max(page, 1), len(self.page_sizes)) - 1
        return self.page_sizes[index]


@dataclass(frozen=True, slots=True)
class RenderRequest:
    page: int                      # 1-based
    scale: float                   # device pixels per PDF point
    rotation: int = 0              # the user's rotation, degrees clockwise
    clip: Optional[Rect] = None    # PDF user space; None means the whole page
    lane: int = 0
    generation: int = 0

    @property
    def key(self) -> Tuple:
        """
        What makes two renders the same picture.

        Scale is quantised to three decimals so that a fit-page recomputation
        that lands a hair away from the previous one re-uses the texture instead
        of re-rendering the identical page.
        """
        return (self.page, round(self.scale, 3), self.rotation % 360, self.clip)


@dataclass(frozen=True, slots=True)
class RenderResult:
    request: RenderRequest
    texture: object                # Gdk.Texture, built on the main loop
    width: int                     # device pixels
    height: int
    origin_x: float                # pix.x / pix.y -- non-zero for a crop or a
    origin_y: float                # rotation, and fed straight to PageTransform
    page_w: float                  # the page's own size, for the ±1 pt guard
    page_h: float
    scale: float                   # what was actually rendered at, after the
                                   # MAX_RENDER_PIXELS cap; may be below the
                                   # scale that was asked for
    render_ms: int = 0
    nbytes: int = field(default=0)

    @property
    def key(self) -> Tuple:
        return self.request.key


@dataclass(frozen=True, slots=True)
class SearchRequest:
    needle: str
    pages: Tuple[int, ...]
    lane: int = 3
    generation: int = 0
    token: int = 0

    @property
    def key(self) -> Tuple:
        return ("search", self.needle, self.pages, self.token)


@dataclass(frozen=True, slots=True)
class TextPassRequest:
    pages: Tuple[int, ...]
    lane: int = 3
    generation: int = 0
    token: int = 0

    @property
    def key(self) -> Tuple:
        return ("text-pass", self.pages, self.token)
