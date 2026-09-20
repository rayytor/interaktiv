"""
The queue in front of the renderer, and the thread that feeds it.

The renderer itself is a child process (see `worker.py` for why). What lives
here is everything about *which* page to draw next, which is a question only the
reader can answer -- so all of it stays on this side of the pipe.

Three things make paging a 443 MB book on a smartboard feel like paging a book.

**Coalescing.** Work is keyed by what it draws -- page, scale, rotation, clip --
and resubmitting a key replaces the pending entry instead of queueing beside it.
Holding the next-page button for twenty pages therefore never builds a queue of
twenty renders; only the spread the teacher stopped on is drawn.

**Generations.** Anything that changes what should be on screen -- a page turn,
a zoom, a rotate, a mode switch, a resize -- bumps a counter. A queued request
from an older generation is dropped when it reaches the front of the queue
rather than drawn and thrown away. Exactly one render is ever in flight, so
"finish and drop" costs at most one page; MuPDF has no cooperative abort and at
20-50 ms a page that is not worth interrupting.

**Lanes.** A priority queue keeps a visible page ahead of a prefetch, a prefetch
ahead of a thumbnail, and a thumbnail ahead of a background scan.

The pump thread spends nearly all its time blocked reading the pipe, which
releases the GIL -- so unlike the in-process renderer it replaces, it does not
compete with the main loop for the one thing the main loop cannot share.
"""

import os
import queue
import subprocess
import sys
import threading
import traceback
from typing import Callable, Optional

from gi.repository import GLib

from .protocol import read_message, write_message
from .requests import DocumentInfo, RenderRequest, SearchRequest, TextPassRequest

LANE_VISIBLE = 0
LANE_PREFETCH = 1
LANE_THUMBNAIL = 2
LANE_SCAN = 3

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# Parent plus renderer. A school board is not a workstation: some of them have
# two gigabytes in total, and a reader that is swapping has stopped being usable.
RSS_CEILING_MB = 1200


