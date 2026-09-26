"""
One open PDF, owned by one thread.

Nothing in this module may be called from the main loop. `DocumentHandle` is
constructed on the render thread, every method runs there, and it is closed
there; the only things that leave are the plain values in `requests.py`.

The page sizes are read once at open. That costs about 9 ms for a 289-page book
-- `page.rect` loads the page object but not its content stream -- and it buys
the main loop a complete layout without a single PyMuPDF call.
"""

import math
import time
from typing import Sequence, Tuple

from interaktiv_core.geometry import PageTransform

from .requests import MAX_RENDER_PIXELS, DocumentInfo, RenderRequest


class DocumentHandle:
    def __init__(self, path: str):
        import pymupdf

        self._pymupdf = pymupdf
        self.path = path
        self.doc = pymupdf.open(path)
        self.info = DocumentInfo(
            path=path,
            page_count=self.doc.page_count,
            page_sizes=self._read_page_sizes(),
            toc=self.toc(),
        )

    def _read_page_sizes(self) -> Sequence[Tuple[float, float]]:
        sizes = []
        for index in range(self.doc.page_count):
            rect = self.doc[index].rect
            sizes.append((float(rect.width), float(rect.height)))
        return tuple(sizes)

    # -- rendering --------------------------------------------------------

    def render(self, request: RenderRequest):
        """
        Returns `(bytes_, width, height, stride, origin_x, origin_y, page_w,
        page_h, scale, ms)` -- everything the main loop needs to wrap a texture,
        and no MuPDF object at all. The pixmap is released before returning so
        that its 5 MB is not held while the result waits in the idle queue.
        """
        page = self.doc[request.page - 1]
        rect = page.rect
        page_w, page_h = float(rect.width), float(rect.height)

        scale = self._capped_scale(request, page_w, page_h)
        transform = PageTransform.for_full_page(page_w, page_h, scale, request.rotation)
        clip = transform.rect_to_clip(request.clip) if request.clip else None

        started = time.perf_counter()
        pix = page.get_pixmap(
            matrix=transform.pymupdf_matrix(), clip=clip, alpha=False
        )
        # `pix.samples` is a copy into a Python bytes object and takes a few
        # milliseconds for a full page; `samples_mv` looks cheaper but PyGObject
        # walks a memoryview element by element and takes eighty.
        data = pix.samples
        result = (
            data,
            pix.width,
            pix.height,
            pix.stride,
            float(pix.x),
            float(pix.y),
            page_w,
            page_h,
            scale,
            int((time.perf_counter() - started) * 1000),
        )
        del pix
        return result

    @staticmethod
    def _capped_scale(request: RenderRequest, page_w: float, page_h: float) -> float:
        """
        The requested scale, lowered if it would produce more than
        `MAX_RENDER_PIXELS`.

        The area measured is the clip's, not the page's, so a focus crop keeps
        its full resolution -- that is the whole point of cropping.
        """
        if request.clip:
            w = abs(request.clip[2] - request.clip[0])
            h = abs(request.clip[3] - request.clip[1])
        else:
            w, h = page_w, page_h
        if w <= 0 or h <= 0:
            return request.scale
        area = w * h * request.scale * request.scale
        if area <= MAX_RENDER_PIXELS:
            return request.scale
        return request.scale * math.sqrt(MAX_RENDER_PIXELS / area)

    # -- text -------------------------------------------------------------

    def search(self, page: int, needle: str):
        """
        Rects in PDF user space -- origin bottom-left, y up.

        `search_for` answers in MuPDF page space: origin top-left, y down, and
        offset by the page rect when the crop box does not start at zero.
        Everything downstream is in user space, so the flip happens here rather
        than being left for each caller to remember -- handing page-space rects
        to `PageTransform.rect_to_widget` puts every highlight at the mirror
        image of the line it belongs to.
        """
        doc_page = self.doc[page - 1]
        rect = doc_page.rect
        left, top = float(rect.x0), float(rect.y0)
        height = float(rect.height)
        out = []
        for r in doc_page.search_for(needle):
            x0 = float(r.x0) - left
            x1 = float(r.x1) - left
            v0 = float(r.y0) - top
            v1 = float(r.y1) - top
            out.append((x0, height - v1, x1, height - v0))
        return out

    def get_text(self, page: int) -> str:
        try:
            return self.doc[page - 1].get_text("text")
        except Exception:
            return ""

    def toc(self):
        try:
            return self.doc.get_toc()
        except Exception:
            return []

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        try:
            self.doc.close()
        except Exception:
            pass
        self.doc = None
