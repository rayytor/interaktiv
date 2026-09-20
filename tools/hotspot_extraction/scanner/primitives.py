"""
High-Performance PDF Primitive Extraction via PyMuPDF.

Extracts text spans, vector drawings, and image bounding boxes directly from
PDF dictionaries without decoding uncompressed image pixel buffers.
All coordinates in PagePrimitives are standardized to PDF user space (y-up,
origin at bottom-left) to match regions.json and the frontend reader.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
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
    for info in page.get_image_info(xrefs=xrefs):
        bbox = info.get("bbox")
        if not bbox:
            continue
        pdf_bbox = to_pdf_coords(bbox, page_height)
        images.append(
            ImageRect(
                bbox=pdf_bbox,
                width=int(info.get("width", 0)),
                height=int(info.get("height", 0)),
            )
        )

    # 2. Vector drawing extraction
    drawings: List[VectorDrawing] = []
    for d in page.get_drawings():
        d_rect = d.get("rect")
        if not d_rect:
            continue
        pdf_rect = to_pdf_coords(tuple(d_rect), page_height)
        fill = tuple(d["fill"]) if d.get("fill") is not None else None
        stroke = tuple(d["stroke"]) if d.get("stroke") is not None else None
        raw_width = d.get("width")
        width = float(raw_width) if raw_width is not None else 0.0

        # Determine if drawing is a rectangle
        items = d.get("items", [])
        is_rect = False
        lines: List[Tuple[float, float, float, float]] = []

        if len(items) == 1 and items[0][0] == "re":
            is_rect = True
        elif items:
            for item in items:
                cmd = item[0]
                if cmd == "l":
                    p1, p2 = item[1], item[2]
                    lines.append(_to_pdf_line((p1.x, p1.y, p2.x, p2.y), page_height))
                elif cmd == "re":
                    is_rect = (len(items) == 1)
                    r = item[1]
                    # Add perimeter segments
                    p_lines = [
                        (r.x0, r.y0, r.x1, r.y0),
                        (r.x1, r.y0, r.x1, r.y1),
                        (r.x1, r.y1, r.x0, r.y1),
                        (r.x0, r.y1, r.x0, r.y0),
                    ]
                    lines.extend(_to_pdf_line(l, page_height) for l in p_lines)

        drawings.append(
            VectorDrawing(
                rect=pdf_rect,
                fill=fill,
                stroke=stroke,
                width=width,
                is_rect=is_rect,
                lines=lines,
            )
        )

    # 3. Text span extraction
    # Using 'dict' with ligatures and whitespace flags extracts blocks -> lines -> spans
    # without decoding embedded images or allocating character-by-character dicts.
    spans: List[TextSpan] = []
    text_flags = pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE
    text_data = page.get_text("dict", flags=text_flags)
    for block in text_data.get("blocks", []):
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line.get("spans", []):
                raw_text = span.get("text", "")
                if not raw_text:
                    continue
                bbox = span.get("bbox")
                pdf_bbox = to_pdf_coords(bbox, page_height) if bbox else (0.0, 0.0, 0.0, 0.0)
                spans.append(
                    TextSpan(
                        text=raw_text,
                        bbox=pdf_bbox,
                        font=str(span.get("font", "")),
                        size=float(span.get("size", 0.0)),
                        flags=int(span.get("flags", 0)),
                        color=int(span.get("color", 0)),
                    )
                )

    return PagePrimitives(
        page_num=page_num,
        width=page_width,
        height=page_height,
        spans=spans,
        drawings=drawings,
        images=images,
    )