class RenderService:
    """
    Owns one renderer process and the single thread that talks to it.

    Every callback is delivered on the main loop through `GLib.idle_add`, so a
    caller never has to think about threads; it does have to think about the
    document not being open yet, which is what `on_ready` is for.
    """

    def __init__(
        self,
        path: str,
        on_ready: Callable,
        on_result: Callable,
        on_error: Optional[Callable] = None,
        on_pressure: Optional[Callable] = None,
    ):
        self.path = path
        self._on_ready = on_ready
        self._on_result = on_result
        self._on_error = on_error
        self._on_pressure = on_pressure

        self._queue: "queue.PriorityQueue" = queue.PriorityQueue()
        self._pending = {}
        self._search_callbacks = {}
        self._text_callbacks = {}
        self._token_seq = 0
        self._lock = threading.Lock()
        self._generation = 0
        self._seq = 0
        self._closed = False
        self._renders = 0
        self._child: Optional[subprocess.Popen] = None

        self._thread = threading.Thread(
            target=self._run, name="interaktiv-render", daemon=True
        )

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        """
        Safe to call twice; the reader closes on pop and again on quit.

        Closing is also how the memory comes back. MuPDF's store cannot be
        capped in PyMuPDF 1.28, so the only guarantee that a closed book
        releases what it was holding is that the process holding it exits.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._pending.clear()
            self._search_callbacks.clear()
            self._text_callbacks.clear()
        self._queue.put((-1, -1, None))

    def _terminate_child(self) -> None:
        child = self._child
        if child is None:
            return
        self._child = None
        try:
            if child.stdin and not child.stdin.closed:
                write_message(child.stdin, {"op": "quit"})
                child.stdin.close()
        except Exception:
            pass
        try:
            child.wait(timeout=2)
        except Exception:
            child.kill()
            try:
                child.wait(timeout=2)
            except Exception:
                pass
        for stream in (child.stdin, child.stdout):
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                pass

    # -- submitting -------------------------------------------------------

    def bump_generation(self) -> int:
        """
        Declare everything queued stale. Returns the new generation, which the
        caller stamps onto the requests it is about to make.
        """
        with self._lock:
            self._generation += 1
            generation = self._generation
            for key in [
                k for k, r in self._pending.items() if r.generation < generation
            ]:
                del self._pending[key]
        return generation

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def submit(
        self,
        page: int,
        scale: float,
        rotation: int = 0,
        clip=None,
        lane: int = LANE_VISIBLE,
        generation: Optional[int] = None,
    ) -> Optional[RenderRequest]:
        with self._lock:
            if self._closed:
                return None
            request = RenderRequest(
                page=page,
                scale=scale,
                rotation=rotation % 360,
                clip=clip,
                lane=lane,
                generation=self._generation if generation is None else generation,
            )
            self._pending[request.key] = request
            self._seq += 1
            item = (lane, self._seq, request.key)
        self._queue.put(item)
        return request

    def submit_search(
        self,
        needle: str,
        pages: Sequence[int],
        on_result: Callable,
        lane: int = LANE_SCAN,
    ) -> int:
        with self._lock:
            if self._closed:
                return 0
            self._token_seq += 1
            token = self._token_seq
            self._search_callbacks[token] = on_result
            request = SearchRequest(
                needle=needle,
                pages=tuple(pages),
                lane=lane,
                generation=self._generation,
                token=token,
            )
            self._pending[request.key] = request
            self._seq += 1
            item = (lane, self._seq, request.key)
        self._queue.put(item)
        return token

    def submit_text_pass(
        self,
        pages: Sequence[int],
        on_result: Callable,
        lane: int = LANE_SCAN,
    ) -> int:
        with self._lock:
            if self._closed:
                return 0
            self._token_seq += 1
            token = self._token_seq
            self._text_callbacks[token] = on_result
            request = TextPassRequest(
                pages=tuple(pages),
                lane=lane,
                generation=self._generation,
                token=token,
            )
            self._pending[request.key] = request
            self._seq += 1
            item = (lane, self._seq, request.key)
        self._queue.put(item)
        return token

    # -- the pump thread --------------------------------------------------

    def _spawn(self) -> subprocess.Popen:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [PROJECT_ROOT] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
        )
        return subprocess.Popen(
            [sys.executable, "-u", "-m", "interaktiv_gtk.render.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=PROJECT_ROOT,
            env=env,
            bufsize=0,
        )

    def _run(self) -> None:
        try:
            self._child = self._spawn()
            write_message(self._child.stdin, {"op": "open", "path": self.path})
            header, _ = read_message(self._child.stdout)
        except Exception as exc:
            traceback.print_exc()
            GLib.idle_add(self._deliver_error, str(exc))
            self._terminate_child()
            return

        if header.get("op") != "ready":
            GLib.idle_add(
                self._deliver_error, header.get("message", "Bilinmeyen hata")
            )
            self._terminate_child()
            return

        info = DocumentInfo(
            path=self.path,
            page_count=header["page_count"],
            page_sizes=tuple(tuple(s) for s in header["page_sizes"]),
            toc=tuple(tuple(x) for x in header.get("toc", ())),
        )
        GLib.idle_add(self._deliver_ready, info)

        idle = False
        while True:
            try:
                item = self._queue.get(timeout=None if idle else 0.05)
            except queue.Empty:
                # The queue just ran dry. Nothing is waiting on the renderer,
                # so this is the cheapest moment to have it give memory back.
                self._tell_child({"op": "idle"})
                idle = True
                continue
            idle = False

            _lane, _seq, key = item
            if key is None:
                break

            with self._lock:
                if self._closed:
                    break
                request = self._pending.pop(key, None)
                generation = self._generation

            if request is None:
                # A duplicate queue entry for a key already taken (or replaced
                # and taken) -- there is nothing left to draw.
                continue
            if request.generation < generation:
                continue

            if isinstance(request, RenderRequest):
                if not self._render(request):
                    break
            elif isinstance(request, SearchRequest):
                if not self._search(request):
                    break
            elif isinstance(request, TextPassRequest):
                if not self._text_pass(request):
                    break

        self._terminate_child()

    def _tell_child(self, message: dict) -> bool:
        child = self._child
        if child is None or child.stdin is None or child.stdin.closed:
            return False
        try:
            write_message(child.stdin, message)
            return True
        except Exception:
            return False

    def _render(self, request: RenderRequest) -> bool:
        """One round trip. Returns False when the renderer is gone."""
        child = self._child
        if child is None:
            return False
        try:
            write_message(child.stdin, {
                "op": "render",
                "page": request.page,
                "scale": request.scale,
                "rotation": request.rotation,
                "clip": list(request.clip) if request.clip else None,
            })
            header, payload = read_message(child.stdout)
        except Exception as exc:
            if not self._closed:
                GLib.idle_add(
                    self._deliver_error,
                    f"Görüntüleyici beklenmedik şekilde kapandı: {exc}",
                )
            return False

        if header.get("op") != "result":
            # A page that will not draw is not a reason to lose the book.
            return True

        GLib.idle_add(self._deliver_result, request, header, payload)

        self._renders += 1
        if self._renders % 16 == 0:
            self._check_pressure()
        return True

    def _search(self, request: SearchRequest) -> bool:
        child = self._child
        if child is None:
            return False
        try:
            write_message(child.stdin, {
                "op": "search",
                "needle": request.needle,
                "pages": list(request.pages),
                "token": request.token,
            })
            header, _ = read_message(child.stdout)
        except Exception as exc:
            if not self._closed:
                GLib.idle_add(
                    self._deliver_error,
                    f"Görüntüleyici arama sırasında kapandı: {exc}",
                )
            return False

        if header.get("op") == "search-result":
            callback = self._search_callbacks.pop(request.token, None)
            if callback is not None and not self._closed:
                results = header.get("results", {})
                if not results and "rects" in header and len(request.pages) == 1:
                    results = {request.pages[0]: header.get("rects", [])}
                GLib.idle_add(callback, results)
        return True

    def _text_pass(self, request: TextPassRequest) -> bool:
        child = self._child
        if child is None:
            return False
        try:
            write_message(child.stdin, {
                "op": "extract-text",
                "pages": list(request.pages),
                "token": request.token,
            })
            header, _ = read_message(child.stdout)
        except Exception as exc:
            if not self._closed:
                GLib.idle_add(
                    self._deliver_error,
                    f"Görüntüleyici metin çıkarma sırasında kapandı: {exc}",
                )
            return False

        if header.get("op") == "text-result":
            callback = self._text_callbacks.pop(request.token, None)
            if callback is not None and not self._closed:
                texts = header.get("texts", {})
                # texts keys in JSON are strings or ints
                parsed_texts = {int(k): v for k, v in texts.items()}
                GLib.idle_add(callback, parsed_texts)
        return True

    def _check_pressure(self) -> None:
        try:
            import psutil

            total = psutil.Process().memory_info().rss
            if self._child is not None:
                try:
                    total += psutil.Process(self._child.pid).memory_info().rss
                except Exception:
                    pass
        except Exception:
            return
        if total / (1024 * 1024) < RSS_CEILING_MB:
            return
        if self._on_pressure is not None:
            GLib.idle_add(self._on_pressure, total / (1024 * 1024))

    # -- delivery (main loop) ---------------------------------------------

    def _deliver_ready(self, info) -> bool:
        if not self._closed:
            self._on_ready(info)
        return GLib.SOURCE_REMOVE

    def _deliver_error(self, message: str) -> bool:
        if self._on_error is not None and not self._closed:
            self._on_error(message)
        return GLib.SOURCE_REMOVE

    def _deliver_result(self, request, header, payload) -> bool:
        """
        Wrap the rendered bytes in a texture and hand it on.

        A result whose generation has since gone stale is still delivered rather
        than dropped: the bytes are already paid for and the texture is valid
        for its own key, so the cache keeps it and returning to that zoom or page
        is instant. What must not happen is painting it as if it were current,
        and that is the receiver's job -- a `PageView` compares the result's key
        against the one it is waiting for.
        """
        if self._closed:
            return GLib.SOURCE_REMOVE
        from gi.repository import Gdk

        from .requests import RenderResult

        texture = Gdk.MemoryTexture.new(
            header["width"], header["height"], Gdk.MemoryFormat.R8G8B8,
            GLib.Bytes.new(payload), header["stride"],
        )
        self._on_result(RenderResult(
            request=request,
            texture=texture,
            width=header["width"],
            height=header["height"],
            origin_x=header["origin_x"],
            origin_y=header["origin_y"],
            page_w=header["page_w"],
            page_h=header["page_h"],
            scale=header["scale"],
            render_ms=header["ms"],
            nbytes=len(payload),
        ))
        return GLib.SOURCE_REMOVE
