"""
The interactive activity modal dialog.

School Edition displays the publisher's interactive HTML activities in a native
dialog powered by WebKitGTK 6.0 when available, or provides a seamless fallback
to an external browser via `Gtk.UriLauncher`.

Key responsibilities:
  * **Floating dialog**: `Adw.Dialog` configured with `FLOATING` presentation.
  * **Local-first then CDN**: If the activity bundle is present on disk
    (`activities/<guid>/index.html`), load `file://...`. If not, load directly
    from the publisher's CDN (`https://ogm-large-cdn.eba.gov.tr/...`).
  * **Background caching**: When opened from CDN, trigger `jobs.fetch_activity`
    so future opens work offline without network round-trips.
  * **7-second offline banner**: Display an `Adw.Banner` if loading takes longer
    than 7 seconds or fails due to network disconnection.
  * **Controls**: Header bar buttons to open the activity in an external browser
    or toggle fullscreen.
  * **Memory hygiene**: Reset WebView to `about:blank` and remove timers on close.
"""

from typing import Optional
import webbrowser

from gi.repository import Adw, GLib, Gtk

from interaktiv_core import jobs

from .. import icons

try:
    import gi
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit
    HAS_WEBKIT = True
except (ValueError, ImportError):
    HAS_WEBKIT = False
    WebKit = None


