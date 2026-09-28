"""Phase 1 tests for render.py. Run with the vision venv from the project root:

    .venv-vision/bin/python -m pytest tools/hotspot_extraction/vision/test_render.py -q
"""
import json
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from tools.hotspot_extraction.vision import render

BOOK = "0a3fbb41-8855-460f-af3d-41489a269851"
PDF = render.DEFAULT_BOOKS_DIR / f"{BOOK}.pdf"
PAGE = 31

pytestmark = pytest.mark.skipif(not PDF.is_file(), reason="book PDF not installed")


def _render_twice(tmp_path: Path):
    doc = pymupdf.open(str(PDF))
    try:
        page = doc[PAGE - 1]
        a = tmp_path / "a.jpg"
        b = tmp_path / "b.jpg"
        rec_a = render.render_page_to_file(page, a)
        rec_b = render.render_page_to_file(page, b)
    finally:
        doc.close()
    return a, b, rec_a, rec_b


def test_render_is_deterministic(tmp_path):
    a, b, rec_a, rec_b = _render_twice(tmp_path)
    assert a.read_bytes() == b.read_bytes()
    assert rec_a == rec_b


def test_index_matches_jpeg_size(tmp_path):
    a, _, rec, _ = _render_twice(tmp_path)
    with Image.open(a) as im:
        assert im.size == (rec["width_px"], rec["height_px"])
        assert im.mode == "RGB"
    assert rec["width_px"] == render.DEFAULT_WIDTH
    assert rec["page"] == PAGE


def test_scale_is_uniform_within_0_1_percent(tmp_path):
    _, _, rec, _ = _render_twice(tmp_path)
    sx = rec["width_px"] / rec["width_pt"]
    sy = rec["height_px"] / rec["height_pt"]
    assert abs(sx - sy) / sx < 0.001


def test_render_book_is_resumable(tmp_path):
    task = {"book_id": BOOK, "pdf_path": str(PDF), "out_dir": str(tmp_path), "pages": "30-31"}
    first = render.render_book(task)
    assert first["status"] == "rendered" and first["rendered"] == 2
    second = render.render_book(task)
    assert second["status"] == "skipped" and second["skipped"] == 2 and second["rendered"] == 0
    lines = [json.loads(l) for l in (tmp_path / BOOK / "index.jsonl").read_text().splitlines()]
    assert [l["page"] for l in lines] == [30, 31]
    assert (tmp_path / BOOK / "0031.jpg").is_file()
    assert (tmp_path / BOOK / "fingerprint.txt").read_text().strip() == render.compute_fingerprint(str(PDF))


def test_discover_folds_duplicate_files():
    books = render.discover_books(render.DEFAULT_BOOKS_DIR)
    ids = [b["book_id"] for b in books]
    assert len(ids) == len(set(ids))
    assert all(render.UUID_RE.fullmatch(i) for i in ids), ids
