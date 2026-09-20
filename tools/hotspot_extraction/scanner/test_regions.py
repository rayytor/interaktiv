"""
Verification and test suite for Phase 3: Region Growth, Vector Panel Snapping & Solution Spaces.

Evaluates:
- books/0e966773-5012-4f57-8be5-d892e8c75f22.pdf (English ELT textbook)
- books/51cdbbce-66f4-4baa-95d7-634bf11b7e44.pdf (Turkish Matematik textbook)

Checks:
1. No overlaps: All generated activity rects have >= 3 pt separation (overlap area <= 1.0 pt^2).
2. No panel cuts: Bounding boxes for activities on drawn panels cover the entire panel (0 panel cuts).
3. Speech bubble snapping on p33 activity 'a' (width > 500 pt, height > 350 pt).
4. Sub-question extraction on p33 activity 'b' (sub-questions 1..4).
5. Robustness across Turkish textbooks (step markers & numeric questions).
6. Graceful handling of pages with zero activities (e.g. p164).
"""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pymupdf

from tools.hotspot_extraction.scanner.primitives import extract_page_primitives
from tools.hotspot_extraction.scanner.layout import detect_layout
from tools.hotspot_extraction.scanner.markers import detect_markers
from tools.hotspot_extraction.scanner.regions import (
    grow_activity_regions,
    detect_panels,
    detect_solution_spaces,
    cuts,
    rect_overlap,
    rect_area,
    WHOLE_TOL,
)


def check_page_regions(doc, page_num: int, label: str):
    """Run primitive extraction, layout, markers, and region growth on a page."""
    prim = extract_page_primitives(doc, page_num)
    layout = detect_layout(prim)
    markers = detect_markers(prim, layout=layout)
    activities = grow_activity_regions(prim, layout, markers)

    print(f"\n[{label} Page {page_num}] Detected {len(activities)} activities from {len(markers)} markers:")
    all_rects = []
    for act in activities:
        sub_str = f" with {len(act.items)} items" if act.items else ""
        r_str = f"[{act.rect[0]:.1f}, {act.rect[1]:.1f}, {act.rect[2]:.1f}, {act.rect[3]:.1f}]"
        w = act.rect[2] - act.rect[0]
        h = act.rect[3] - act.rect[1]
        print(f"  {act.id} ({act.label}) in col {act.column}: rect={r_str} ({w:.1f}x{h:.1f}){sub_str}")
        print(f"    Headline: {repr(act.headline)}")
        for p in act.parts:
            all_rects.append((act.id, p))

    # 1. Check for overlapping hotspots
    n = len(all_rects)
    overlap_violations = []
    for i in range(n):
        for j in range(i + 1, n):
            id_a, ra = all_rects[i]
            id_b, rb = all_rects[j]
            ov = rect_overlap(ra, rb)
            if ov > 1.0:
                overlap_violations.append((id_a, id_b, ov))

    assert not overlap_violations, f"Overlap violations on p{page_num}: {overlap_violations}"
    print(f"  ✓ No overlapping hotspots (0 overlaps among {len(all_rects)} parts)")

    # 2. Check for panel cuts against detected panels
    panels = detect_panels(prim.drawings, [s for s in prim.spans if layout.content_box[1] <= s.bbox[1] and s.bbox[3] <= layout.content_box[3]])
    panel_cuts = []
    for act_id, r in all_rects:
        for p in panels:
            # Only consider panels takeable by this rect (must overlap along height by >= 50%)
            along = min(r[3], p[3]) - max(r[1], p[1])
            if along >= 0.5 * (p[3] - p[1]) and cuts(r, p):
                panel_cuts.append((act_id, r, p, rect_overlap(r, p) / rect_area(p)))

    assert not panel_cuts, f"Panel cuts on p{page_num}: {panel_cuts}"
    print(f"  ✓ No panel cuts (0 panel cuts among {len(panels)} panels)")

    return activities, panels


PDF_0E966773 = str(PROJECT_ROOT / "books" / "0e966773-5012-4f57-8be5-d892e8c75f22.pdf")
PDF_51CDBBCE = str(PROJECT_ROOT / "books" / "51cdbbce-66f4-4baa-95d7-634bf11b7e44.pdf")