class ActivityDialog(Adw.Dialog):
    __gtype_name__ = "InteraktivActivityDialog"

    def __init__(
        self,
        manager,
        guid: str,
        title: str = "",
        installed: bool = False,
    ):
        super().__init__()
        self.manager = manager
        self.guid = guid
        self.title_text = title or "Etkileşimli Etkinlik"
        self.installed = installed

        self.url = jobs.activity_index_url(manager, guid) or ""
        self._is_local = bool(
            jobs.activity_asset_path(manager, guid, "index.html")
        )
        self._timer_id: Optional[int] = None
        self._is_loaded: bool = False

        self.set_title(self.title_text)
        self.set_content_width(960)
        self.set_content_height(720)
        self.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)

        self._build_ui()
        self.connect("closed", self._on_closed)

        self._start_loading()

    def _build_ui(self) -> None:
        toolbar = Adw.ToolbarView()

        # Header bar
        header = Adw.HeaderBar()
        window_title = Adw.WindowTitle(
            title=self.title_text,
            subtitle="Etkileşimli Etkinlik",
        )
        header.set_title_widget(window_title)

        self.btn_browser = Gtk.Button(
            icon_name=icons.BROWSER,
            tooltip_text="Tarayıcıda Aç",
        )
        self.btn_browser.add_css_class("tool-btn")
        self.btn_browser.connect("clicked", lambda *_: self.open_in_browser())
        header.pack_end(self.btn_browser)

        self.btn_fullscreen = Gtk.Button(
            icon_name=icons.FULLSCREEN,
            tooltip_text="Tam Ekran (F)",
        )
        self.btn_fullscreen.add_css_class("tool-btn")
        self.btn_fullscreen.connect("clicked", lambda *_: self.toggle_fullscreen())
        header.pack_end(self.btn_fullscreen)

        toolbar.add_top_bar(header)

        # Offline banner
        self.banner = Adw.Banner(
            title="Bu etkinlik internet bağlantısı gerektiriyor.",
            revealed=False,
        )
        toolbar.add_top_bar(self.banner)

        # Content area
        overlay = Gtk.Overlay()

        self.spinner = Adw.Spinner(
            width_request=48,
            height_request=48,
            hexpand=True,
            vexpand=True,
        )
        self.spinner.set_halign(Gtk.Align.CENTER)
        self.spinner.set_valign(Gtk.Align.CENTER)

        if HAS_WEBKIT:
            network_session = WebKit.NetworkSession.new_ephemeral()
            settings = WebKit.Settings()
            settings.set_allow_file_access_from_file_urls(False)
            settings.set_enable_javascript(True)

            self.webview = WebKit.WebView(network_session=network_session, settings=settings)
            self.webview.set_hexpand(True)
            self.webview.set_vexpand(True)

            self.webview.connect("load-changed", self._on_load_changed)
            self.webview.connect("load-failed", self._on_load_failed)

            overlay.set_child(self.webview)
            overlay.add_overlay(self.spinner)
        else:
            self.webview = None
            status = Adw.StatusPage(
                icon_name="applications-internet-symbolic",
                title=self.title_text,
                description="Etkinliği görüntülemek için WebKitGTK 6.0 gereklidir veya harici tarayıcıda açabilirsiniz.",
                hexpand=True,
                vexpand=True,
            )
            btn = Gtk.Button(
                label="Tarayıcıda Aç",
                css_classes=["suggested-action", "pill"],
            )
            btn.set_halign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda *_: self.open_in_browser())
            status.set_child(btn)

            overlay.set_child(status)

        toolbar.set_content(overlay)
        self.set_child(toolbar)

    def _start_loading(self) -> None:
        if not self.url:
            self.banner.set_title("Etkinlik adresi bulunamadı.")
            self.banner.set_revealed(True)
            self.spinner.set_visible(False)
            return

        if HAS_WEBKIT and self.webview is not None:
            self.spinner.set_visible(True)
            self.webview.load_uri(self.url)

            # 7-second offline detection timer if loading from remote CDN
            if not self._is_local:
                self._timer_id = GLib.timeout_add_seconds(7, self._on_offline_timeout)
        else:
            self.spinner.set_visible(False)

        # Trigger background caching if not already on disk
        if not self._is_local and self.manager is not None:
            jobs.fetch_activity(
                self.manager,
                self.guid,
                only_html=True,
                on_done=self._on_fetch_done,
            )

    def _on_load_changed(self, _webview, event) -> None:
        if event == WebKit.LoadEvent.FINISHED:
            self._is_loaded = True
            if self._timer_id is not None:
                GLib.source_remove(self._timer_id)
                self._timer_id = None
            self.banner.set_revealed(False)
            self.spinner.set_visible(False)
        elif event == WebKit.LoadEvent.STARTED:
            self.spinner.set_visible(True)

    def _on_load_failed(self, _webview, _event, _failing_uri, _error) -> bool:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self.banner.set_title("Bu etkinlik internet bağlantısı gerektiriyor.")
        self.banner.set_revealed(True)
        self.spinner.set_visible(False)
        return False

    def _on_offline_timeout(self) -> bool:
        self._timer_id = None
        if not self._is_loaded:
            self.banner.set_title("Bu etkinlik internet bağlantısı gerektiriyor.")
            self.banner.set_revealed(True)
            self.spinner.set_visible(False)
        return GLib.SOURCE_REMOVE

    def _on_fetch_done(self, guid: str, ok: bool) -> None:
        GLib.idle_add(self._apply_fetch_result, guid, ok)

    def _apply_fetch_result(self, guid: str, ok: bool) -> bool:
        if ok:
            self._is_local = True
        elif not self._is_loaded:
            self.banner.set_title("Bu etkinlik internet bağlantısı gerektiriyor.")
            self.banner.set_revealed(True)
        return GLib.SOURCE_REMOVE

    def open_in_browser(self) -> None:
        if not self.url:
            return
        root = self.get_root()
        try:
            launcher = Gtk.UriLauncher.new(self.url)
            launcher.launch(root if isinstance(root, Gtk.Window) else None, None, None, None)
        except Exception:
            webbrowser.open(self.url)

    def toggle_fullscreen(self) -> None:
        root = self.get_root()
        if root and isinstance(root, Gtk.Window):
            if root.is_fullscreen():
                root.unfullscreen()
                self.btn_fullscreen.set_icon_name(icons.FULLSCREEN)
            else:
                root.fullscreen()
                self.btn_fullscreen.set_icon_name(icons.RESTORE)

    def _on_closed(self, *_args) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if HAS_WEBKIT and self.webview is not None:
            try:
                self.webview.load_uri("about:blank")
            except Exception:
                pass
