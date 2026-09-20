"""
Book covers, decoded off the main loop.

All 56 covers ship in `thumbnails/` (asserted by `tests/test_school_edition.py`),
so the web dashboard's fallback -- render page 1 over the byte-range proxy with
pdf.js -- has nothing to do here and is gone. What is left is reading a small
JPEG, which is fast but not free: 56 of them on the main loop is a visible stall
when the library first opens.

The decode runs on a worker thread through GdkPixbuf, which is thread-safe; the
cheap wrap into a `Gdk.Texture` happens back on the main loop, because a texture
belongs to the display.
"""

import threading
from typing import Callable, Dict, Optional

from gi.repository import Gdk, GdkPixbuf, GLib

# Two at a time is plenty: the work is I/O plus a small JPEG decode, and a
# deeper pool only competes with the render thread for the same cores.
WORKERS = 2


class CoverLoader:
    """Decodes cover images in the background, once each."""

    def __init__(self, manager):
        self.manager = manager
        self._cache: Dict[str, Optional[Gdk.Texture]] = {}
        self._waiting: Dict[str, list] = {}
        self._queue: list = []
        self._lock = threading.Lock()
        self._running = 0
        self._shutdown = False

    def cached(self, book_id: str) -> Optional[Gdk.Texture]:
        """The cover if it is already decoded, without starting any work."""
        return self._cache.get(book_id)

    def load(self, book_id: str, callback: Callable[[Optional[Gdk.Texture]], None]) -> None:
        """
        Ask for one cover.

        `callback` is always called on the main loop, with the texture or with
        None when the book has no cover on disk. A book already being decoded
        for another card is not decoded twice.
        """
        if book_id in self._cache:
            callback(self._cache[book_id])
            return
        with self._lock:
            if book_id in self._waiting:
                self._waiting[book_id].append(callback)
                return
            self._waiting[book_id] = [callback]
            self._queue.append(book_id)
            start = self._running < WORKERS
            if start:
                self._running += 1
        if start:
            threading.Thread(target=self._drain, daemon=True).start()

    def shutdown(self) -> None:
        self._shutdown = True
        with self._lock:
            self._queue.clear()

    # -- worker -----------------------------------------------------------

    def _drain(self) -> None:
        while not self._shutdown:
            with self._lock:
                if not self._queue:
                    self._running -= 1
                    return
                book_id = self._queue.pop(0)
            pixbuf = self._decode(book_id)
            GLib.idle_add(self._deliver, book_id, pixbuf, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _decode(self, book_id: str) -> Optional[GdkPixbuf.Pixbuf]:
        try:
            path = self.manager.get_thumbnail_path(book_id)
        except Exception:
            path = None
        if not path:
            return None
        try:
            return GdkPixbuf.Pixbuf.new_from_file(path)
        except GLib.Error:
            return None

    # -- main loop --------------------------------------------------------

    def _deliver(self, book_id: str, pixbuf: Optional[GdkPixbuf.Pixbuf]) -> bool:
        texture = Gdk.Texture.new_for_pixbuf(pixbuf) if pixbuf is not None else None
        self._cache[book_id] = texture
        with self._lock:
            callbacks = self._waiting.pop(book_id, [])
        for callback in callbacks:
            try:
                callback(texture)
            except Exception:
                pass
        return GLib.SOURCE_REMOVE

    def forget(self, book_id: str) -> None:
        """Drop a cached cover, so a freshly generated one is picked up."""
        self._cache.pop(book_id, None)
