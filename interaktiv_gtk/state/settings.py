"""
The handful of things the reader remembers between runs.

One JSON file at `$XDG_CONFIG_HOME/interaktiv/state.json`, written atomically
and no more than once a second however often it is touched -- a teacher paging
through a book changes `last_pages` on every turn and none of those writes is
worth a disk hit of its own.
"""

import json
import os
import tempfile
from typing import Any, Dict

from gi.repository import GLib

from interaktiv_core import appdirs

# Writes are coalesced over this long; a quit flushes whatever is pending.
SAVE_DELAY_MS = 1000


class Settings:
    DEFAULTS: Dict[str, Any] = {
        # Window
        "window_width": 1280,
        "window_height": 820,
        "window_maximized": False,
        # Library
        "library_filter": "all",
        # Reader (used from M2 on; declared here so one file holds the schema)
        "theme": "dark",
        "view_mode": "book",
        "zoom_mode": "fit-page",
        "sidebar_open": False,
        # Whether every activity region is outlined, or only the one under the
        # pointer. On by default, unlike the web: a board is touched, not
        # hovered, so hover-to-reveal would leave the activities invisible.
        "show_activities": True,
        "last_pages": {},
        # Whether a bake's calibration confidence may suppress activity links.
        # Off by default: the web reader never applied the gate (`bakedPage()`
        # does not copy `confidence` onto the page), so leaving it off is what
        # keeps the native reader showing the same hotspots.
        "link_confidence_gate": False,
    }

    def __init__(self, path: str = None):
        self.path = path or appdirs.state_path()
        self._data: Dict[str, Any] = dict(self.DEFAULTS)
        self._pending = 0
        self._load()

    # -- reading ----------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(stored, dict):
            return
        for key, value in stored.items():
            # An unknown key is kept, not dropped: a newer build may have
            # written it and this one should not silently eat it.
            self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        return self.DEFAULTS.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    # -- writing ----------------------------------------------------------

    def set(self, key: str, value: Any) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._schedule()

    def update(self, **values: Any) -> None:
        changed = False
        for key, value in values.items():
            if self._data.get(key) != value:
                self._data[key] = value
                changed = True
        if changed:
            self._schedule()

    def last_page(self, book_id: str) -> int:
        return int((self._data.get("last_pages") or {}).get(book_id, 1))

    def set_last_page(self, book_id: str, page: int) -> None:
        pages = dict(self._data.get("last_pages") or {})
        if pages.get(book_id) == page:
            return
        pages[book_id] = int(page)
        self._data["last_pages"] = pages
        self._schedule()

    # -- persistence ------------------------------------------------------

    def _schedule(self) -> None:
        if self._pending:
            return
        self._pending = GLib.timeout_add(SAVE_DELAY_MS, self._on_timeout)

    def _on_timeout(self) -> bool:
        self._pending = 0
        self.flush()
        return GLib.SOURCE_REMOVE

    def flush(self) -> None:
        """Write now. Called on quit, and by the coalescing timer."""
        if self._pending:
            GLib.source_remove(self._pending)
            self._pending = 0
        directory = os.path.dirname(self.path)
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            # A read-only config directory is not worth failing a lesson over.
            pass
