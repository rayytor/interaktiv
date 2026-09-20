"""
Regions & Diagnostics Serialization for Frontend Reader & Server.

Generates exact regions.json (BAKE_VERSION = 2) and diagnostics.json.gz
(DIAGNOSTICS_VERSION = 1) schemas drop-in compatible with js/viewer.js,
server.py, and tools/hotspot_extraction/tests/scorecard.mjs.
"""

from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import pymupdf

from .primitives import extract_page_primitives, TextSpan
from .layout import detect_folio, PageLayout
from .regions import ActivityRegion, clean_page_activities

BAKE_VERSION = 2
DIAGNOSTICS_VERSION = 1


def compute_fingerprint(pdf_path: str) -> str:
    """
    Compute `<file_size>-<sha256 of head, middle, tail>` fingerprint.

    Reads 256 KB slices from head (byte 0), middle (half-size), and tail.
    Identical to fingerprint() in bake_activities.mjs.
    """
    size = os.path.getsize(pdf_path)
    span = 256 * 1024
    offsets = [0, max(0, (size >> 1) - (span >> 1)), max(0, size - span)]

    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for at in offsets:
            f.seek(at)
            chunk = f.read(min(span, size))
            h.update(chunk)

    return f"{size}-{h.hexdigest()[:32]}"


def detect_folio_from_page(page: pymupdf.Page) -> Optional[int]:
    """Extract printed folio from header/footer using fast clipped word extraction."""
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    foot_clip = pymupdf.Rect(0, page_height - 44.0, page_width, page_height)
    head_clip = pymupdf.Rect(0, 0, page_width, 40.0)
    words = page.get_text("words", clip=foot_clip) + page.get_text("words", clip=head_clip)
    candidates = []
    for w in words:
        txt = w[4].strip()
        if not re.fullmatch(r"\d{1,4}", txt):
            continue
        val = int(txt)
        if val <= 0:
            continue
        pdf_y0 = page_height - w[3]
        pdf_y1 = page_height - w[1]
        in_foot = pdf_y0 <= 44.0  # FOOTER_BAND
        in_head = pdf_y1 >= page_height - 40.0  # HEADER_BAND
        if not (in_foot or in_head):
            continue
        center_dist = abs((w[0] + w[2]) / 2.0 - page_width / 2.0)
        candidates.append({
            "val": val,
            "in_foot": in_foot,
            "center_dist": center_dist,
            "y_dist": pdf_y0 if in_foot else (page_height - pdf_y1)
        })
    if not candidates:
        return None
    candidates.sort(key=lambda c: (
        0 if c["in_foot"] else 1,
        -c["center_dist"],
        c["y_dist"]
    ))
    return candidates[0]["val"]


def compute_book_folio(
    doc: pymupdf.Document,
    max_pages: Optional[int] = 35,
) -> Tuple[int, Dict[int, int]]:
    """
    Extract printed folio numbers and determine the modal sheet-to-folio offset.

    Returns:
        (modal_offset, by_page_map)
        where by_page_map maps 1-based pageNum to printed page number.
    """
    total = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
    folio_read: Dict[int, int] = {}

    for p in range(1, total + 1):
        page = doc[p - 1]
        folio = detect_folio_from_page(page)
        doc._forget_page(page)
        if folio is not None:
            folio_read[p] = folio

    pymupdf.TOOLS.store_shrink(100)
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass

    # Compute modal offset (pageNum - printedPage)
    offset_votes = Counter(p - f for p, f in folio_read.items())
    if offset_votes:
        # Most frequent offset; break ties by smallest absolute offset
        sorted_votes = sorted(
            offset_votes.items(),
            key=lambda item: (-item[1], abs(item[0]))
        )
        modal_offset = sorted_votes[0][0]
    else:
        modal_offset = 0

    by_page: Dict[int, int] = {}
    for p in range(1, doc.page_count + 1):
        if p in folio_read:
            by_page[p] = folio_read[p]
        elif modal_offset is not None:
            printed = p - modal_offset
            if printed >= 0:
                by_page[p] = printed

    return modal_offset, by_page


