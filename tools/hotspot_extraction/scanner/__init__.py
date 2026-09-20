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
from .layout import (
    Column,
    PageLayout,
    detect_layout,
    detect_folio,
    detect_body_font_size,
    detect_columns,
)
from .markers import (
    SubQuestion,
    DetectedMarker,
    parse_label,
    detect_markers,
    detect_subquestions,
    LABEL_VALUES,
    LABEL_SUCCESSORS,
)
from .regions import (
    SubItemRect,
    ActivityRegion,
    detect_panels,
    detect_solution_spaces,
    grow_activity_regions,
    separate_rects,
    cuts,
)
from .anchors import (
    PublisherOge,
    load_publisher_oges,
    parse_oge_label,
    find_unplaced_anchors,
    reconcile_anchors,
)
from .serializer import (
    BAKE_VERSION,
    DIAGNOSTICS_VERSION,
    compute_fingerprint,
    compute_book_folio,
    detect_book_calibration,
    serialize_activity,
    serialize_book_regions,
    serialize_diagnostics,
    save_regions_json,
    save_diagnostics_gz,
)

__all__ = [
    # Primitives
    "TextSpan",
    "VectorDrawing",
    "ImageRect",
    "PagePrimitives",
    "extract_page_primitives",
    "to_pdf_coords",
    "from_pdf_coords",
    # Layout
    "Column",
    "PageLayout",
    "detect_layout",
    "detect_folio",
    "detect_body_font_size",
    "detect_columns",
    # Markers
    "SubQuestion",
    "DetectedMarker",
    "parse_label",
    "detect_markers",
    "detect_subquestions",
    "LABEL_VALUES",
    "LABEL_SUCCESSORS",
    # Regions
    "SubItemRect",
    "ActivityRegion",
    "detect_panels",
    "detect_solution_spaces",
    "grow_activity_regions",
    "separate_rects",
    "cuts",
    # Anchors
    "PublisherOge",
    "load_publisher_oges",
    "parse_oge_label",
    "find_unplaced_anchors",
    "reconcile_anchors",
    # Serializer
    "BAKE_VERSION",
    "DIAGNOSTICS_VERSION",
    "compute_fingerprint",
    "compute_book_folio",
    "detect_book_calibration",
    "serialize_activity",
    "serialize_book_regions",
    "serialize_diagnostics",
    "save_regions_json",
    "save_diagnostics_gz",
]
