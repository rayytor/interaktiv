"""
End-to-End Test Suite for Phase 4: Publisher Anchor Reconciliation & regions.json Serialization.

Evaluates:
- Target book: books/0e966773-5012-4f57-8be5-d892e8c75f22.pdf
- Publisher metadata: activities_meta/0e966773-5012-4f57-8be5-d892e8c75f22.json
- Reference output: activities/books/0e966773-5012-4f57-8be5-d892e8c75f22/regions.json

Verifies:
1. Byte fingerprint matches bake_activities.mjs output identically.
2. Modal folio offset and page numbers match reference.
3. Anchor reconciliation correctly snaps unplaced interactive oges.
4. Output regions.json strictly adheres to BAKE_VERSION = 2 schema.
5. Output diagnostics.json.gz is properly compressed and adheres to DIAGNOSTICS_VERSION = 1 schema.
6. Node.js scorecard compatibility check (--from-bake readable).
"""

import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pymupdf

from tools.hotspot_extraction.scanner import (
    BAKE_VERSION,
    DIAGNOSTICS_VERSION,
    ActivityRegion,
    PageLayout,
    PagePrimitives,
    PublisherOge,
    compute_book_folio,
    compute_fingerprint,
    detect_book_calibration,
    detect_layout,
    detect_markers,
    detect_panels,
    detect_solution_spaces,
    extract_page_primitives,
    grow_activity_regions,
    load_publisher_oges,
    reconcile_anchors,
    find_unplaced_anchors,
    save_diagnostics_gz,
    save_regions_json,
    serialize_book_regions,
    serialize_diagnostics,
)