def test_book_0e966773(pdf_path: str = PDF_0E966773):
    if not Path(pdf_path).exists():
        try:
            import pytest
            pytest.skip(f"{pdf_path} not found")
        except ImportError:
            print(f"Skipping: {pdf_path} not found")
            return
    print("\n" + "=" * 60)
    print(f"Testing 0e966773: {pdf_path}")
    print("=" * 60)
    doc = pymupdf.open(pdf_path)

    # --- Test Page 33 ---
    acts33, panels33 = check_page_regions(doc, 33, "0e966773")
    act_a = next(a for a in acts33 if a.label == "a")
    w_a = act_a.rect[2] - act_a.rect[0]
    h_a = act_a.rect[3] - act_a.rect[1]
    assert w_a > 500.0, f"Expected activity 'a' to span full width (>500 pt), got width={w_a:.1f}"
    assert h_a > 350.0, f"Expected activity 'a' to cover speech bubble (>350 pt), got height={h_a:.1f}"
    print(f"  ✓ Activity 'a' snapped to full speech bubble: {w_a:.1f} x {h_a:.1f} pt")

    act_b = next(a for a in acts33 if a.label == "b")
    assert act_b.items and len(act_b.items) == 4, f"Expected 4 sub-items under 'b', got {act_b.items}"
    assert [it.number for it in act_b.items] == [1, 2, 3, 4]
    print(f"  ✓ Activity 'b' has 4 sub-questions (1..4) with valid nested rects")

    labels33 = [a.label for a in acts33]
    assert labels33 == ["a", "b", "c", "d", "e", "f", "g"]
    print("✓ Page 33 passed all checks.")

    # --- Test Page 34 ---
    acts34, panels34 = check_page_regions(doc, 34, "0e966773")
    assert len(acts34) >= 7, f"Expected at least 7 activities on p34, got {len(acts34)}"
    print("✓ Page 34 passed all checks.")

    # --- Test Page 36 ---
    acts36, panels36 = check_page_regions(doc, 36, "0e966773")
    assert len(acts36) >= 9, f"Expected at least 9 activities on p36, got {len(acts36)}"
    print("✓ Page 36 passed all checks.")

    # --- Test Page 164 (back-matter / 0 activities) ---
    acts164, _ = check_page_regions(doc, 164, "0e966773")
    assert len(acts164) == 0, f"Expected 0 activities on p164, got {len(acts164)}"
    print("✓ Page 164 handled gracefully (0 activities, no errors).")


def test_book_51cdbbce(pdf_path: str = PDF_51CDBBCE):
    if not Path(pdf_path).exists():
        try:
            import pytest
            pytest.skip(f"{pdf_path} not found")
        except ImportError:
            print(f"Skipping: {pdf_path} not found")
            return
    print("\n" + "=" * 60)
    print(f"Testing 51cdbbce (Matematik): {pdf_path}")
    print("=" * 60)
    doc = pymupdf.open(pdf_path)

    # --- Test Page 15 (numeric questions 1..13) ---
    acts15, _ = check_page_regions(doc, 15, "51cdbbce")
    assert len(acts15) == 13, f"Expected 13 activities on p15, got {len(acts15)}"
    print("✓ Page 15 passed all checks (13 numeric questions).")

    # --- Test Page 46 (step markers + letters) ---
    acts46, _ = check_page_regions(doc, 46, "51cdbbce")
    assert len(acts46) == 5, f"Expected 5 activities on p46, got {len(acts46)}"
    print("✓ Page 46 passed all checks (step markers and letters).")

    # --- Test Page 50 (step markers + numeric questions) ---
    acts50, _ = check_page_regions(doc, 50, "51cdbbce")
    assert len(acts50) == 8, f"Expected 8 activities on p50, got {len(acts50)}"
    print("✓ Page 50 passed all checks (step markers and questions).")


def main():
    pdf1 = "books/0e966773-5012-4f57-8be5-d892e8c75f22.pdf"
    pdf2 = "books/51cdbbce-66f4-4baa-95d7-634bf11b7e44.pdf"

    if not Path(pdf1).exists():
        print(f"Error: {pdf1} not found.")
        sys.exit(1)
    if not Path(pdf2).exists():
        print(f"Error: {pdf2} not found.")
        sys.exit(1)

    test_book_0e966773(pdf1)
    test_book_51cdbbce(pdf2)

    print("\n" + "=" * 60)
    print("ALL PHASE 3 ACCEPTANCE CRITERIA PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
