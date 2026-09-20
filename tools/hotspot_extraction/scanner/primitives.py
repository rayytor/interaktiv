"""
High-Performance PDF Primitive Extraction via PyMuPDF.

Extracts text spans, vector drawings, and image bounding boxes directly from
PDF dictionaries without decoding uncompressed image pixel buffers.
All coordinates in PagePrimitives are standardized to PDF user space (y-up,
origin at bottom-left) to match regions.json and the frontend reader.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import pymupdf


@dataclass
class TextSpan:
    """A contiguous span of text sharing a single font and style."""
    text: str
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1) in PDF user space (y-up)
    font: str
    size: float
    flags: int  # PyMuPDF flags (bit 0: superscript, bit 1: italic, bit 2: serif, bit 4: bold)
    color: int


@dataclass
class VectorDrawing:
    """A vector path or shape (panel backdrop, rule, table border)."""
    rect: Tuple[float, float, float, float]  # Bounding box in PDF user space (y-up)
    fill: Optional[Tuple[float, ...]]  # RGB/Gray fill tuple or None
    stroke: Optional[Tuple[float, ...]]  # RGB/Gray stroke tuple or None
    width: float  # Line width
    is_rect: bool  # True if path is a rectangular fill/box
    lines: List[Tuple[float, float, float, float]]  # Line segments (x0, y0, x1, y1) in PDF coords


@dataclass
class ImageRect:
    """Image location and dimensions without pixel data."""
    bbox: Tuple[float, float, float, float]  # Bounding box in PDF user space (y-up)
    width: int  # Native pixel width
    height: int  # Native pixel height


@dataclass
class PagePrimitives:
    """All raw primitives extracted from a single PDF page."""
    page_num: int  # 1-based page index
    width: float  # Page width in pt
    height: float  # Page height in pt
    spans: List[TextSpan]
    drawings: List[VectorDrawing]
    images: List[ImageRect]


def to_pdf_coords(
    rect: Tuple[float, float, float, float],
    page_height: float
) -> Tuple[float, float, float, float]:
    """
    Convert (x0, y0, x1, y1) from PyMuPDF screen coords (y-down, top-left origin)
    to PDF user space coords (y-up, bottom-left origin).
    """
    x0, y0, x1, y1 = rect
    return (float(x0), float(page_height - y1), float(x1), float(page_height - y0))


def from_pdf_coords(
    rect: Tuple[float, float, float, float],
    page_height: float
) -> Tuple[float, float, float, float]:
    """
    Convert (x0, y0, x1, y1) from PDF user space coords (y-up, bottom-left origin)
    to PyMuPDF screen coords (y-down, top-left origin).
    """
    x0, y0, x1, y1 = rect
    return (float(x0), float(page_height - y1), float(x1), float(page_height - y0))


def _to_pdf_line(
    line: Tuple[float, float, float, float],
    page_height: float
) -> Tuple[float, float, float, float]:
    """Convert a line segment (x0, y0, x1, y1) from y-down to y-up."""
    x0, y0, x1, y1 = line
    return (float(x0), float(page_height - y0), float(x1), float(page_height - y1))


def extract_page_primitives(
    doc: pymupdf.Document,
    page_num: int,
    *,
    xrefs: bool = False
) -> PagePrimitives:
    """
    Extracts text spans, vector drawings, and image bounding boxes for a page.
    Crucially: NEVER calls page.get_pixmap() or decodes image pixel streams.

    Args:
        doc: PyMuPDF Document instance.
        page_num: 1-based page number.
        xrefs: Whether to resolve exact xref ids for images (default: False for max speed).

    Returns:
        PagePrimitives containing all extracted data in PDF user space (y-up).
    """
    # PyMuPDF uses 0-based page index
    page = doc[page_num - 1]
    page_rect = page.rect
    page_width = float(page_rect.width)
    page_height = float(page_rect.height)

    # 1. Image extraction (header metadata only, zero pixel decoding)
    images: List[ImageRect] = []
    raw_images = page.get_image_info(xrefs=xrefs)
    for info in raw_images:
        bbox = info.get("bbox")
        if not bbox:
            continue
        w = int(info.get("width", 0))
        h = int(info.get("height", 0))
        if (bbox[2] - bbox[0] < 5.0 and bbox[3] - bbox[1] < 5.0) or (w < 5 and h < 5):
            continue
        pdf_bbox = to_pdf_coords(bbox, page_height)
        images.append(
            ImageRect(
                bbox=pdf_bbox,
                width=w,
                height=h,
            )
        )
    if len(raw_images) > 100:
        del raw_images
        pymupdf.TOOLS.store_shrink(100)
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass

    # 2. Vector drawing extraction via get_cdrawings callback
    # Crucially: avoids allocating millions of Python Point/Rect objects for complex artwork/meshes.
    # Panels and table/solution rules never exceed 100 segments.
    drawings: List[VectorDrawing] = []

    def _collect_drawing(d: dict) -> None:
        items = d.get("items", [])
        if len(items) > 100:
            return
        d_rect = d.get("rect")
        if not d_rect:
            return
        dw = d_rect[2] - d_rect[0]
        dh = d_rect[3] - d_rect[1]
        # Only retain drawings that could potentially be panels or solution rules
        if not ((dw >= 20.0 and dh >= 12.0) or (dh <= 3.0 and dw >= 20.0) or (dw <= 3.0 and dh >= 20.0)):
            return
        pdf_rect = to_pdf_coords(tuple(d_rect), page_height)
        fill = tuple(d["fill"]) if d.get("fill") is not None else None
        stroke = tuple(d["color"]) if d.get("color") is not None else (
            tuple(d["stroke"]) if d.get("stroke") is not None else None
        )
        raw_width = d.get("width")
        width = float(raw_width) if raw_width is not None else 0.0
        is_rect = (len(items) == 1 and items[0][0] == "re")

        drawings.append(
            VectorDrawing(
                rect=pdf_rect,
                fill=fill,
                stroke=stroke,
                width=width,
                is_rect=is_rect,
                lines=[],
            )
        )

    page.get_cdrawings(callback=_collect_drawing)

    # 3. Text span extraction
    # Using 'dict' with ligatures and whitespace flags extracts blocks -> lines -> spans
    # without decoding embedded images or allocating character-by-character dicts.
    spans: List[TextSpan] = []
    text_flags = pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE
    text_data = page.get_text("dict", flags=text_flags)

    # Check if any span contains a tab character. If so, fetch word-level bboxes
    # to precisely split tab-separated spans (e.g. "1.\tAşağıdaki sorular...") into
    # separate spans for the leading marker and following body text.
    has_tabs = any(
        "\t" in s.get("text", "")
        for b in text_data.get("blocks", [])
        for l in b.get("lines", [])
        for s in l.get("spans", [])
    )
    words_by_line: Dict[Tuple[int, int], List[Tuple]] = {}
    if has_tabs:
        for w in page.get_text("words"):
            words_by_line.setdefault((w[5], w[6]), []).append(w)

    for b_idx, block in enumerate(text_data.get("blocks", [])):
        if "lines" not in block:
            continue
        for l_idx, line in enumerate(block["lines"]):
            l_words = words_by_line.get((b_idx, l_idx), [])
            w_idx = 0
            for span in line.get("spans", []):
                raw_text = span.get("text", "")
                if not raw_text:
                    continue
                bbox = span.get("bbox") or (0.0, 0.0, 0.0, 0.0)
                font = str(span.get("font", ""))
                size = float(span.get("size", 0.0))
                flags = int(span.get("flags", 0))
                color = int(span.get("color", 0))

                if "\t" in raw_text:
                    parts = raw_text.split("\t")
                    for p_i, part in enumerate(parts):
                        if not part:
                            continue
                        part_words = part.strip().split()
                        num_words = len(part_words)
                        if num_words > 0 and w_idx < len(l_words):
                            w_first = l_words[w_idx]
                            w_last = l_words[min(w_idx + num_words - 1, len(l_words) - 1)]
                            part_bbox = (w_first[0], bbox[1], w_last[2], bbox[3])
                            w_idx += num_words
                        else:
                            est_w = max(len(part) * size * 0.55, 4.0)
                            p_x0 = bbox[0] if p_i == 0 else min(bbox[0] + est_w, bbox[2])
                            part_bbox = (p_x0, bbox[1], min(p_x0 + est_w, bbox[2]), bbox[3])

                        pdf_bbox = to_pdf_coords(part_bbox, page_height)
                        spans.append(
                            TextSpan(
                                text=part,
                                bbox=pdf_bbox,
                                font=font,
                                size=size,
                                flags=flags,
                                color=color,
                            )
                        )
                else:
                    part_words = raw_text.strip().split()
                    w_idx += len(part_words)
                    pdf_bbox = to_pdf_coords(bbox, page_height)
                    spans.append(
                        TextSpan(
                            text=raw_text,
                            bbox=pdf_bbox,
                            font=font,
                            size=size,
                            flags=flags,
                            color=color,
                        )
                    )

    doc._forget_page(page)

    return PagePrimitives(
        page_num=page_num,
        width=page_width,
        height=page_height,
        spans=spans,
        drawings=drawings,
        images=images,
    )
