"""
Watching downloads that `BooksManager` runs on its own threads.

The manager already owns the transfer; the UI only needs to know how far it
got. It asks on a timer rather than through a callback because that is what a
progress bar wants -- one repaint per tick, not one per 128 KB chunk -- and the
timer stops itself as soon as nothing is moving.
"""

from typing import Any, Callable, Dict

from gi.repository import GLib

# The cadence `js/dashboard.js:startPollingDownloads` uses.
INTERVAL_MS = 800


class DownloadWatcher:
    """Polls `BooksManager.get_download_statuses()` while anything is active."""

    def __init__(
        self,
        manager,
        on_tick: Callable[[Dict[str, Any]], None],
        on_settled: Callable[[], None],
    ):
        self.manager = manager
        self.on_tick = on_tick
        self.on_settled = on_settled
        self._source = 0

    @property
    def running(self) -> bool:
        return bool(self._source)

    def start(self) -> None:
        if self._source or getattr(self.manager, "library_mode", False):
            # Nothing can be downloaded in a packaged library.
            return
        self._source = GLib.timeout_add(INTERVAL_MS, self._tick)

    def stop(self) -> None:
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0

    def _tick(self) -> bool:
        statuses = self.manager.get_download_statuses()
        self.on_tick(statuses)
        active = any(info.get("status") == "downloading" for info in statuses.values())
        if active:
            return GLib.SOURCE_CONTINUE
        self._source = 0
        # A finished download changes install state and file size, which only a
        # catalogue reload knows about.
        self.on_settled()
        return GLib.SOURCE_REMOVE
