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
from .figures import (
    Figure,
    detect_figures,
)
from .prompts import (
    Paragraph,
    detect_activity_markers,
    detect_prompts,
    group_paragraphs,
    reads_as_question,
    reads_as_rubric,
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
from .profile import (
    Profile,
    RULER,
    SPEC,
    active_profile,
    apply_profile,
    load_profile,
    profile_hash,
    save_profile,
)
from .regions import (
    SubItemRect,
    ActivityRegion,
    GrowthTrace,
    PageGeometry,
    detect_panels,
    detect_solution_spaces,
    grow_activity_regions,
    separate_rects,
    snap_edges,
    clean_page_activities,
    cuts,
)
from .trace import (
    TRACE_VERSION,
    Drop,
    attribute_empty_page,
    GrowthTrace,
    serialize_page_trace,
    serialize_trace,
    save_trace_gz,
    load_trace_gz,
)
from .anchors import (
    PublisherOge,
    load_publisher_oges,
    parse_oge_label,
    link_page_oges,
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
    # Figures
    "Figure",
    "detect_figures",
    # Prompts
    "Paragraph",
    "detect_activity_markers",
    "detect_prompts",
    "group_paragraphs",
    "reads_as_question",
    "reads_as_rubric",
    # Profile
    "Profile",
    "RULER",
    "SPEC",
    "active_profile",
    "apply_profile",
    "load_profile",
    "profile_hash",
    "save_profile",
    # Regions
    "SubItemRect",
    "ActivityRegion",
    "GrowthTrace",
    "PageGeometry",
    "detect_panels",
    "detect_solution_spaces",
    "grow_activity_regions",
    "separate_rects",
    "snap_edges",
    "clean_page_activities",
    "cuts",
    # Trace
    "TRACE_VERSION",
    "Drop",
    "attribute_empty_page",
    "serialize_page_trace",
    "serialize_trace",
    "save_trace_gz",
    "load_trace_gz",
    # Anchors
    "PublisherOge",
    "load_publisher_oges",
    "parse_oge_label",
    "link_page_oges",
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
