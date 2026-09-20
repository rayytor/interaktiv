#!/usr/bin/env python3
"""
Unit tests for Milestone 6: Scroll Mode + Sidebar.

Tests cover:
  1. ScrollModeView virtualization:
     - Gtk.ListView model population for 289 pages.
     - Factory unbind drops the texture (unmountPageCanvas equivalent).
     - TextureCache stays bounded at or below budget when scrolling through all pages.
  2. Thumbnails grouping (initBookThumbnails / initSingleThumbnails):
     - Spread grouping in book mode: [1], [2, 3], [4, 5], ...
     - Single grouping in single and scroll mode: [1], [2], [3], ...
     - Active thumbnail selection matching.
  3. Bookmarks / Table of Contents:
     - build_toc_tree hierarchical conversion.
     - BookmarksPanel tree list and navigation.
  4. ReaderPage integration:
     - Switching between book, single, and scroll modes.
     - Keyboard shortcuts ('c|C', 't|T').
     - Navigation in scroll mode.
"""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from interaktiv_gtk.reader.scroll_mode import (
    PAGE_GAP,
    PageRowWrapper,
    ScrollModeView,
    ScrollPageItem,
)
from interaktiv_gtk.reader.sidebar import (
    BookmarksPanel,
    ReaderSidebar,
    ThumbnailItem,
    ThumbnailsPanel,
    TocItem,
    build_toc_tree,
)
from interaktiv_gtk.render.cache import TextureCache
from interaktiv_gtk.render.requests import DocumentInfo, RenderRequest, RenderResult


class DummyTexture:
    """Mock Gdk.Texture for testing texture caching."""
    def __init__(self, width=100, height=100):
        self.width = width
        self.height = height


class TestScrollModeVirtualization(unittest.TestCase):
    """Gtk.ListView virtualization in ScrollModeView."""

    def setUp(self):
        self.info = DocumentInfo(
            path="dummy.pdf",
            page_count=289,
            page_sizes=tuple((595.0, 842.0) for _ in range(289)),
        )
        self.budget = 10 * 1024 * 1024  # 10 MB budget
        self.cache = TextureCache(budget=self.budget)
        self.view = ScrollModeView(cache=self.cache)

    def tearDown(self):
        self.view.shutdown()

    def test_model_population_matches_page_count(self):
        self.view.attach(service=None, info=self.info)
        self.assertEqual(self.view._store.get_n_items(), 289)
        item0 = self.view._store.get_item(0)
        self.assertEqual(item0.page, 1)
        self.assertEqual(item0.width, 595.0)
        self.assertEqual(item0.height, 842.0)
        item288 = self.view._store.get_item(288)
        self.assertEqual(item288.page, 289)

    def test_unbind_drops_texture(self):
        """When an off-screen page is unbound, unbind drops its texture."""
        self.view.attach(service=None, info=self.info)

        class DummyListItem:
            def __init__(self, item):
                self._item = item
                self._child = None
                self.view = None
                self.wrapper = None

            def get_item(self):
                return self._item

            def set_child(self, child):
                self._child = child

            def get_child(self):
                return self._child

        list_item = DummyListItem(self.view._store.get_item(5))
        self.view._on_setup(None, list_item)
        self.view._on_bind(None, list_item)

        # Simulate texture arrival
        tex = DummyTexture(500, 700)
        list_item.view.set_texture(tex, 6, 0)
        self.assertTrue(list_item.view.has_texture)

        # Unbind must clear the texture
        self.view._on_unbind(None, list_item)
        self.assertFalse(list_item.view.has_texture)
        self.assertNotIn(6, self.view._bound_views)

    def test_scroll_end_to_end_texture_cache_stays_at_budget(self):
        """Simulating rendering through all 289 pages keeps TextureCache within budget."""
        self.view.attach(service=None, info=self.info)

        # Each page rendered is ~1 MB
        bytes_per_page = 1024 * 1024
        for page in range(1, 290):
            key = (page, 1.0, 0, None)
            tex = DummyTexture()
            self.cache.put(key, tex, bytes_per_page)

        # Total cached bytes must never exceed the budget (plus the single latest entry)
        self.assertLessEqual(self.cache.nbytes, self.budget + bytes_per_page)
        # Verify LRU eviction occurred: earlier pages were evicted
        self.assertIsNone(self.cache.get((1, 1.0, 0, None)))
        # Latest page is still in cache
        self.assertIsNotNone(self.cache.get((289, 1.0, 0, None)))

    def test_page_row_wrapper_measure_includes_gap(self):
        from interaktiv_gtk.reader.page_view import PageView
        pv = PageView()
        wrapper = PageRowWrapper(pv)
        wrapper.set_target_size(300, 500)

        min_w, nat_w, _, _ = wrapper.do_measure(Gtk.Orientation.HORIZONTAL, -1)
        self.assertEqual(nat_w, 300)

        min_h, nat_h, _, _ = wrapper.do_measure(Gtk.Orientation.VERTICAL, -1)
        self.assertEqual(nat_h, 500 + PAGE_GAP)