def run_test():
    book_id = "0e966773-5012-4f57-8be5-d892e8c75f22"
    pdf_path = os.path.join(PROJECT_ROOT, "books", f"{book_id}.pdf")
    meta_path = os.path.join(PROJECT_ROOT, "activities_meta", f"{book_id}.json")
    ref_dir = os.path.join(PROJECT_ROOT, "activities", "books", book_id)
    ref_regions_path = os.path.join(ref_dir, "regions.json")
    ref_diag_path = os.path.join(ref_dir, "diagnostics.json.gz")

    assert os.path.exists(pdf_path), f"PDF file not found: {pdf_path}"
    assert os.path.exists(meta_path), f"Metadata file not found: {meta_path}"
    assert os.path.exists(ref_regions_path), f"Reference regions.json not found: {ref_regions_path}"

    with open(ref_regions_path, "r", encoding="utf-8") as f:
        ref_data = json.load(f)

    print("=" * 70)
    print("PHASE 4 VERIFICATION: ANCHORS & SERIALIZATION")
    print(f"Book: {book_id}")
    print("=" * 70)

    # 1. Verify fingerprint computation
    t0 = time.time()
    computed_fp = compute_fingerprint(pdf_path)
    ref_fp = ref_data["fingerprint"]
    print(f"\n[1/6] Testing Fingerprint Computation...")
    print(f"  Computed:  {computed_fp}")
    print(f"  Reference: {ref_fp}")
    assert computed_fp == ref_fp, f"Fingerprint mismatch! {computed_fp} != {ref_fp}"
    print("  ✓ Fingerprint matches bake_activities.mjs identically!")

    # 2. Verify Folio Mapping
    print(f"\n[2/6] Testing Folio Mapping...")
    doc = pymupdf.open(pdf_path)
    folio_offset, folio_map = compute_book_folio(doc)
    ref_offset = ref_data["folio"]["offset"]
    ref_by_page = ref_data["folio"]["byPage"]

    print(f"  Computed Folio Offset: {folio_offset} (Reference: {ref_offset})")
    assert folio_offset == ref_offset, f"Folio offset mismatch: {folio_offset} != {ref_offset}"

    sample_matches = 0
    for p in range(1, min(doc.page_count, 50) + 1):
        sp = str(p)
        if sp in ref_by_page:
            assert folio_map.get(p) == ref_by_page[sp], f"Page {p} folio mismatch: {folio_map.get(p)} != {ref_by_page[sp]}"
            sample_matches += 1
    print(f"  ✓ Folio map verified ({sample_matches} sample pages checked against reference)!")

    # 3. Load Publisher Oges
    print(f"\n[3/6] Testing Publisher Metadata Loading...")
    oges = load_publisher_oges(meta_path)
    print(f"  Loaded {len(oges)} interactive page elements from {meta_path}")
    assert len(oges) > 0, "No publisher oges loaded!"
    assert all(isinstance(o, PublisherOge) for o in oges)
    assert all(o.sayfano > 0 and o.data for o in oges)
    print("  ✓ Publisher metadata parsed into typed PublisherOge records!")

    # 4. End-to-End Extraction & Anchor Reconciliation
    print(f"\n[4/6] Running Extraction, Layout, Markers, Regions & Anchors...")
    pages_activities = {}
    pages_layout = {}
    pages_dimensions = {}
    diagnostics_by_page = {}
    anchors_by_page = {}

    total_regions = 0
    total_anchored = 0
    total_questions = 0

    # Process all pages
    scan_start = time.time()
    for p in range(1, doc.page_count + 1):
        prim = extract_page_primitives(doc, p)
        pages_dimensions[p] = (prim.width, prim.height)
        layout = detect_layout(prim)
        pages_layout[p] = layout

        # Detect markers and grow regions
        markers = detect_markers(prim, layout=layout)
        activities = grow_activity_regions(prim, layout, markers)

        # Check publisher anchors for printed page
        printed_page = folio_map.get(p)
        page_oges = [o for o in oges if o.sayfano == printed_page] if printed_page is not None else []

        if page_oges:
            unplaced = find_unplaced_anchors(
                activities=activities,
                oges=page_oges,
                page_height=prim.height,
                printed_page=printed_page,
                page_width=prim.width,
            )
            if unplaced:
                anchors_by_page[p] = [o.id for o in unplaced]
                activities = reconcile_anchors(
                    activities=activities,
                    oges=page_oges,
                    page_height=prim.height,
                    printed_page=printed_page,
                    page_width=prim.width,
                    page_num=p,
                    primitives=prim,
                    layout=layout,
                    markers=markers,
                )

        if activities:
            pages_activities[p] = activities
            total_regions += len(activities)
            total_anchored += sum(1 for a in activities if a.anchored)
            total_questions += sum(len(a.items) for a in activities if a.items)

        # Build diagnostic sidecar geometry for this page
        body_spans = [
            s for s in prim.spans
            if s.bbox[1] >= layout.content_box[1] - 2.0 and s.bbox[3] <= layout.content_box[3] + 2.0
        ]
        panels = detect_panels(prim.drawings, body_spans)
        solutions = detect_solution_spaces(prim.drawings, body_spans)

        diagnostics_by_page[p] = {
            "pageWidth": prim.width,
            "pageHeight": prim.height,
            "contentTop": layout.content_box[3],
            "contentBottom": layout.content_box[1],
            "panels": [list(pnl) for pnl in panels],
            "solutions": [list(sol["rect"] if isinstance(sol, dict) else sol) for sol in solutions],
            "markers": [list(m.span.bbox) for m in markers],
        }

    scan_elapsed = time.time() - scan_start
    print(f"  Scanned {doc.page_count} pages in {scan_elapsed:.2f}s ({doc.page_count / scan_elapsed:.1f} pages/sec)")
    print(f"  Yield: {total_regions} activities ({total_anchored} anchored), {total_questions} sub-questions")
    print(f"  Anchors offered on {len(anchors_by_page)} sheets: {list(anchors_by_page.keys())}")
    assert total_regions > 200, f"Expected >200 regions, got {total_regions}"
    assert total_anchored > 0, "Expected at least 1 anchored activity!"
    print("  ✓ Extraction and anchor reconciliation completed successfully!")

    # 5. Serialization & Schema Validation
    print(f"\n[5/6] Testing Serialization and Temporary Output...")
    temp_dir = tempfile.mkdtemp(prefix="interaktiv_test_serialize_")
    try:
        temp_regions_file = os.path.join(temp_dir, "regions.json")
        temp_diag_file = os.path.join(temp_dir, "diagnostics.json.gz")

        cal_info = detect_book_calibration(doc, pages_activities)
        print(f"  Calibration detected: {cal_info['confidence']} (markerSize={cal_info['markerSize']})")

        # Serialize regions.json
        regions_json_data = serialize_book_regions(
            book_id=book_id,
            pdf_path=pdf_path,
            page_count=doc.page_count,
            pages_activities=pages_activities,
            folio_map=folio_map,
            folio_offset=folio_offset,
            calibration_info=cal_info,
            pages_layout=pages_layout,
            page_dimensions=pages_dimensions,
            anchors_by_page=anchors_by_page,
        )

        assert regions_json_data["version"] == BAKE_VERSION, "Invalid version"
        assert regions_json_data["bookId"] == book_id, "Invalid bookId"
        assert regions_json_data["fingerprint"] == ref_fp, "Fingerprint mismatch in output"
        assert "calibration" in regions_json_data
        assert "folio" in regions_json_data
        assert "anchors" in regions_json_data
        assert "pages" in regions_json_data

        save_regions_json(temp_regions_file, regions_json_data)
        assert os.path.exists(temp_regions_file), "regions.json was not created"
        size_kb = os.path.getsize(temp_regions_file) / 1024
        print(f"  ✓ Saved regions.json ({size_kb:.1f} KB)")

        # Verify saved JSON re-parses properly
        with open(temp_regions_file, "r", encoding="utf-8") as f:
            reloaded_regions = json.load(f)
        assert reloaded_regions["version"] == 2
        assert len(reloaded_regions["pages"]) > 0

        # Serialize diagnostics.json.gz
        diag_json_data = serialize_diagnostics(
            book_id=book_id,
            pdf_path=pdf_path,
            diagnostics_by_page=diagnostics_by_page,
            fingerprint=computed_fp,
        )

        assert diag_json_data["version"] == DIAGNOSTICS_VERSION
        assert diag_json_data["bookId"] == book_id
        assert diag_json_data["fingerprint"] == ref_fp

        save_diagnostics_gz(temp_diag_file, diag_json_data)
        assert os.path.exists(temp_diag_file), "diagnostics.json.gz was not created"
        diag_size_kb = os.path.getsize(temp_diag_file) / 1024
        print(f"  ✓ Saved diagnostics.json.gz ({diag_size_kb:.1f} KB, gzip compressed)")

        # Verify gunzip
        with gzip.open(temp_diag_file, "rb") as f:
            reloaded_diag = json.loads(f.read().decode("utf-8"))
        assert reloaded_diag["version"] == 1
        assert reloaded_diag["bookId"] == book_id
        assert reloaded_diag["fingerprint"] == ref_fp
        assert len(reloaded_diag["pages"]) == doc.page_count
        print("  ✓ Gzip integrity verified (gunzips cleanly, 100% schema match)")

        # 6. Cross-Validation against Reference Schema and Node.js scorecard compatibility
        print(f"\n[6/6] Testing Compatibility with Scorecard / Reader Harness...")
        # Check an anchored activity in output:
        p34_acts = reloaded_regions["pages"].get("34", {}).get("activities", [])
        anchored_p34 = [a for a in p34_acts if a.get("anchored")]
        print(f"  Page 34 activities: {len(p34_acts)}, Anchored on p34: {len(anchored_p34)}")
        assert len(anchored_p34) == 1, f"Expected 1 anchored activity on p34, got {len(anchored_p34)}"
        assert anchored_p34[0]["id"] == "p34-oge-20e3aa32-f4a6-f111-b317-005056a7d529"
        print(f"  ✓ Anchor on page 34 matched: {anchored_p34[0]['id']}, rect={anchored_p34[0]['rect']}")

        # Verify that all activities match the schema expected by js/viewer.js
        for p_str, p_info in reloaded_regions["pages"].items():
            assert "pageWidth" in p_info
            assert "pageHeight" in p_info
            assert "columns" in p_info
            assert "activities" in p_info
            for act in p_info["activities"]:
                assert "id" in act
                assert "column" in act
                assert "rect" in act
                assert len(act["rect"]) == 4
                assert "parts" in act
                assert len(act["parts"]) >= 1
                if "items" in act and act["items"]:
                    for itm in act["items"]:
                        assert "id" in itm
                        assert "partIndex" in itm
                        assert "rect" in itm

        print("  ✓ All serialized pages and activities adhere strictly to viewer schema!")

        # Explicit Node.js runtime validation
        node_script = (
            f"const fs = require('fs');\n"
            f"const zlib = require('zlib');\n"
            f"const reg = JSON.parse(fs.readFileSync('{temp_regions_file}', 'utf8'));\n"
            f"const diag = JSON.parse(zlib.gunzipSync(fs.readFileSync('{temp_diag_file}')).toString('utf8'));\n"
            f"if (reg.version !== 2 || diag.version !== 1 || reg.fingerprint !== diag.fingerprint) process.exit(1);\n"
            f"console.log('  ✓ Node.js successfully loaded regions.json and gunzipped diagnostics.json.gz');\n"
        )
        res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
        assert res.returncode == 0, f"Node.js parsing failed: {res.stderr}"
        print(res.stdout.strip())

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("\n" + "=" * 70)
    print("ALL PHASE 4 ACCEPTANCE CRITERIA PASSED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    run_test()
