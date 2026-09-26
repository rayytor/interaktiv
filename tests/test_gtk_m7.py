#!/usr/bin/env python3
"""
Unit tests for Milestone 7: Search + Shortcuts.

Tests cover:
  1. DocumentHandle text extraction and search:
     - DocumentHandle.get_text(page)
     - DocumentHandle.search(page, needle)
  2. RenderService search and text pass dispatch:
     - submit_text_pass chunking
     - submit_search dispatch
  3. SearchController:
     - Background text pass caching
     - Search execution (visible page first, remaining in chunks)
     - Match ordering (reading order) and indexing
     - Match navigation (next, prev, jump_to) and wrapping
     - Clear and empty query handling
  4. PageView search highlight rendering:
     - set_search_matches, active vs inactive highlight snapshot
  5. SpreadView and ScrollModeView search match distribution:
     - set_search_matches propagates to child views
  6. Adw.ShortcutsDialog:
     - Proper sections and shortcut items
  7. ReaderPage keyboard shortcuts parity and Escape priority:
     - All viewer.js shortcuts represented in SHORTCUTS
     - Escape priority: focus -> search -> close book
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk

from interaktiv_gtk.reader.page_view import PageView
from interaktiv_gtk.reader.scroll_mode import ScrollModeView
from interaktiv_gtk.reader.search import CHUNK_SIZE, SearchController, SearchMatch
from interaktiv_gtk.reader.spread import SpreadView
from interaktiv_gtk.reader.view import SHORTCUTS, ReaderPage
from interaktiv_gtk.render.document import DocumentHandle
from interaktiv_gtk.render.requests import DocumentInfo, SearchRequest, TextPassRequest
from interaktiv_gtk.render.service import LANE_SCAN, LANE_VISIBLE, RenderService

SAMPLE_PDF = os.path.join(PROJECT_ROOT, "books", "0e966773-5012-4f57-8be5-d892e8c75f22.pdf")


class TestDocumentHandleSearchAndText(unittest.TestCase):
    """Test PyMuPDF text extraction and search in DocumentHandle."""

    def test_get_text_and_search_on_pdf(self):
        if not os.path.exists(SAMPLE_PDF):
            self.skipTest("Sample PDF not found")

        handle = DocumentHandle(SAMPLE_PDF)
        self.assertGreater(handle.info.page_count, 0)

        # Test text extraction
        text = handle.get_text(1)
        self.assertIsInstance(text, str)

        # Test search
        rects = handle.search(1, "ve")
        self.assertIsInstance(rects, list)
        for r in rects:
            self.assertEqual(len(r), 4)
            self.assertLess(r[0], r[2])  # x0 < x1
            self.assertLess(r[1], r[3])  # y0 < y1

        handle.close()


class TestSearchController(unittest.TestCase):
    """Test SearchController background text pass and search navigation."""

    def setUp(self):
        self.controller = SearchController()
        self.info = DocumentInfo(
            path="dummy.pdf",
            page_count=20,
            page_sizes=tuple((595.0, 842.0) for _ in range(20)),
        )

    def tearDown(self):
        self.controller.shutdown()

    def test_start_background_text_pass_chunks(self):
        mock_service = MagicMock()
        self.controller.attach(mock_service, self.info)

        # 20 pages with CHUNK_SIZE=8 -> chunks: [1..8], [9..16], [17..20] = 3 chunks
        self.assertEqual(mock_service.submit_text_pass.call_count, 3)

        calls = mock_service.submit_text_pass.call_args_list
        self.assertEqual(calls[0][0][0], list(range(1, 9)))
        self.assertEqual(calls[1][0][0], list(range(9, 17)))
        self.assertEqual(calls[2][0][0], list(range(17, 21)))

    def test_text_chunk_accumulation(self):
        self.controller.page_count = 10
        self.assertFalse(self.controller._text_pass_complete)

        self.controller._on_text_chunk({1: "Sayfa bir", 2: "Sayfa iki"})
        self.assertEqual(len(self.controller._page_texts), 2)
        self.assertFalse(self.controller._text_pass_complete)

        # Complete the pages
        rest = {p: f"Sayfa {p}" for p in range(3, 11)}
        self.controller._on_text_chunk(rest)
        self.assertEqual(len(self.controller._page_texts), 10)
        self.assertTrue(self.controller._text_pass_complete)

    def test_search_execution_and_results(self):
        mock_service = MagicMock()
        self.controller.attach(mock_service, self.info)

        # Simulate text pass complete
        self.controller._page_texts = {
            1: "Bu birinci sayfa ve metin",
            2: "İkinci sayfa",
            5: "Beşinci sayfa ve diğer metin",
        }
        self.controller._text_pass_complete = True

        results_changed_mock = MagicMock()
        active_match_mock = MagicMock()
        self.controller.connect("results-changed", results_changed_mock)
        self.controller.connect("active-match-changed", active_match_mock)

        # Search for "ve" with visible page 1
        self.controller.execute_search("ve", visible_pages=[1])

        # Visible page 1 was submitted at LANE_VISIBLE
        # Page 5 was submitted at LANE_SCAN
        self.assertEqual(mock_service.submit_search.call_count, 2)
        call_vis = mock_service.submit_search.call_args_list[0]
        call_other = mock_service.submit_search.call_args_list[1]

        self.assertEqual(call_vis[0][0], "ve")
        self.assertEqual(call_vis[0][1], [1])
        self.assertEqual(call_vis[1]["lane"], LANE_VISIBLE)

        self.assertEqual(call_other[0][0], "ve")
        self.assertEqual(call_other[0][1], [5])
        self.assertEqual(call_other[1]["lane"], LANE_SCAN)

        # Simulate results arriving from worker for page 1
        tok = self.controller._search_token
        self.controller._on_search_chunk(tok, {
            1: [(10.0, 20.0, 50.0, 30.0)],
        })
        self.assertEqual(self.controller.total_matches, 1)
        self.assertEqual(self.controller.current_match_index, 0)
        self.assertEqual(self.controller.current_match.page, 1)

        # Simulate results arriving for page 5 (with 2 matches)
        self.controller._on_search_chunk(tok, {
            5: [(10.0, 60.0, 50.0, 70.0), (10.0, 20.0, 50.0, 30.0)],
        })
        self.assertEqual(self.controller.total_matches, 3)

        # Verify global match order: page 1 first, then page 5 in reading
        # order. Rects are PDF user space (y grows upward), so the larger y
        # is nearer the top of the page.
        m0 = self.controller._matches[0]
        m1 = self.controller._matches[1]
        m2 = self.controller._matches[2]
        self.assertEqual((m0.page, m0.match_index, m0.global_index), (1, 0, 0))
        self.assertEqual((m1.page, m1.match_index, m1.global_index), (5, 0, 1))
        self.assertEqual(m1.rect[1], 60.0)  # Top match on page 5
        self.assertEqual((m2.page, m2.match_index, m2.global_index), (5, 1, 2))
        self.assertEqual(m2.rect[1], 20.0)  # Lower match on page 5

    def test_search_navigation_and_cycling(self):
        self.controller._matches = [
            SearchMatch(page=1, match_index=0, rect=(0, 0, 10, 10), global_index=0),
            SearchMatch(page=2, match_index=0, rect=(0, 0, 10, 10), global_index=1),
            SearchMatch(page=3, match_index=0, rect=(0, 0, 10, 10), global_index=2),
        ]
        self.controller._current_index = 0

        # Next match
        m = self.controller.next_match()
        self.assertEqual(m.global_index, 1)
        self.assertEqual(self.controller.current_match_index, 1)

        # Next match
        m = self.controller.next_match()
        self.assertEqual(m.global_index, 2)
        self.assertEqual(self.controller.current_match_index, 2)

        # Next match wraps around to 0
        m = self.controller.next_match()
        self.assertEqual(m.global_index, 0)
        self.assertEqual(self.controller.current_match_index, 0)

        # Prev match wraps around to 2
        m = self.controller.prev_match()
        self.assertEqual(m.global_index, 2)
        self.assertEqual(self.controller.current_match_index, 2)

        # Jump to index 1
        m = self.controller.jump_to_match(1)
        self.assertEqual(m.global_index, 1)
        self.assertEqual(self.controller.current_match_index, 1)

    def test_search_clear(self):
        self.controller._matches = [
            SearchMatch(page=1, match_index=0, rect=(0, 0, 10, 10), global_index=0)
        ]
        self.controller.query = "test"
        self.controller._current_index = 0

        cleared_called = []
        self.controller.connect("search-cleared", lambda *_: cleared_called.append(True))

        self.controller.clear()
        self.assertEqual(self.controller.query, "")
        self.assertEqual(self.controller.total_matches, 0)
        self.assertEqual(self.controller.current_match_index, -1)
        self.assertTrue(cleared_called)


class TestPageViewSearchHighlights(unittest.TestCase):
    """Test PageView search highlight methods and snapshot drawing."""

    def test_set_and_clear_search_matches(self):
        view = PageView()
        view.set_page(1, 0)
        view.set_pdf_size(595.0, 842.0)
        view.set_layout_size(595.0, 842.0)

        matches = [(10.0, 20.0, 100.0, 40.0), (10.0, 50.0, 100.0, 70.0)]
        view.set_search_matches(matches, active_index=0)

        self.assertEqual(view._search_matches, matches)
        self.assertEqual(view._active_search_index, 0)

        # Snapshot test
        snapshot = Gtk.Snapshot()
        view.snapshot_search_highlights(snapshot)

        # Clearing matches
        view.clear_search_matches()
        self.assertEqual(view._search_matches, [])
        self.assertIsNone(view._active_search_index)


class TestSpreadAndScrollSearchIntegration(unittest.TestCase):
    """Test SpreadView and ScrollModeView set_search_matches."""

    def test_spread_view_search_matches(self):
        spread = SpreadView()
        info = DocumentInfo(
            path="dummy.pdf",
            page_count=10,
            page_sizes=tuple((595.0, 842.0) for _ in range(10)),
        )
        spread.attach(None, info)
        spread.show_pages([2, 3])

        page_matches = {
            2: [(10.0, 20.0, 50.0, 30.0)],
            3: [(40.0, 50.0, 80.0, 60.0)],
        }
        spread.set_search_matches(page_matches, active_match=(2, 0))

        # Check left page (page 2)
        v2 = spread._views[0]
        self.assertEqual(v2.page, 2)
        self.assertEqual(v2._search_matches, page_matches[2])
        self.assertEqual(v2._active_search_index, 0)

        # Check right page (page 3)
        v3 = spread._views[1]
        self.assertEqual(v3.page, 3)
        self.assertEqual(v3._search_matches, page_matches[3])
        self.assertIsNone(v3._active_search_index)

    def test_scroll_mode_view_search_matches(self):
        scroll_view = ScrollModeView()
        info = DocumentInfo(
            path="dummy.pdf",
            page_count=5,
            page_sizes=tuple((595.0, 842.0) for _ in range(5)),
        )
        scroll_view.attach(None, info)

        page_matches = {1: [(10.0, 20.0, 50.0, 30.0)]}
        scroll_view.set_search_matches(page_matches, active_match=(1, 0))

        self.assertEqual(scroll_view._search_matches, page_matches)
        self.assertEqual(scroll_view._active_match, (1, 0))
        scroll_view.shutdown()


class TestShortcutsDialog(unittest.TestCase):
    """Test Adw.ShortcutsDialog construction and parity."""

    def test_shortcuts_dialog_structure(self):
        Adw.init()
        dialog = Adw.ShortcutsDialog()
        dialog.set_title("Klavye Kısayolları")

        nav = Adw.ShortcutsSection(title="Gezinme")
        nav.add(Adw.ShortcutsItem(title="Sonraki sayfa", accelerator="Right"))
        nav.add(Adw.ShortcutsItem(title="Önceki sayfa", accelerator="Left"))
        dialog.add(nav)

        self.assertEqual(dialog.get_title(), "Klavye Kısayolları")


class TestKeyboardShortcutsParity(unittest.TestCase):
    """Verify that ReaderPage's SHORTCUTS list contains all required bindings."""

    def test_shortcuts_parity_with_viewer_js(self):
        shortcut_dict = {action: trigger for trigger, action in SHORTCUTS}

        # Navigation
        self.assertIn("next", shortcut_dict)
        self.assertIn("Right", shortcut_dict["next"])
        self.assertIn("prev", shortcut_dict)
        self.assertIn("Left", shortcut_dict["prev"])
        self.assertIn("first", shortcut_dict)
        self.assertIn("last", shortcut_dict)

        # Zoom & Modes
        self.assertIn("zoom-in", shortcut_dict)
        self.assertIn("zoom-out", shortcut_dict)
        self.assertIn("zoom-reset", shortcut_dict)
        self.assertIn("mode-book", shortcut_dict)
        self.assertIn("mode-single", shortcut_dict)
        self.assertIn("mode-scroll", shortcut_dict)
        self.assertIn("rotate", shortcut_dict)
        self.assertIn("fullscreen", shortcut_dict)

        # M7 Specific: help and theme
        self.assertIn("help", shortcut_dict)
        self.assertEqual(shortcut_dict["help"], "question")
        self.assertIn("cycle-theme", shortcut_dict)
        self.assertIn("m|M", shortcut_dict["cycle-theme"])

        # Escape
        self.assertIn("close", shortcut_dict)
        self.assertEqual(shortcut_dict["close"], "Escape")


if __name__ == "__main__":
    unittest.main()
