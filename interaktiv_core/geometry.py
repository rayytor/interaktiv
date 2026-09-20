"""
The one place PDF rectangles become widget coordinates.

Three coordinate systems meet in the reader and it is worth naming them, because
mixing two of them silently puts a hotspot on the wrong half of the sheet:

  * **PDF user space** -- what a bake stores. Origin bottom-left, y grows upward.
    Every rect in `regions.json` is in it.
  * **MuPDF page space** -- what `pymupdf` clips and renders in. Origin top-left,
    y grows downward. Related to the first by `v = page_height - y`.
  * **Device space** -- page space run through the render matrix
    (`Matrix(s, s).prerotate(rot)`). Rotation moves the origin off zero, which is
    why a rendered pixmap reports a non-zero `pix.x` / `pix.y`.

Widget coordinates are device coordinates with that origin subtracted, so a
clipped render -- which is how focus mode keeps its canvas bounded -- and a full
page are handled by exactly the same arithmetic. `origin` is read straight off
the pixmap rather than derived, so there is nothing to keep in step with MuPDF.
"""

from dataclasses import dataclass
from typing import Sequence, Tuple

Rect = Tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class PageTransform:
    """
    One page, at one scale and rotation, with one crop origin.

    `scale` is device pixels per PDF point and already carries the widget's
    scale factor. `rotation` is the user's rotation (the R button), not the
    document's own /Rotate, which `page.rect` has already applied.
    """

    page_w: float
    page_h: float
    scale: float
    rotation: int = 0
    origin_x: float = 0.0
    origin_y: float = 0.0

    # -- construction ----------------------------------------------------

    @classmethod
    def for_full_page(
        cls, page_w: float, page_h: float, scale: float, rotation: int = 0
    ) -> "PageTransform":
        """
        A transform for an uncropped render, with the origin derived rather
        than read off a pixmap -- for laying out a page before it has been
        rendered.
        """
        ox, oy = cls._full_page_origin(page_w, page_h, scale, rotation)
        return cls(page_w, page_h, scale, rotation % 360, ox, oy)

    @staticmethod
    def _full_page_origin(
        page_w: float, page_h: float, scale: float, rotation: int
    ) -> Tuple[float, float]:
        r = rotation % 360
        if r == 90:
            return (-scale * page_h, 0.0)
        if r == 180:
            return (-scale * page_w, -scale * page_h)
        if r == 270:
            return (0.0, -scale * page_w)
        return (0.0, 0.0)

    def with_origin(self, origin_x: float, origin_y: float) -> "PageTransform":
        """The same transform, re-anchored on a rendered pixmap's `pix.x/pix.y`."""
        return PageTransform(
            self.page_w, self.page_h, self.scale, self.rotation, origin_x, origin_y
        )

    def with_scale(self, scale: float) -> "PageTransform":
        return PageTransform.for_full_page(
            self.page_w, self.page_h, scale, self.rotation
        )

    # -- size ------------------------------------------------------------

    @property
    def widget_size(self) -> Tuple[float, float]:
        """The device size of the whole page at this scale and rotation."""
        if self.rotation % 180 == 90:
            return (self.page_h * self.scale, self.page_w * self.scale)
        return (self.page_w * self.scale, self.page_h * self.scale)

    # -- forward ---------------------------------------------------------

    def _device(self, u: float, v: float) -> Tuple[float, float]:
        """Page space (y-down) through the render matrix, origin not removed."""
        s = self.scale
        r = self.rotation % 360
        if r == 90:
            return (-s * v, s * u)
        if r == 180:
            return (-s * u, -s * v)
        if r == 270:
            return (s * v, -s * u)
        return (s * u, s * v)

    def point_to_widget(self, x: float, y: float) -> Tuple[float, float]:
        """A point in PDF user space (y-up) to widget pixels."""
        dx, dy = self._device(x, self.page_h - y)
        return (dx - self.origin_x, dy - self.origin_y)

    def rect_to_widget(self, rect: Sequence[float]) -> Rect:
        """
        A rect in PDF user space to a widget rect, normalised.

        Both corners are transformed and then min/max'd, because a rotation
        swaps which corner is which -- the same normalisation the web reader's
        `rectToViewport` does after `convertToViewportRectangle`.
        """
        ax, ay = self.point_to_widget(rect[0], rect[1])
        bx, by = self.point_to_widget(rect[2], rect[3])
        left, right = (ax, bx) if ax <= bx else (bx, ax)
        top, bottom = (ay, by) if ay <= by else (by, ay)
        return (left, top, right - left, bottom - top)

    # -- inverse ---------------------------------------------------------

    def point_to_pdf(self, wx: float, wy: float) -> Tuple[float, float]:
        """Widget pixels back to a point in PDF user space, for hit testing."""
        dx = wx + self.origin_x
        dy = wy + self.origin_y
        s = self.scale
        r = self.rotation % 360
        if r == 90:
            u, v = dy / s, -dx / s
        elif r == 180:
            u, v = -dx / s, -dy / s
        elif r == 270:
            u, v = -dy / s, dx / s
        else:
            u, v = dx / s, dy / s
        return (u, self.page_h - v)

    # -- pymupdf ---------------------------------------------------------

    def rect_to_clip(self, rect: Sequence[float]) -> Rect:
        """
        A rect in PDF user space to a `pymupdf` clip in page space (y-down).

        This is the focus-mode crop: rendering with it bounds the pixmap by the
        region rather than by the page, so canvas memory stays flat however deep
        the zoom goes.
        """
        return (
            rect[0],
            self.page_h - rect[3],
            rect[2],
            self.page_h - rect[1],
        )

    def pymupdf_matrix(self):
        """The render matrix this transform describes. Imports pymupdf lazily."""
        import pymupdf

        m = pymupdf.Matrix(self.scale, self.scale)
        if self.rotation % 360:
            m = m.prerotate(self.rotation % 360)
        return m
