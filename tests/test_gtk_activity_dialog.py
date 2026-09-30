"""
Interactive activities: the WebKit window, and the browser hand-off used where
WebKitGTK is not installed.
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
from gi.repository import Adw

from interaktiv_gtk.reader import activity_dialog
from interaktiv_gtk.reader.activity_dialog import ActivityDialog, HAS_WEBKIT, WebKit
from interaktiv_gtk.reader.overlay import Pin, Spot
from interaktiv_core.oges import Oge


@unittest.skipUnless(HAS_WEBKIT, "WebKitGTK 6.0 is not installed")
class TestActivityDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        self.manager = MagicMock()
        self.manager.base_dir = "/tmp/interaktiv_test"
        self.manager.activity_dir.return_value = None
        self.guid = "323bb88f-3ff7-483a-88ae-bcd170ceb53c"

    def test_dialog_properties_and_structure(self):
        """ActivityDialog is a modal window of a usable size with its controls."""
        with patch("interaktiv_core.jobs.fetch_activity"):
            dialog = ActivityDialog(
                self.manager,
                self.guid,
                title="Sayfa 42 · Activity a",
                installed=False,
            )
            self.assertEqual(dialog.get_title(), "Sayfa 42 · Activity a")
            self.assertEqual(tuple(dialog.get_default_size()), (960, 720))
            self.assertTrue(dialog.get_modal())
            self.assertIsNotNone(dialog.btn_browser)
            self.assertIsNotNone(dialog.btn_fullscreen)
            self.assertIsNotNone(dialog.banner)
            self.assertIsNotNone(dialog.webview)
            self.assertIsInstance(dialog.webview, WebKit.WebView)
            dialog.destroy()

    def test_dialog_fallback_when_no_webkit(self):
        """When WebKit is not available, dialog shows fallback StatusPage."""
        with patch("interaktiv_gtk.reader.activity_dialog.HAS_WEBKIT", False), \
             patch("interaktiv_core.jobs.fetch_activity"):
            dialog = ActivityDialog(
                self.manager,
                self.guid,
                title="Sayfa 42 · Activity a",
                installed=False,
            )
            self.assertIsNone(dialog.webview)
            self.assertFalse(dialog.spinner.get_visible())
            dialog.destroy()

    def test_local_url_resolution(self):
        """When local index.html exists, dialog loads file:// URL and does not fetch."""
        with patch("interaktiv_core.jobs.activity_asset_path") as mock_path, \
             patch("interaktiv_core.jobs.fetch_activity") as mock_fetch:
            mock_path.return_value = "/mock/activities/323bb88f/index.html"
            dialog = ActivityDialog(
                self.manager, self.guid, title="Test Local", installed=True
            )
            self.assertTrue(dialog.url.startswith("file:///mock/activities/323bb88f/index.html"))
            mock_fetch.assert_not_called()
            # Local load should not set 7s remote timer
            self.assertIsNone(dialog._timer_id)
            dialog.destroy()

    def test_remote_url_resolution_and_background_caching(self):
        """When not on disk, loads from CDN and initiates background caching."""
        with patch("interaktiv_core.jobs.activity_asset_path", return_value=None), \
             patch("interaktiv_core.jobs.fetch_activity") as mock_fetch:
            dialog = ActivityDialog(
                self.manager, self.guid, title="Test CDN", installed=False
            )
            self.assertEqual(
                dialog.url,
                f"https://ogm-large-cdn.eba.gov.tr/materyal/Uygulama/{self.guid}/index.html",
            )
            mock_fetch.assert_called_once_with(
                self.manager, self.guid, only_html=True, on_done=dialog._on_fetch_done
            )
            self.assertIsNotNone(dialog._timer_id)
            dialog.destroy()

    def test_7s_offline_timer_timeout(self):
        """If loading takes too long, timer triggers offline banner."""
        with patch("interaktiv_core.jobs.activity_asset_path", return_value=None), \
             patch("interaktiv_core.jobs.fetch_activity"):
            dialog = ActivityDialog(self.manager, self.guid, title="Timeout Test")
            self.assertFalse(dialog.banner.get_revealed())
            # Simulate timer expiring
            dialog._on_offline_timeout()
            self.assertTrue(dialog.banner.get_revealed())
            self.assertFalse(dialog.spinner.get_visible())
            dialog.destroy()

    def test_load_finished_cancels_timer_and_hides_banner(self):
        """When load completes, offline banner is hidden and timer cancelled."""
        with patch("interaktiv_core.jobs.activity_asset_path", return_value=None), \
             patch("interaktiv_core.jobs.fetch_activity"):
            dialog = ActivityDialog(self.manager, self.guid, title="Success Test")
            self.assertIsNotNone(dialog._timer_id)
            # Simulate WebKit LOAD_FINISHED
            dialog._on_load_changed(dialog.webview, WebKit.LoadEvent.FINISHED)
            self.assertTrue(dialog._is_loaded)
            self.assertIsNone(dialog._timer_id)
            self.assertFalse(dialog.banner.get_revealed())
            self.assertFalse(dialog.spinner.get_visible())
            dialog.destroy()

    def test_load_failed_reveals_banner(self):
        """When load fails, timer is cancelled and banner is revealed."""
        with patch("interaktiv_core.jobs.activity_asset_path", return_value=None), \
             patch("interaktiv_core.jobs.fetch_activity"):
            dialog = ActivityDialog(self.manager, self.guid, title="Failure Test")
            dialog._on_load_failed(dialog.webview, WebKit.LoadEvent.FINISHED, dialog.url, None)
            self.assertIsNone(dialog._timer_id)
            self.assertTrue(dialog.banner.get_revealed())
            self.assertFalse(dialog.spinner.get_visible())
            dialog.destroy()

    def test_open_in_browser(self):
        """The header button hands the activity's URL to the desktop."""
        with patch("interaktiv_core.jobs.activity_asset_path", return_value=None), \
             patch("interaktiv_core.jobs.fetch_activity"), \
             patch("interaktiv_gtk.browser.open_uri") as mock_open:
            dialog = ActivityDialog(self.manager, self.guid, title="Browser Test")
            dialog.open_in_browser()
            mock_open.assert_called_once_with(dialog.url)
            dialog.destroy()

    def test_toggle_fullscreen(self):
        """toggle_fullscreen takes the activity window in and out of fullscreen."""
        with patch("interaktiv_core.jobs.activity_asset_path", return_value=None), \
             patch("interaktiv_core.jobs.fetch_activity"):
            dialog = ActivityDialog(self.manager, self.guid, title="Fullscreen Test")
            with patch.object(dialog, "is_fullscreen", return_value=False), \
                 patch.object(dialog, "fullscreen") as enter:
                dialog.toggle_fullscreen()
                enter.assert_called_once()
            with patch.object(dialog, "is_fullscreen", return_value=True), \
                 patch.object(dialog, "unfullscreen") as leave:
                dialog.toggle_fullscreen()
                leave.assert_called_once()
            dialog.destroy()