def detect_book_calibration(
    doc: Optional[pymupdf.Document] = None,
    pages_activities: Optional[Dict[int, List[ActivityRegion]]] = None,
    sample_budget: int = 40,
    sample_spans: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Calibrate document typography: marker font styles, dominant marker size,
    and detector confidence level ('strong', 'weak', 'none').
    """
    all_acts: List[ActivityRegion] = []
    if pages_activities:
        for acts in pages_activities.values():
            all_acts.extend(acts)

    lettered_acts = [a for a in all_acts if a.label and not a.anchored]
    has_letters = any(a.label.isalpha() for a in lettered_acts if a.label)

    if lettered_acts and has_letters:
        confidence = "strong"
        enabled = True
    elif all_acts:
        confidence = "weak"
        enabled = True
    else:
        confidence = "none"
        enabled = False

    marker_sizes: List[float] = []
    marker_styles: Set[str] = set()
    marker_fonts: Set[str] = set()

    if sample_spans is not None:
        for s in sample_spans:
            if isinstance(s, dict):
                txt = s.get("text", "")
                sz = float(s.get("size", 0.0))
                fnt = str(s.get("font", ""))
            else:
                txt = getattr(s, "text", "")
                sz = float(getattr(s, "size", 0.0))
                fnt = str(getattr(s, "font", ""))
            txt_clean = txt.strip().lower()
            if re.fullmatch(r"[a-z]", txt_clean) or re.fullmatch(r"\d{1,2}", txt_clean):
                sz_round = round(sz, 1)
                marker_sizes.append(sz_round)
                marker_styles.add(f"{fnt}|{sz_round}")
                marker_fonts.add(fnt)
    elif doc is not None:
        # Extract sample fonts and sizes from first pages (text only, no drawings)
        sample_pages = min(doc.page_count, sample_budget)
        text_flags = pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE

        for p in range(1, sample_pages + 1):
            page = doc[p - 1]
            text_data = page.get_text("dict", flags=text_flags)
            doc._forget_page(page)
            for block in text_data.get("blocks", []):
                if "lines" not in block:
                    continue
                for line in block["lines"]:
                    for s in line.get("spans", []):
                        txt = s.get("text", "").strip().lower()
                        if re.fullmatch(r"[a-z]", txt) or re.fullmatch(r"\d{1,2}", txt):
                            sz = round(float(s.get("size", 0.0)), 1)
                            marker_sizes.append(sz)
                            fnt = str(s.get("font", ""))
                            marker_styles.add(f"{fnt}|{sz}")
                            marker_fonts.add(fnt)
        pymupdf.TOOLS.store_shrink(100)
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass

    modal_size = 11
    if marker_sizes:
        size_counts = Counter(marker_sizes)
        modal_size = size_counts.most_common(1)[0][0]

    return {
        "enabled": enabled,
        "confidence": confidence,
        "markerSize": modal_size,
        "markerStyles": sorted(list(marker_styles)) if marker_styles else ["sans-serif|1|-0.2|11.0"],
        "markerFonts": sorted(list(marker_fonts)) if marker_fonts else ["sans-serif|1|-0.2"],
    }


def serialize_activity(act: ActivityRegion) -> Dict[str, Any]:
    """Serialize a single ActivityRegion into the exact viewer JSON schema."""
    out: Dict[str, Any] = {
        "id": act.id,
        "label": act.label if act.label is not None else None,
        "column": act.column,
        "rect": [act.rect[0], act.rect[1], act.rect[2], act.rect[3]],
        "parts": [[p[0], p[1], p[2], p[3]] for p in (act.parts or [act.rect])],
    }

    if act.headline:
        out["headline"] = act.headline

    if act.anchored:
        out["anchored"] = True

    if act.items:
        out["items"] = [
            {
                "id": q.id,
                "label": q.label if q.label is not None else None,
                "number": q.number if q.number is not None else None,
                "partIndex": q.part_index,
                "rect": [q.rect[0], q.rect[1], q.rect[2], q.rect[3]],
                **({"text": q.text} if q.text else {}),
            }
            for q in act.items
        ]

    return out


def serialize_book_regions(
    book_id: str,
    pdf_path: str,
    page_count: int,
    pages_activities: Dict[int, List[ActivityRegion]],
    folio_map: Dict[int, int],
    folio_offset: int,
    calibration_info: Dict[str, Any],
    pages_layout: Optional[Dict[int, PageLayout]] = None,
    page_dimensions: Optional[Dict[int, Tuple[float, float]]] = None,
    anchors_by_page: Optional[Dict[int, List[str]]] = None,
    built_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the complete dictionary representation of regions.json (BAKE_VERSION = 2).
    """
    fingerprint = compute_fingerprint(pdf_path)
    now_iso = built_at or (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")

    pages_out: Dict[str, Any] = {}
    for p, raw_acts in sorted(pages_activities.items()):
        if not raw_acts:
            continue
        acts = clean_page_activities(raw_acts)
        if not acts:
            continue

        # Determine dimensions
        pw, ph = 595.0, 842.0
        if page_dimensions and p in page_dimensions:
            pw, ph = page_dimensions[p]
        elif pages_layout and p in pages_layout:
            cbox = pages_layout[p].content_box
            pw = cbox[2] if cbox[2] > 0 else 595.0
            ph = cbox[3] + 40.0

        # Columns
        cols = []
        if pages_layout and p in pages_layout:
            cols = [[c.x0, c.x1] for c in pages_layout[p].columns]
        elif acts:
            col_indices = sorted(list({a.column for a in acts}))
            for c_idx in col_indices:
                col_acts = [a for a in acts if a.column == c_idx]
                min_x = min(a.rect[0] for a in col_acts)
                max_x = max(a.rect[2] for a in col_acts)
                cols.append([min_x, max_x])

        pages_out[str(p)] = {
            "pageWidth": round(pw, 3),
            "pageHeight": round(ph, 3),
            "columns": cols,
            "activities": [serialize_activity(a) for a in acts],
        }

    # Format anchors dictionary
    anchors_out: Dict[str, List[str]] = {}
    if anchors_by_page:
        for p_idx, a_list in sorted(anchors_by_page.items()):
            if a_list:
                anchors_out[str(p_idx)] = [str(a) for a in a_list]

    return {
        "version": BAKE_VERSION,
        "bookId": book_id,
        "fingerprint": fingerprint,
        "builtAt": now_iso,
        "pageCount": page_count,
        "calibration": calibration_info,
        "folio": {
            "offset": folio_offset,
            "byPage": {str(p): folio_map[p] for p in sorted(folio_map.keys())},
        },
        "anchors": anchors_out,
        "pages": pages_out,
    }


def serialize_diagnostics(
    book_id: str,
    pdf_path: str,
    diagnostics_by_page: Dict[int, Dict[str, Any]],
    fingerprint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build dictionary representation for diagnostics.json.gz (DIAGNOSTICS_VERSION = 1).
    """
    fp = fingerprint or compute_fingerprint(pdf_path)

    def _to_rect_coords(item: Any) -> List[float]:
        if isinstance(item, dict) and "rect" in item:
            coords = item["rect"]
        elif isinstance(item, (list, tuple)):
            coords = item
        else:
            return []
        return [round(float(x), 4) for x in coords]

    pages_out: Dict[str, Any] = {}
    for p, d in sorted(diagnostics_by_page.items()):
        pages_out[str(p)] = {
            "pageWidth": round(float(d.get("pageWidth", 595.0)), 3),
            "pageHeight": round(float(d.get("pageHeight", 842.0)), 3),
            "contentTop": round(float(d["contentTop"]), 3) if d.get("contentTop") is not None else None,
            "contentBottom": round(float(d["contentBottom"]), 3) if d.get("contentBottom") is not None else None,
            "panels": [_to_rect_coords(r) for r in d.get("panels", []) if _to_rect_coords(r)],
            "solutions": [_to_rect_coords(r) for r in d.get("solutions", []) if _to_rect_coords(r)],
            "markers": [_to_rect_coords(r) for r in d.get("markers", []) if _to_rect_coords(r)],
        }

    return {
        "version": DIAGNOSTICS_VERSION,
        "bookId": book_id,
        "fingerprint": fp,
        "pages": pages_out,
    }


def save_regions_json(
    output_path: str,
    regions_data: Dict[str, Any],
) -> None:
    """Save formatted regions.json, creating parent directory if necessary."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(regions_data, f, ensure_ascii=False, indent=2)


def save_diagnostics_gz(
    output_path: str,
    diagnostics_data: Dict[str, Any],
) -> None:
    """Save gzip-compressed diagnostics.json.gz, creating parent directory if necessary."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    raw_bytes = json.dumps(diagnostics_data, ensure_ascii=False).encode("utf-8")
    with gzip.open(output_path, "wb") as f:
        f.write(raw_bytes)
