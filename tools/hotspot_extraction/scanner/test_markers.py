"""
Verification and test suite for Phase 2: Layout Segmentation & Typographic Marker Detection.

Evaluates:
- books/0e966773-5012-4f57-8be5-d892e8c75f22.pdf (English ELT textbook)
- books/51cdbbce-66f4-4baa-95d7-634bf11b7e44.pdf (Turkish Matematik textbook)

Checks:
1. Columns are accurately detected on 1-column and 2-column pages.
2. Known activity markers (e.g. p33 of 0e966773: a,b,c,d on left, e,f,g on right) are detected with 100% precision.
3. Turkish step markers (e.g. '1. adım:', '2. adım:') are recognized.
4. Nested sub-questions (1, 2, 3...) are correctly identified under activities.
"""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pymupdf

from tools.hotspot_extraction.scanner.primitives import extract_page_primitives
from tools.hotspot_extraction.scanner.layout import detect_layout, PageLayout
from tools.hotspot_extraction.scanner.markers import detect_markers, DetectedMarker


def test_book_0e966773(pdf_path: str):
    print("\n" + "=" * 60)
    print(f"Testing 0e966773: {pdf_path}")
    print("=" * 60)
    doc = pymupdf.open(pdf_path)

    # --- Test Page 33 (2 columns, a-d left, e-g right, b has subquestions 1-4) ---
    p33 = extract_page_primitives(doc, 33)
    layout33 = detect_layout(p33)
    markers33 = detect_markers(p33, layout=layout33)

    print(f"\n[Page 33] Body font size: {layout33.body_font_size} pt")
    print(f"[Page 33] Columns ({len(layout33.columns)}):")
    for col in layout33.columns:
        print(f"  Col {col.index}: [{col.x0:.1f}, {col.x1:.1f}], y: [{col.closes_at:.1f}, {col.opens_at:.1f}]")

    print(f"[Page 33] Detected markers ({len(markers33)}):")
    for m in markers33:
        sub_str = f" with {len(m.items)} items: {[it.number for it in m.items]}" if m.items else ""
        print(f"  Marker '{m.label}' in col {m.column_index} (val={m.normalized_value}){sub_str}")

    assert len(layout33.columns) == 2, f"Expected 2 columns on p33, got {len(layout33.columns)}"
    col0_labels = [m.label.strip() for m in markers33 if m.column_index == 0]
    col1_labels = [m.label.strip() for m in markers33 if m.column_index == 1]
    assert col0_labels == ["a", "b", "c", "d"], f"Expected ['a', 'b', 'c', 'd'] in col 0, got {col0_labels}"
    assert col1_labels == ["e", "f", "g"], f"Expected ['e', 'f', 'g'] in col 1, got {col1_labels}"

    marker_b = next(m for m in markers33 if m.label.strip() == "b")
    b_sub_numbers = [it.number for it in marker_b.items]
    assert b_sub_numbers == [1, 2, 3, 4], f"Expected items [1, 2, 3, 4] under marker b, got {b_sub_numbers}"
    print("✓ Page 33 passed all checks (100% precision on columns, markers, and sub-questions).")

    # --- Test Page 18 (2 columns, a,b left; c,d right; a has 1..8, c has 1..6, d has 1..5) ---
    p18 = extract_page_primitives(doc, 18)
    layout18 = detect_layout(p18)
    markers18 = detect_markers(p18, layout=layout18)

    print(f"\n[Page 18] Body font size: {layout18.body_font_size} pt")
    print(f"[Page 18] Columns ({len(layout18.columns)}):")
    for col in layout18.columns:
        print(f"  Col {col.index}: [{col.x0:.1f}, {col.x1:.1f}]")

    print(f"[Page 18] Detected markers ({len(markers18)}):")
    for m in markers18:
        sub_str = f" with {len(m.items)} items: {[it.number for it in m.items]}" if m.items else ""
        print(f"  Marker '{m.label}' in col {m.column_index}{sub_str}")

    assert len(layout18.columns) == 2, f"Expected 2 columns on p18, got {len(layout18.columns)}"
    p18_col0 = [m.label.strip() for m in markers18 if m.column_index == 0]
    p18_col1 = [m.label.strip() for m in markers18 if m.column_index == 1]
    assert p18_col0 == ["a", "b"], f"Expected ['a', 'b'] in col 0, got {p18_col0}"
    assert p18_col1 == ["c", "d"], f"Expected ['c', 'd'] in col 1, got {p18_col1}"

    marker_a = next(m for m in markers18 if m.label.strip() == "a")
    assert [it.number for it in marker_a.items] == list(range(1, 9)), "Expected items 1..8 under marker a"
    marker_c = next(m for m in markers18 if m.label.strip() == "c")
    assert [it.number for it in marker_c.items] == list(range(1, 7)), "Expected items 1..6 under marker c"
    marker_d = next(m for m in markers18 if m.label.strip() == "d")
    assert [it.number for it in marker_d.items] == list(range(1, 6)), "Expected items 1..5 under marker d"
    print("✓ Page 18 passed all checks.")

    # --- Test Page 20 (1-column layout, markers j, k) ---
    p20 = extract_page_primitives(doc, 20)
    layout20 = detect_layout(p20)
    markers20 = detect_markers(p20, layout=layout20)

    print(f"\n[Page 20] Body font size: {layout20.body_font_size} pt")
    print(f"[Page 20] Columns ({len(layout20.columns)}):")
    for col in layout20.columns:
        print(f"  Col {col.index}: [{col.x0:.1f}, {col.x1:.1f}]")

    print(f"[Page 20] Detected markers ({len(markers20)}):")
    for m in markers20:
        print(f"  Marker '{m.label}' in col {m.column_index}")

    assert len(layout20.columns) == 1, f"Expected 1 column on p20, got {len(layout20.columns)}"
    p20_labels = [m.label.strip() for m in markers20]
    assert p20_labels == ["j", "k"], f"Expected ['j', 'k'] on p20, got {p20_labels}"
    print("✓ Page 20 passed all checks (1-column layout, markers j, k).")


