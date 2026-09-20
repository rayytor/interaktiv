"""
Everything that touches MuPDF.

The rule this package exists to enforce: exactly one thread ever holds a
`pymupdf.Document`, and the main loop never calls a PyMuPDF function at all.
MuPDF has a single global context and is not thread-safe, so ownership is the
only safe discipline -- and page turning wants coalescing more than it wants
parallelism anyway.
"""

from .cache import TextureCache
from .requests import DocumentInfo, RenderRequest, RenderResult
from .service import RenderService

__all__ = [
    "DocumentInfo",
    "RenderRequest",
    "RenderResult",
    "RenderService",
    "TextureCache",
]
