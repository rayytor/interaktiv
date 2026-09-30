"""
A publisher's interactive HTML activity, shown without leaving the lesson.

Where WebKitGTK 6.0 is installed the activity opens in a window of our own.
Where it is not -- the boards -- `open_in_browser` hands it to Chrome as an
app window instead.

The activity is loaded from disk if its bundle is there and from the
publisher's CDN if not; a CDN load also caches the bundle for next time. If a
CDN load has not finished after `OFFLINE_AFTER_S` the window says the activity
needs a connection instead of showing a blank page.
"""

from typing import Optional

from gi.repository import Adw, GLib, Gtk

from interaktiv_core import jobs

from .. import browser, icons
from ..widgets import Banner

try:
    import gi
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit
    HAS_WEBKIT = True
except (ValueError, ImportError):
    HAS_WEBKIT = False
    WebKit = None

OFFLINE_AFTER_S = 7

# The window's share of the one it opens over.
WINDOW_FRACTION = 0.86
MIN_SIZE = (960, 720)


def open_in_browser(manager, guid: str) -> bool:
    """Open the activity in the desktop's browser, and cache it for next time."""
    url = jobs.activity_index_url(manager, guid)
    if not url:
        return False
    if not jobs.activity_asset_path(manager, guid, "index.html") and manager is not None:
        jobs.fetch_activity(manager, guid, only_html=True)
    return browser.open_as_app(url) or browser.open_uri(url)


class ActivityDialog(Adw.Window):
    __gtype_name__ = "InteraktivActivityDialog"

    def __init__(
        self,
        manager,
        guid: str,
        title: str = "",
        installed: bool = False,
    ):
        super().__init__(modal=True)
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
        self.set_default_size(*MIN_SIZE)

        self._build_ui()
        self.connect("close-request", self._on_closed)

        self._start_loading()

    def show_for(self, parent: Gtk.Widget) -> None:
        """Present over `parent`'s window, sized to most of it."""
        window = parent.get_root() if parent is not None else None
        if isinstance(window, Gtk.Window):
            self.set_transient_for(window)
            self.set_default_size(
                max(MIN_SIZE[0], int(window.get_width() * WINDOW_FRACTION)),
                max(MIN_SIZE[1], int(window.get_height() * WINDOW_FRACTION)),
            )
        self.present()

    def _build_ui(self) -> None:
        toolbar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

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

        toolbar.append(header)

        # Offline banner
        self.banner = Banner(
            title="Bu etkinlik internet bağlantısı gerektiriyor.",
            revealed=False,
        )
        toolbar.append(self.banner)

        # Content area
        overlay = Gtk.Overlay()

        self.spinner = Gtk.Spinner(
            spinning=True,
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
                description="Bu etkinlik tarayıcıda açılır.",
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

        overlay.set_vexpand(True)
        toolbar.append(overlay)
        self.set_content(toolbar)

    def _start_loading(self) -> None:
        if not self.url:
            self.banner.set_title("Etkinlik adresi bulunamadı.")
            self.banner.set_revealed(True)
            self.spinner.set_visible(False)
            return

        if HAS_WEBKIT and self.webview is not None:
            self.spinner.set_visible(True)
            self.webview.load_uri(self.url)

            if not self._is_local:
                self._timer_id = GLib.timeout_add_seconds(
                    OFFLINE_AFTER_S, self._on_offline_timeout
                )
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
        if self.url:
            browser.open_uri(self.url)

    def toggle_fullscreen(self) -> None:
        if self.is_fullscreen():
            self.unfullscreen()
            self.btn_fullscreen.set_icon_name(icons.FULLSCREEN)
        else:
            self.fullscreen()
            self.btn_fullscreen.set_icon_name(icons.RESTORE)

    def _on_closed(self, *_args) -> bool:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        if HAS_WEBKIT and self.webview is not None:
            try:
                self.webview.load_uri("about:blank")
            except Exception:
                pass
        return False