def test_book_51cdbbce(pdf_path: str):
    print("\n" + "=" * 60)
    print(f"Testing 51cdbbce (Matematik): {pdf_path}")
    print("=" * 60)
    doc = pymupdf.open(pdf_path)

    # --- Test Page 15 (1-column layout, numeric markers 1..13) ---
    p15 = extract_page_primitives(doc, 15)
    layout15 = detect_layout(p15)
    markers15 = detect_markers(p15, layout=layout15)

    print(f"\n[Page 15] Body font size: {layout15.body_font_size} pt")
    print(f"[Page 15] Columns ({len(layout15.columns)}):")
    for col in layout15.columns:
        print(f"  Col {col.index}: [{col.x0:.1f}, {col.x1:.1f}]")

    print(f"[Page 15] Detected markers ({len(markers15)}):")
    for m in markers15:
        print(f"  Marker '{m.label}' in col {m.column_index}")

    assert len(layout15.columns) == 1, f"Expected 1 column on p15, got {len(layout15.columns)}"
    p15_labels = [m.label.strip() for m in markers15]
    expected_15 = [f"{i}." for i in range(1, 14)]
    assert p15_labels == expected_15, f"Expected {expected_15} on p15, got {p15_labels}"
    print("✓ Page 15 passed all checks (1-column layout, numeric markers 1..13).")

    # --- Test Page 16 (2-column layout, markers 1., 2. and 1., 2.) ---
    p16 = extract_page_primitives(doc, 16)
    layout16 = detect_layout(p16)
    markers16 = detect_markers(p16, layout=layout16)

    print(f"\n[Page 16] Body font size: {layout16.body_font_size} pt")
    print(f"[Page 16] Columns ({len(layout16.columns)}):")
    for col in layout16.columns:
        print(f"  Col {col.index}: [{col.x0:.1f}, {col.x1:.1f}]")

    print(f"[Page 16] Detected markers ({len(markers16)}):")
    for m in markers16:
        print(f"  Marker '{m.label}' in col {m.column_index}")

    assert len(layout16.columns) == 2, f"Expected 2 columns on p16, got {len(layout16.columns)}"
    p16_labels = [m.label.strip() for m in markers16]
    assert p16_labels == ["1.", "2.", "1.", "2."], f"Expected ['1.', '2.', '1.', '2.'] on p16, got {p16_labels}"
    print("✓ Page 16 passed all checks (2-column layout, split markers).")

    # --- Test Page 37 (numeric markers 2..5, rogue 'G' discarded) ---
    p37 = extract_page_primitives(doc, 37)
    layout37 = detect_layout(p37)
    markers37 = detect_markers(p37, layout=layout37)

    print(f"\n[Page 37] Body font size: {layout37.body_font_size} pt")
    print(f"[Page 37] Columns ({len(layout37.columns)}):")
    for col in layout37.columns:
        print(f"  Col {col.index}: [{col.x0:.1f}, {col.x1:.1f}]")

    print(f"[Page 37] Detected markers ({len(markers37)}):")
    for m in markers37:
        print(f"  Marker '{m.label}' in col {m.column_index} (is_step={m.is_step})")

    p37_labels = [m.label.strip() for m in markers37]
    assert p37_labels == ["2.", "3.", "4.", "5."], f"Expected ['2.', '3.', '4.', '5.'] on p37, got {p37_labels}"
    assert all(m.label.strip() != "G" for m in markers37), "Rogue symbol 'G' should have been discarded"
    print("✓ Page 37 passed all checks (numeric markers 2..5, rogue G discarded).")

    # --- Test Page 46 (Turkish letter markers a), b) and step markers 1. adım: .. 3. adım:) ---
    p46 = extract_page_primitives(doc, 46)
    layout46 = detect_layout(p46)
    markers46 = detect_markers(p46, layout=layout46)

    print(f"\n[Page 46] Body font size: {layout46.body_font_size} pt")
    print(f"[Page 46] Detected markers ({len(markers46)}):")
    for m in markers46:
        print(f"  Marker '{m.label}' in col {m.column_index} (is_step={m.is_step})")

    p46_labels = [m.label.strip() for m in markers46]
    assert p46_labels == ["a)", "b)", "1. adım:", "2. adım:", "3. adım:"], f"Unexpected markers on p46: {p46_labels}"
    step_markers_46 = [m for m in markers46 if m.is_step]
    assert len(step_markers_46) == 3, f"Expected 3 step markers on p46, got {len(step_markers_46)}"
    assert [m.normalized_value for m in step_markers_46] == [1.0, 2.0, 3.0]
    print("✓ Page 46 passed all checks (Turkish letters and step markers recognized).")

    # --- Test Page 50 (Step markers 1..4 followed by numeric questions 1..4) ---
    p50 = extract_page_primitives(doc, 50)
    layout50 = detect_layout(p50)
    markers50 = detect_markers(p50, layout=layout50)

    print(f"\n[Page 50] Detected markers ({len(markers50)}):")
    for m in markers50:
        print(f"  Marker '{m.label}' in col {m.column_index} (is_step={m.is_step})")

    p50_labels = [m.label.strip() for m in markers50]
    expected_50 = ["1. adım:", "2. adım:", "3. adım:", "4. adım:", "1.", "2.", "3.", "4."]
    assert p50_labels == expected_50, f"Unexpected markers on p50: {p50_labels}"
    print("✓ Page 50 passed all checks (coexisting step markers and numbered questions).")


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
    print("ALL PHASE 2 ACCEPTANCE CRITERIA PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