class TestBrowserHandOff(unittest.TestCase):
    """What a board without WebKitGTK does with an interactive activity."""

    GUID = "323bb88f-3ff7-483a-88ae-bcd170ceb53c"

    def setUp(self):
        self.manager = MagicMock()
        self.manager.activity_dir.return_value = None

    def test_chrome_app_window_is_preferred(self):
        with patch("interaktiv_core.jobs.fetch_activity"), \
             patch("interaktiv_gtk.browser.open_as_app", return_value=True) as as_app, \
             patch("interaktiv_gtk.browser.open_uri") as default:
            self.assertTrue(activity_dialog.open_in_browser(self.manager, self.GUID))
            as_app.assert_called_once_with(
                f"https://ogm-large-cdn.eba.gov.tr/materyal/Uygulama/{self.GUID}/index.html"
            )
            default.assert_not_called()

    def test_falls_back_to_the_default_browser(self):
        with patch("interaktiv_core.jobs.fetch_activity"), \
             patch("interaktiv_gtk.browser.open_as_app", return_value=False), \
             patch("interaktiv_gtk.browser.open_uri", return_value=True) as default:
            self.assertTrue(activity_dialog.open_in_browser(self.manager, self.GUID))
            default.assert_called_once()

    def test_a_remote_activity_is_cached_for_next_time(self):
        with patch("interaktiv_core.jobs.fetch_activity") as fetch, \
             patch("interaktiv_gtk.browser.open_as_app", return_value=True):
            activity_dialog.open_in_browser(self.manager, self.GUID)
            fetch.assert_called_once_with(self.manager, self.GUID, only_html=True)

    def test_a_bad_guid_opens_nothing(self):
        with patch("interaktiv_gtk.browser.open_as_app") as as_app:
            self.assertFalse(activity_dialog.open_in_browser(self.manager, "not-a-guid"))
            as_app.assert_not_called()

    def test_chrome_is_started_as_an_app_window(self):
        from interaktiv_gtk import browser

        with patch("interaktiv_gtk.browser.shutil.which",
                   side_effect=lambda name: "/usr/bin/google-chrome"
                   if name == "google-chrome" else None), \
             patch("interaktiv_gtk.browser.subprocess.Popen") as popen:
            self.assertTrue(browser.open_as_app("https://example.invalid/a"))
            command = popen.call_args[0][0]
            self.assertEqual(command[0], "/usr/bin/google-chrome")
            self.assertIn("--app=https://example.invalid/a", command)

    def test_no_chrome_means_no_app_window(self):
        from interaktiv_gtk import browser

        with patch("interaktiv_gtk.browser.shutil.which", return_value=None):
            self.assertFalse(browser.open_as_app("https://example.invalid/a"))


class TestReaderPageActivityIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def test_interactive_spot_triggers_activity_dialog(self):
        """Activating an interactive Spot calls open_activity_dialog."""
        from interaktiv_gtk.reader.view import ReaderPage
        item = MagicMock()
        item.id = "test-book"
        item.title = "Test Book"
        app = MagicMock()
        app.settings.get.return_value = None
        app.manager = MagicMock()

        with patch("interaktiv_gtk.reader.view.DocumentSession") as mock_session_cls, \
             patch("interaktiv_gtk.reader.view.ReaderPage._install_shortcuts"):
            session = MagicMock()
            session.is_open = True
            session.page_count = 10
            session.manager = app.manager
            mock_session_cls.return_value = session

            page = ReaderPage(app, item, "/fake/path.pdf")
            page.open_activity_dialog = MagicMock()
            page.focus_activity = MagicMock()

            # Interactive Spot
            spot = Spot(
                act_index=0,
                part_index=0,
                rect=(10, 10, 100, 100),
                label="a",
                item_count=3,
                guid="test-guid-1234-5678-9012-345678901234",
                installed=False,
            )
            page._on_activity_activated(None, spot, 42)
            page.open_activity_dialog.assert_called_once_with(
                guid="test-guid-1234-5678-9012-345678901234",
                title="Sayfa 42 · a",
                installed=False,
            )
            page.focus_activity.assert_not_called()

    def test_non_interactive_spot_triggers_focus(self):
        """Activating a non-interactive Spot calls focus_activity."""
        from interaktiv_gtk.reader.view import ReaderPage
        item = MagicMock()
        item.id = "test-book"
        item.title = "Test Book"
        app = MagicMock()
        app.settings.get.return_value = None
        app.manager = MagicMock()

        with patch("interaktiv_gtk.reader.view.DocumentSession") as mock_session_cls, \
             patch("interaktiv_gtk.reader.view.ReaderPage._install_shortcuts"):
            session = MagicMock()
            session.is_open = True
            session.page_count = 10
            mock_session_cls.return_value = session

            page = ReaderPage(app, item, "/fake/path.pdf")
            page.open_activity_dialog = MagicMock()
            page.focus_activity = MagicMock()

            mock_act = MagicMock()
            spot = Spot(
                act_index=0,
                part_index=1,
                rect=(10, 10, 100, 100),
                label="b",
                item_count=0,
                guid=None,
                activity=mock_act,
            )
            page._on_activity_activated(None, spot, 42)
            page.open_activity_dialog.assert_not_called()
            page.focus_activity.assert_called_once_with(mock_act, part_index=1)

    def test_pin_triggers_activity_dialog(self):
        """Activating a Pin with guid calls open_activity_dialog."""
        from interaktiv_gtk.reader.view import ReaderPage
        item = MagicMock()
        item.id = "test-book"
        item.title = "Test Book"
        app = MagicMock()
        app.settings.get.return_value = None
        app.manager = MagicMock()

        with patch("interaktiv_gtk.reader.view.DocumentSession") as mock_session_cls, \
             patch("interaktiv_gtk.reader.view.ReaderPage._install_shortcuts"):
            session = MagicMock()
            session.is_open = True
            session.page_count = 10
            mock_session_cls.return_value = session

            page = ReaderPage(app, item, "/fake/path.pdf")
            page.open_activity_dialog = MagicMock()

            oge = Oge(
                id="oge-1",
                guid="oge-guid-1234-5678-9012-345678901234",
                title="Warm-up Activity",
                printed_page=15,
                posx=50.0,
                posy=50.0,
                is_installed=True,
            )
            pin = Pin(oge=oge, x_pct=50.0, y_pct=50.0)

            page._on_activity_activated(None, pin, 15)
            page.open_activity_dialog.assert_called_once_with(
                guid="oge-guid-1234-5678-9012-345678901234",
                title="Sayfa 15 · Warm-up Activity",
                installed=True,
            )


if __name__ == "__main__":
    unittest.main()
