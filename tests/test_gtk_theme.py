"""
Themes and settings: the colour matrices that tint the page, how a theme is
applied, what the settings file remembers, and the desktop entry.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Graphene

from interaktiv_gtk.reader.page_view import PageView
from interaktiv_gtk.state.settings import Settings
from interaktiv_gtk.theme import (
    THEMES,
    apply_theme,
    get_theme_color_matrix,
)


class TestColorMatrixMath(unittest.TestCase):
    """Test colour matrix mathematics and conversions."""

    def test_themes_list(self):
        self.assertEqual(THEMES, ["dark", "light", "sepia", "inverted"])

    def test_identity_for_dark_and_light(self):
        mat_dark, vec_dark = get_theme_color_matrix("dark")
        self.assertIsNone(mat_dark)
        self.assertIsNone(vec_dark)

        mat_light, vec_light = get_theme_color_matrix("light")
        self.assertIsNone(mat_light)
        self.assertIsNone(vec_light)

    def test_sepia_color_matrix(self):
        mat, vec = get_theme_color_matrix("sepia")
        self.assertIsNotNone(mat)
        self.assertIsNotNone(vec)
        self.assertIsInstance(mat, Graphene.Matrix)
        self.assertIsInstance(vec, Graphene.Vec4)

        # Test transformation of pure white (1, 1, 1, 1)
        # In sepia, warm reddish/brown tones dominate -> R > B
        in_vec = Graphene.Vec4()
        in_vec.init(1.0, 1.0, 1.0, 1.0)
        out_vec = mat.transform_vec4(in_vec)

        r = out_vec.get_x() + vec.get_x()
        g = out_vec.get_y() + vec.get_y()
        b = out_vec.get_z() + vec.get_z()
        a = out_vec.get_w() + vec.get_w()

        self.assertGreater(r, b)
        self.assertGreater(g, b)
        self.assertAlmostEqual(a, 1.0, delta=0.01)

    def test_inverted_color_matrix(self):
        mat, vec = get_theme_color_matrix("inverted")
        self.assertIsNotNone(mat)
        self.assertIsNotNone(vec)
        self.assertIsInstance(mat, Graphene.Matrix)
        self.assertIsInstance(vec, Graphene.Vec4)

        # Test transformation of pure white (1, 1, 1, 1)
        # Invert(0.9) turns bright white into a dark background color (< 0.25)
        in_white = Graphene.Vec4()
        in_white.init(1.0, 1.0, 1.0, 1.0)
        out_white = mat.transform_vec4(in_white)

        r_white = out_white.get_x() + vec.get_x()
        g_white = out_white.get_y() + vec.get_y()
        b_white = out_white.get_z() + vec.get_z()
        self.assertLess(r_white, 0.35)
        self.assertLess(g_white, 0.35)
        self.assertLess(b_white, 0.35)

        # Invert(0.9) turns pure black (0, 0, 0, 1) into a light color (> 0.7)
        in_black = Graphene.Vec4()
        in_black.init(0.0, 0.0, 0.0, 1.0)
        out_black = mat.transform_vec4(in_black)

        r_black = out_black.get_x() + vec.get_x()
        g_black = out_black.get_y() + vec.get_y()
        b_black = out_black.get_z() + vec.get_z()
        self.assertGreater(r_black, 0.7)
        self.assertGreater(g_black, 0.7)
        self.assertGreater(b_black, 0.7)


class TestPageViewTheming(unittest.TestCase):
    """Test PageView color matrix application during snapshot."""

    def setUp(self):
        self.page_view = PageView()
        self.page_view.set_layout_size(200, 300)
        self.page_view.set_pdf_size(200, 300)
        self.page_view.set_page(1, 0)

    def test_theme_property_and_overrides(self):
        PageView.set_global_theme("dark")
        self.assertEqual(self.page_view.theme, "dark")

        PageView.set_global_theme("sepia")
        self.assertEqual(self.page_view.theme, "sepia")

        # Per-instance override
        self.page_view.set_theme("inverted")
        self.assertEqual(self.page_view.theme, "inverted")

        self.page_view.set_theme(None)
        self.assertEqual(self.page_view.theme, "sepia")

    def test_snapshot_wraps_texture_node_for_sepia(self):
        mock_snapshot = MagicMock()
        mock_texture = MagicMock()
        self.page_view.set_texture(mock_texture, 1, 0)
        self.page_view.set_theme("sepia")

        # Trigger do_snapshot
        with patch.object(self.page_view, "get_width", return_value=200), \
             patch.object(self.page_view, "get_height", return_value=300):
            self.page_view.do_snapshot(mock_snapshot)

        # push_color_matrix should have been called
        self.assertEqual(mock_snapshot.push_color_matrix.call_count, 1)
        self.assertEqual(mock_snapshot.pop.call_count, 1)
        self.assertEqual(mock_snapshot.append_scaled_texture.call_count, 1)

    def test_snapshot_does_not_wrap_texture_node_for_dark(self):
        mock_snapshot = MagicMock()
        mock_texture = MagicMock()
        self.page_view.set_texture(mock_texture, 1, 0)
        self.page_view.set_theme("dark")

        with patch.object(self.page_view, "get_width", return_value=200), \
             patch.object(self.page_view, "get_height", return_value=300):
            self.page_view.do_snapshot(mock_snapshot)

        # push_color_matrix should NOT be called for dark
        self.assertEqual(mock_snapshot.push_color_matrix.call_count, 0)
        self.assertEqual(mock_snapshot.append_scaled_texture.call_count, 1)


class TestThemeApplicationAndCycling(unittest.TestCase):
    """Test theme application, style manager color schemes, and window classes."""

    def test_apply_theme_schemes_and_window_classes(self):
        mock_window = MagicMock()
        mock_window.remove_css_class = MagicMock()
        mock_window.add_css_class = MagicMock()

        # Dark theme
        apply_theme("dark", mock_window)
        mock_window.remove_css_class.assert_any_call("theme-sepia")
        mock_window.remove_css_class.assert_any_call("theme-inverted")

        # Sepia theme
        apply_theme("sepia", mock_window)
        mock_window.add_css_class.assert_called_with("theme-sepia")

        # Inverted theme
        apply_theme("inverted", mock_window)
        mock_window.add_css_class.assert_called_with("theme-inverted")


class TestSettingsPersistence(unittest.TestCase):
    """Test Settings persistence, schema defaults, and atomic flush."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.settings_file = os.path.join(self.tmpdir, "test_state.json")
        self.settings = Settings(path=self.settings_file)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_settings_defaults(self):
        self.assertEqual(self.settings.get("theme"), "dark")
        self.assertEqual(self.settings.get("view_mode"), "book")
        self.assertEqual(self.settings.get("zoom_mode"), "fit-page")
        self.assertFalse(self.settings.get("sidebar_open"))
        self.assertTrue(self.settings.get("show_activities"))
        self.assertEqual(self.settings.get("library_filter"), "all")

    def test_settings_save_and_reload(self):
        self.settings.set("theme", "sepia")
        self.settings.set("sidebar_open", True)
        self.settings.set("view_mode", "scroll")
        self.settings.set_last_page("book-123", 42)
        self.settings.update(window_width=1600, window_height=900, window_maximized=True)
        self.settings.flush()

        # Reload in a new Settings instance
        reloaded = Settings(path=self.settings_file)
        self.assertEqual(reloaded.get("theme"), "sepia")
        self.assertTrue(reloaded.get("sidebar_open"))
        self.assertEqual(reloaded.get("view_mode"), "scroll")
        self.assertEqual(reloaded.last_page("book-123"), 42)
        self.assertEqual(reloaded.get("window_width"), 1600)
        self.assertEqual(reloaded.get("window_height"), 900)
        self.assertTrue(reloaded.get("window_maximized"))


class TestPackagingAndDesktop(unittest.TestCase):
    """The desktop entry and icon used when running from a source checkout."""

    def test_desktop_file_exists_and_valid(self):
        desktop_path = os.path.join(PROJECT_ROOT, "packaging", "org.interaktiv.School.desktop")
        self.assertTrue(os.path.isfile(desktop_path))

        with open(desktop_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("[Desktop Entry]", content)
        self.assertIn("Type=Application", content)
        self.assertIn("Icon=org.interaktiv.School", content)
        self.assertIn("Comment[tr]=", content)

    def test_application_icons_exist(self):
        svg_icon = os.path.join(
            PROJECT_ROOT, "interaktiv_gtk", "icons", "hicolor", "scalable", "apps",
            "org.interaktiv.School.svg",
        )
        self.assertTrue(os.path.isfile(svg_icon), "org.interaktiv.School.svg must exist")
        self.assertGreater(os.path.getsize(svg_icon), 0)


if __name__ == "__main__":
    unittest.main()
