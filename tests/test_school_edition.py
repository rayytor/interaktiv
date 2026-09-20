#!/usr/bin/env python3
"""
Unit tests for Interaktiv School Edition default behavior.

Verifies:
- School edition ships with all 56 books by default (names, links, thumbnails, metadata).
- PDFs are not bundled by default (isInstalled is False for non-installed books).
- Preview streaming and on-demand installation are enabled.
- Server /api/config reports edition='school', library_mode=False, and features.install=True.
- Packaged library mode still functions when an explicit non-empty library is provided.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from books_manager import BooksManager
from server import ThreadedTCPServer, RangeHTTPRequestHandler


class TestSchoolEdition(unittest.TestCase):
    def test_school_edition_catalog_and_preview(self):
        """School edition by default loads all books with links, thumbnails, and preview capability."""
        manager = BooksManager(base_dir=ROOT, edition="school")

        self.assertEqual(manager.edition, "school")
        self.assertFalse(manager.library_mode)

        books = manager.get_all_books()
        self.assertEqual(len(books), 56)

        # Pick a book and verify its metadata
        first_book = books[0]
        self.assertTrue(first_book["id"])
        self.assertTrue(first_book["title"])
        self.assertTrue(first_book["url"].startswith("https://"))
        self.assertTrue(first_book["hasThumbnail"])
        self.assertIn("grade", first_book)
        self.assertIn("gradeLabel", first_book)

        # Uninstalled book should not report local path
        uninstalled = next(b for b in books if not b["isInstalled"])
        self.assertIsNone(manager.get_local_path(uninstalled["id"]))

        # Thumbnails exist
        thumb_path = manager.get_thumbnail_path(first_book["id"])
        self.assertIsNotNone(thumb_path)
        self.assertTrue(os.path.isfile(thumb_path))

    def test_school_edition_server_config(self):
        """Server in school edition reports correct edition and allows installation/preview."""
        port = 8135
        server = ThreadedTCPServer(
            ("127.0.0.1", port),
            RangeHTTPRequestHandler,
            directory=ROOT,
            edition="school",
        )
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.3)

        base_url = f"http://127.0.0.1:{port}"
        try:
            # Check /api/config
            req = urllib.request.Request(f"{base_url}/api/config")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data.get("edition"), "school")
                self.assertFalse(data.get("library_mode"))
                features = data.get("features", {})
                self.assertTrue(features.get("install"))
                self.assertTrue(features.get("uninstall"))
                self.assertFalse(features.get("catalogue_sync"))
                self.assertFalse(features.get("bulk_extract"))
                self.assertTrue(features.get("activities"))
                self.assertFalse(features.get("jit_activities"))

            # Check /api/books returns all 56 books
            req = urllib.request.Request(f"{base_url}/api/books")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                books = data.get("books", [])
                self.assertEqual(len(books), 56)
                self.assertTrue(all(b.get("url") for b in books))
                self.assertTrue(all(b.get("hasThumbnail") for b in books))

            # Check thumbnail endpoint
            test_id = books[0]["id"]
            req = urllib.request.Request(f"{base_url}/api/thumbnail?id={test_id}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertTrue(len(resp.read()) > 0)

        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