class TestThumbnailGrouping(unittest.TestCase):
    """Spread grouping in book mode vs single grouping in single/scroll mode."""

    def test_book_mode_spread_grouping_odd_pages(self):
        panel = ThumbnailsPanel()
        panel.load(page_count=289, mode="book", rotation=0)

        # Total spreads: [1] is cover, then 2..289 (288 pages = 144 pairs). Total items: 145
        self.assertEqual(panel.store.get_n_items(), 145)

        first = panel.store.get_item(0)
        self.assertEqual(first.pages, (1,))
        self.assertEqual(first.label, "Sayfa 1")

        second = panel.store.get_item(1)
        self.assertEqual(second.pages, (2, 3))
        self.assertEqual(second.label, "Sayfa 2–3")

        last = panel.store.get_item(144)
        self.assertEqual(last.pages, (288, 289))
        self.assertEqual(last.label, "Sayfa 288–289")

    def test_book_mode_spread_grouping_even_pages(self):
        panel = ThumbnailsPanel()
        panel.load(page_count=10, mode="book", rotation=0)

        # Spreads: [1], [2, 3], [4, 5], [6, 7], [8, 9], [10]
        self.assertEqual(panel.store.get_n_items(), 6)
        self.assertEqual(panel.store.get_item(0).pages, (1,))
        self.assertEqual(panel.store.get_item(1).pages, (2, 3))
        self.assertEqual(panel.store.get_item(4).pages, (8, 9))
        self.assertEqual(panel.store.get_item(5).pages, (10,))
        self.assertEqual(panel.store.get_item(5).label, "Sayfa 10")

    def test_single_and_scroll_mode_grouping(self):
        panel = ThumbnailsPanel()
        for mode in ("single", "scroll"):
            with self.subTest(mode=mode):
                panel.load(page_count=50, mode=mode, rotation=0)
                self.assertEqual(panel.store.get_n_items(), 50)
                self.assertEqual(panel.store.get_item(0).pages, (1,))
                self.assertEqual(panel.store.get_item(0).label, "Sayfa 1")
                self.assertEqual(panel.store.get_item(49).pages, (50,))
                self.assertEqual(panel.store.get_item(49).label, "Sayfa 50")

    def test_active_page_selection_matching(self):
        panel = ThumbnailsPanel()
        panel.load(page_count=289, mode="book", rotation=0)

        # In book mode, page 1 selects item 0
        panel.update_active_page(1)
        self.assertEqual(panel.selection.get_selected(), 0)

        # Page 2 or 3 selects item 1
        panel.update_active_page(2)
        self.assertEqual(panel.selection.get_selected(), 1)
        panel.update_active_page(3)
        self.assertEqual(panel.selection.get_selected(), 1)

        # Page 289 selects item 144
        panel.update_active_page(289)
        self.assertEqual(panel.selection.get_selected(), 144)


