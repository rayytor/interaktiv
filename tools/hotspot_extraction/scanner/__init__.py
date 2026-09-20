"""
Interaktiv Activity Hotspot Scanner
High-performance PyMuPDF-based hotspot detection engine.
"""

from .primitives import (
    TextSpan,
    VectorDrawing,
    ImageRect,
    PagePrimitives,
    extract_page_primitives,
    to_pdf_coords,
    from_pdf_coords,
)

__all__ = [
    "TextSpan",
    "VectorDrawing",
    "ImageRect",
    "PagePrimitives",
    "extract_page_primitives",
    "to_pdf_coords",
    "from_pdf_coords",
]
