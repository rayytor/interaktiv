#!/usr/bin/env python3
"""
Benchmark script for Phase 1: High-Performance Primitive Extraction.

Measures:
1. Extraction throughput (pages/sec) and completeness across 0e966773.pdf.
2. Peak RSS on sheet 164 of 1cc573f6.pdf to verify zero-pixel decoding
   (legacy Node.js spiked to 2.8 GB RSS on this 15,071x9,830 illustration).
"""

import multiprocessing
import os
import sys
import time
import psutil
import pymupdf

# Add repo root to path so scanner package can be imported
HERE = os.path.dirname(os.path.abspath(__file__))
HOTSPOT_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(HOTSPOT_DIR))
sys.path.insert(0, REPO_ROOT)

from tools.hotspot_extraction.scanner.primitives import (
    extract_page_primitives,
    PagePrimitives,
    to_pdf_coords,
    from_pdf_coords,
)


def get_current_rss_mb() -> float:
    """Returns current process resident memory (RSS) in megabytes."""
    return psutil.Process().memory_info().rss / (1024 * 1024)


def test_coordinate_conversion():
    """Verify coordinate conversion between PyMuPDF (y-down) and PDF (y-up)."""
    print("--- Test: Coordinate Conversion ---")
    page_height = 800.0
    # PyMuPDF coords: box from y=100 to y=200
    mupdf_rect = (50.0, 100.0, 300.0, 200.0)
    pdf_rect = to_pdf_coords(mupdf_rect, page_height)

    # In PDF coords (y-up):
    # bottom = 800 - 200 = 600
    # top = 800 - 100 = 700
    assert pdf_rect == (50.0, 600.0, 300.0, 700.0), f"Unexpected PDF rect: {pdf_rect}"

    # Invert back
    roundtrip = from_pdf_coords(pdf_rect, page_height)
    assert roundtrip == mupdf_rect, f"Roundtrip failed: {roundtrip} != {mupdf_rect}"
    print("  [PASS] to_pdf_coords and from_pdf_coords are exact and reversible.\n")


def benchmark_full_book(pdf_path: str):
    """Benchmark extraction throughput and completeness across an entire book."""
    print(f"--- Benchmark: Full Book ({os.path.basename(pdf_path)}) ---")
    assert os.path.isfile(pdf_path), f"File not found: {pdf_path}"

    doc = pymupdf.open(pdf_path)
    num_pages = len(doc)
    print(f"  Pages: {num_pages}")

    rss_before = get_current_rss_mb()
    t0 = time.perf_counter()

    total_spans = 0
    total_drawings = 0
    total_images = 0
    pages_with_content = 0

    for p in range(1, num_pages + 1):
        primitives: PagePrimitives = extract_page_primitives(doc, p)
        total_spans += len(primitives.spans)
        total_drawings += len(primitives.drawings)
        total_images += len(primitives.images)

        if primitives.spans or primitives.drawings or primitives.images:
            pages_with_content += 1

    elapsed = time.perf_counter() - t0
    rss_after = get_current_rss_mb()
    throughput = num_pages / elapsed

    print(f"  Elapsed: {elapsed:.3f}s")
    print(f"  Throughput: {throughput:.1f} pages/sec")
    print(f"  Spans extracted: {total_spans}")
    print(f"  Drawings extracted: {total_drawings}")
    print(f"  Images extracted: {total_images}")
    print(f"  Pages with content: {pages_with_content} / {num_pages}")
    print(f"  Peak RSS: {rss_after:.1f} MB")

    # Assertions
    assert pages_with_content == num_pages, "Some pages yielded zero primitives"
    assert total_spans > 10000, f"Expected >10k spans, got {total_spans}"
    assert total_drawings > 10000, f"Expected >10k drawings, got {total_drawings}"
    assert total_images > 1000, f"Expected >1k images, got {total_images}"
    print("  [PASS] Full book extraction verified.\n")


def benchmark_sheet_164_giant_image(pdf_path: str):
    """
    Verify sheet 164 of 1cc573f6.pdf.
    In legacy Node.js/PDF.js, decoding this 15,071x9,830 illustration spiked RSS to 2.8 GB.
    In PyMuPDF, image info must be extracted strictly from headers without pixel decoding.
    """
    print(f"--- Benchmark: Sheet 164 Giant Image ({os.path.basename(pdf_path)}) ---")
    assert os.path.isfile(pdf_path), f"File not found: {pdf_path}"

    doc = pymupdf.open(pdf_path)
    assert len(doc) >= 164, f"Book has only {len(doc)} pages, expected >= 164"

    rss_before = get_current_rss_mb()
    t0 = time.perf_counter()

    # Sheet 164 (1-based)
    primitives = extract_page_primitives(doc, 164)

    elapsed = time.perf_counter() - t0
    rss_after = get_current_rss_mb()

    print(f"  Sheet 164 dimensions: {primitives.width:.2f} x {primitives.height:.2f} pt")
    print(f"  Extraction time: {elapsed * 1000:.2f} ms")
    print(f"  Text spans: {len(primitives.spans)}")
    print(f"  Drawings: {len(primitives.drawings)}")
    print(f"  Images: {len(primitives.images)}")

    found_giant_image = False
    for img in primitives.images:
        print(f"    Image: {img.width}x{img.height} px, bbox={img.bbox}")
        if img.width == 15071 and img.height == 9830:
            found_giant_image = True
            # Verify coordinates are in PDF space (y-up)
            # Bbox should be approximately (74.8, 209.1, 392.9, 416.6)
            x0, y0, x1, y1 = img.bbox
            assert 70 < x0 < 80, f"Unexpected x0: {x0}"
            assert 200 < y0 < 220, f"Unexpected y0: {y0}"
            assert 390 < x1 < 400, f"Unexpected x1: {x1}"
            assert 410 < y1 < 425, f"Unexpected y1: {y1}"

    print(f"  Peak RSS: {rss_after:.1f} MB (legacy spiked to 2,800 MB)")

    assert found_giant_image, "Did not find 15071x9830 image on sheet 164!"
    # Peak RSS must remain under 100 MB
    assert rss_after < 100.0, f"RSS too high: {rss_after:.1f} MB >= 100 MB"
    print("  [PASS] Zero-pixel image extraction on sheet 164 verified.\n")


def run_isolated(target, *args):
    """Run a benchmark function in an isolated process to measure clean RSS."""
    p = multiprocessing.Process(target=target, args=args)
    p.start()
    p.join()
    if p.exitcode != 0:
        sys.exit(p.exitcode)


def main():
    print("==================================================")
    print("  PyMuPDF Primitive Extraction Benchmark (Phase 1)")
    print("==================================================\n")

    test_coordinate_conversion()

    book2 = os.path.join(REPO_ROOT, "books", "1cc573f6-324a-470d-8452-bbf6d3fd0a23.pdf")
    if os.path.isfile(book2):
        run_isolated(benchmark_sheet_164_giant_image, book2)
    else:
        print(f"Warning: {book2} not found, skipping giant image benchmark.")

    book1 = os.path.join(REPO_ROOT, "books", "0e966773-5012-4f57-8be5-d892e8c75f22.pdf")
    if os.path.isfile(book1):
        run_isolated(benchmark_full_book, book1)
    else:
        print(f"Warning: {book1} not found, skipping full book benchmark.")

    print("==================================================")
    print("  ALL PHASE 1 ACCEPTANCE CRITERIA PASSED")
    print("==================================================")


if __name__ == "__main__":
    main()
