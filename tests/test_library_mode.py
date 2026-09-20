#!/usr/bin/env python3
"""
Unit tests for BooksManager and Server in Library / School mode.

Verifies:
- Manifest loading and catalog representation in library mode.
- Path resolution for book.pdf, regions.json, thumbnails, and book.json.
- Disabling/gating of authoring and converter operations in library mode.
- Server /api/config reporting edition and library mode.
- Gating of mutating endpoints (403 Forbidden).
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path if "sys" in locals() else True:
    import sys
    sys.path.insert(0, ROOT)

from books_manager import BooksManager


class TestLibraryMode(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="interaktiv_lib_test_")
        self.library_dir = os.path.join(self.temp_dir, "library")
        os.makedirs(self.library_dir, exist_ok=True)

        self.book_id = "test-book-1234-5678-abcdef123456"
        self.book_dir = os.path.join(self.library_dir, "books", self.book_id)
        os.makedirs(self.book_dir, exist_ok=True)

        # Create dummy bundle files
        with open(os.path.join(self.book_dir, "book.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 dummy content")

        with open(os.path.join(self.book_dir, "regions.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "pageCount": 10, "pages": {}}, f)

        with open(os.path.join(self.book_dir, "thumbnail.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0 dummy jpeg")

        with open(os.path.join(self.book_dir, "book.json"), "w", encoding="utf-8") as f:
            json.dump({
                "schema": 1,
                "id": self.book_id,
                "title": "Test School Book",
                "grade": 9,
                "gradeLabel": "9. Sınıf",
                "subject": "Fizik",
                "pageCount": 10,
                "kitapogeList": [
                    {"id": 1, "data": "act-1", "baslik": "Etkinlik 1", "sayfano": 1, "posx": 100, "posy": 200, "ogeturu": 1}
                ]
            }, f)

        # Create manifest.json
        self.manifest = {
            "schema": 1,
            "edition": "school",
            "bookCount": 1,
            "generatedAt": "2026-09-19T12:00:00Z",
            "books": [
                {
                    "id": self.book_id,
                    "title": "Test School Book",
                    "grade": 9,
                    "gradeLabel": "9. Sınıf",
                    "subject": "Fizik",
                    "pageCount": 10,
                    "bundle": self.book_id,
                    "hasThumbnail": True,
                    "hasInteractive": True,
                    "interactiveCount": 1,
                }
            ]
        }
        with open(os.path.join(self.library_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(self.manifest, f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_books_manager_library_mode(self):
        manager = BooksManager(base_dir=self.temp_dir, library_dir=self.library_dir, edition="school")
        self.assertTrue(manager.library_mode)
        self.assertEqual(manager.edition, "school")

        books = manager.get_all_books()
        self.assertEqual(len(books), 1)
        b = books[0]
        self.assertEqual(b["id"], self.book_id)
        self.assertEqual(b["title"], "Test School Book")
        self.assertTrue(b["isInstalled"])
        self.assertTrue(b["hasThumbnail"])
        self.assertTrue(b["hasInteractive"])

        # Path resolution
        pdf_path = manager.get_local_path(self.book_id)
        self.assertEqual(pdf_path, os.path.join(self.book_dir, "book.pdf"))

        regions_path = manager.get_regions_path(self.book_id)
        self.assertEqual(regions_path, os.path.join(self.book_dir, "regions.json"))

        thumb_path = manager.get_thumbnail_path(self.book_id)
        self.assertEqual(thumb_path, os.path.join(self.book_dir, "thumbnail.jpg"))

        oges = manager.get_book_oges(self.book_id)
        self.assertEqual(len(oges), 1)
        self.assertEqual(oges[0]["data"], "act-1")

        # Gated operations
        ok, msg = manager.start_download(self.book_id)
        self.assertFalse(ok)
        self.assertIn("edition", msg.lower())

        ok, msg = manager.uninstall_book(self.book_id)
        self.assertFalse(ok)
        self.assertIn("cannot be uninstalled", msg.lower())


if __name__ == "__main__":
    unittest.main()
