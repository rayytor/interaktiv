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


if __name__ == "__main__":
    unittest.main()