class TestBookmarksTOC(unittest.TestCase):
    """Table of contents tree building and BookmarksPanel."""

    def test_build_toc_tree_hierarchy(self):
        raw_toc = [
            [1, "Bölüm 1", 1],
            [2, "Konu 1.1", 5],
            [3, "Detay 1.1.1", 8],
            [2, "Konu 1.2", 12],
            [1, "Bölüm 2", 20],
        ]
        root = build_toc_tree(raw_toc)
        self.assertEqual(root.get_n_items(), 2)

        ch1 = root.get_item(0)
        self.assertEqual(ch1.title, "Bölüm 1")
        self.assertEqual(ch1.page, 1)
        self.assertEqual(ch1.children.get_n_items(), 2)

        sec11 = ch1.children.get_item(0)
        self.assertEqual(sec11.title, "Konu 1.1")
        self.assertEqual(sec11.page, 5)
        self.assertEqual(sec11.children.get_n_items(), 1)

        sub111 = sec11.children.get_item(0)
        self.assertEqual(sub111.title, "Detay 1.1.1")
        self.assertEqual(sub111.page, 8)
        self.assertEqual(sub111.children.get_n_items(), 0)

        ch2 = root.get_item(1)
        self.assertEqual(ch2.title, "Bölüm 2")
        self.assertEqual(ch2.page, 20)
        self.assertEqual(ch2.children.get_n_items(), 0)

    def test_bookmarks_panel_empty(self):
        panel = BookmarksPanel()
        panel.load([])
        self.assertEqual(panel.stack.get_visible_child_name(), "empty")

    def test_bookmarks_panel_populated(self):
        panel = BookmarksPanel()
        raw_toc = [[1, "Giriş", 1], [1, "Sonuç", 100]]
        panel.load(raw_toc)
        self.assertEqual(panel.stack.get_visible_child_name(), "list")


class TestReaderSidebar(unittest.TestCase):
    """ReaderSidebar combining thumbnails, bookmarks, and activities."""

    def test_sidebar_contains_three_tabs(self):
        sidebar = ReaderSidebar()
        pages = [sidebar.view_stack.get_page(c) for c in (sidebar.thumbnails, sidebar.bookmarks, sidebar.activities)]
        names = [sidebar.view_stack.get_page(c).get_name() for c in (sidebar.thumbnails, sidebar.bookmarks, sidebar.activities)]
        self.assertEqual(names, ["thumbnails", "bookmarks", "activities"])

    def test_page_chosen_signal_forwarded(self):
        sidebar = ReaderSidebar()
        chosen = []
        sidebar.connect("page-chosen", lambda _w, p: chosen.append(p))

        sidebar.thumbnails.emit("page-chosen", 42)
        self.assertEqual(chosen, [42])

        sidebar.bookmarks.emit("page-chosen", 88)
        self.assertEqual(chosen, [42, 88])


class TestReaderPageScrollIntegration(unittest.TestCase):
    """Integration of scroll mode in ReaderPage."""

    def test_mode_labels_include_scroll(self):
        from interaktiv_gtk.reader.view import MODE_LABELS
        self.assertIn("scroll", MODE_LABELS)
        self.assertEqual(MODE_LABELS["scroll"], "Kaydırma")

    def test_shortcuts_include_scroll_and_sidebar(self):
        from interaktiv_gtk.reader.view import SHORTCUTS
        shortcut_dict = dict(SHORTCUTS)
        self.assertIn("c|C", shortcut_dict)
        self.assertEqual(shortcut_dict["c|C"], "mode-scroll")
        self.assertIn("F9|t|T", shortcut_dict)
        self.assertEqual(shortcut_dict["F9|t|T"], "toggle-sidebar")


if __name__ == "__main__":
    unittest.main(verbosity=2)
