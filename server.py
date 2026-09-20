#!/usr/bin/env python3
"""
Lightweight threaded HTTP server with RFC 7233 Range support for efficient PDF streaming.
Zero external dependencies (uses Python standard library only).
"""

import base64
import http.server
import json
import mimetypes
import gzip
import os
import re
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from books_manager import BooksManager

# Ensure logs are flushed immediately to stdout/log files
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# Ensure .mjs, PDF, and CSS mime types are correctly registered
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/pdf", ".pdf")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("audio/wav", ".wav")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/webm", ".webm")
mimetypes.add_type("text/html", ".html")

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler supporting RFC 7233 Range requests (HTTP 206)."""

    server_version = "InteraktivPDF/1.0"

    def end_headers(self):
        # Enable CORS and inform clients that Byte Ranges are accepted
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length, Accept-Ranges")
        super().end_headers()

    def _trigger_jit_bake(self, book_id):
        """Trigger a non-blocking background bake using scan.py for an unbaked local book."""
        if not hasattr(self.server, "active_bakes"):
            self.server.active_bakes = set()
            self.server.active_bakes_lock = threading.Lock()

        with self.server.active_bakes_lock:
            if book_id in self.server.active_bakes:
                return
            local_pdf = self.server.books_manager.get_local_path(book_id)
            if not local_pdf or not os.path.isfile(local_pdf):
                return
            scan_script = os.path.join(self.server.books_manager.base_dir, "tools", "hotspot_extraction", "scan.py")
            if not os.path.isfile(scan_script):
                return
            self.server.active_bakes.add(book_id)

        def _run():
            try:
                cmd = [
                    sys.executable,
                    scan_script,
                    "--only", book_id,
                    "--pdf", local_pdf,
                    "--quiet",
                ]
                subprocess.run(
                    cmd,
                    cwd=self.server.books_manager.base_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
            finally:
                with self.server.active_bakes_lock:
                    self.server.active_bakes.discard(book_id)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # API Endpoints
        if path == "/api/config":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            default_pdf = self.server.default_pdf if hasattr(self.server, "default_pdf") else None
            show_dashboard = (default_pdf is None or default_pdf == "")
            book_id = None
            if default_pdf and hasattr(self.server, "books_manager"):
                book_id = self.server.books_manager.book_id_for_path(default_pdf)
            edition = getattr(self.server, "edition", "full")
            library_mode = bool(getattr(self.server, "library_mode", False))
            payload = {
                "default_pdf": default_pdf,
                "show_dashboard": show_dashboard,
                "default_view_mode": "book",
                "filename": os.path.basename(default_pdf) if default_pdf else None,
                # Which catalogue book this file is, so its interactive
                # activities can be linked to it however it was opened.
                "book_id": book_id,
                # Which build this is. The reader uses it to decide how much UI
                # to show (a school board wants large, simple controls) and
                # whether a book with no bake may be scanned live.
                "edition": edition,
                "library_mode": library_mode,
                "features": {
                    # Downloading/uninstalling books are enabled unless in a read-only packaged library.
                    "install": not library_mode,
                    "uninstall": not library_mode,
                    # Authoring / extraction operations are separate tools, not in the app server
                    "catalogue_sync": False,
                    "bulk_extract": False,
                    # Activities themselves still work (pre-extracted or directly via CDN iframe)
                    "activities": True,
                    "jit_activities": False,
                    "live_detection": not library_mode,
                    "logs": not library_mode and edition != "school",
                },
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"status":"ok"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/perf_metrics":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            log = getattr(self.server, "range_log", [])
            body = json.dumps({"range_log": log}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/client_perf":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            log = getattr(self.server, "client_perf_log", [])
            body = json.dumps({"client_perf": log}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/perf_reset":
            self.server.range_log = []
            self.server.client_perf_log = []
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"status":"ok"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/books":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            books = self.server.books_manager.get_all_books()
            body = json.dumps({"books": books}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/books/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            statuses = self.server.books_manager.get_download_statuses()
            body = json.dumps({"downloads": statuses}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/books/install":
            if self.refuse_in_library("Downloading a book"):
                return
            book_id = query.get("id", [None])[0]
            ok, msg = self.server.books_manager.start_download(book_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = json.dumps({"success": ok, "message": msg}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/books/cancel":
            if self.refuse_in_library("Cancelling a download"):
                return
            book_id = query.get("id", [None])[0]
            ok, msg = self.server.books_manager.cancel_download(book_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = json.dumps({"success": ok, "message": msg}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/books/uninstall":
            if self.refuse_in_library("Uninstalling a book"):
                return
            book_id = query.get("id", [None])[0]
            ok, msg = self.server.books_manager.uninstall_book(book_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = json.dumps({"success": ok, "message": msg}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/thumbnail":
            book_id = query.get("id", [None])[0]
            thumb_path = self.server.books_manager.get_thumbnail_path(book_id) if book_id else None
            if thumb_path and os.path.isfile(thumb_path):
                self.serve_file_with_range_direct(thumb_path)
                return
            else:
                self.send_error(404, "Thumbnail not found")
                return

        if path == "/api/stream":
            book_id = query.get("id", [None])[0]
            if not book_id:
                self.send_error(400, "Missing book id")
                return

            local_path = self.server.books_manager.get_local_path(book_id)
            if local_path and os.path.isfile(local_path):
                self.serve_file_with_range_direct(local_path)
                return

            book = self.server.books_manager.books_by_id.get(book_id)
            if not book:
                self.send_error(404, "Book not found")
                return

            self.proxy_remote_stream(book["url"])
            return

        session_id = query.get("session", ["default"])[0]

        if path == "/api/heartbeat":
            if hasattr(self.server, "record_heartbeat"):
                self.server.record_heartbeat(session_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"status":"ok"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/pagehide":
            if hasattr(self.server, "record_pagehide"):
                self.server.record_pagehide(session_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"status":"ok"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/activities/regions":
            book_id = query.get("book_id", [None])[0]
            if not book_id or not re.fullmatch(r"[A-Fa-f0-9\-]{36}", book_id):
                self.send_error(400, "Missing or malformed book_id")
                return
            # The full edition bakes into `activities/books/`; a packaged
            # library carries the bake inside the bundle. Which one it is is the
            # manager's question, not this handler's.
            regions_path = self.server.books_manager.get_regions_path(book_id)
            if not os.path.isfile(regions_path):
                # If the PDF is installed locally, trigger a fast JIT background bake
                # so subsequent requests or reloads will have baked regions ready.
                self._trigger_jit_bake(book_id)
                # Not an error, but the meaning differs by edition: in the full
                # edition a book with no bake is served live by the detector in
                # the browser; in the school edition every packaged book has one
                # and this is a broken bundle.
                self.send_error(404, "No baked regions for this book")
                return
            with open(regions_path, "rb") as f:
                body = f.read()
            # A book's regions run to a few hundred KB of JSON and compress by
            # about 8x, and this is fetched once per book opened.
            encoding = None
            if "gzip" in self.headers.get("Accept-Encoding", ""):
                body = gzip.compress(body, 6)
                encoding = "gzip"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/activities/meta":
            book_id = query.get("book_id", [None])[0]
            if not book_id:
                self.send_error(400, "Missing book_id")
                return
            try:
                oges = self.server.books_manager.get_book_oges(book_id)
            except LookupError:
                self.send_error(404, "Book not listed in the publisher's catalogue")
                return
            except Exception as e:
                # A manifest that could not be fetched or parsed is a failure,
                # not a book without activities.
                self.send_error(502, f"Could not read the activity manifest: {e}")
                return
            enriched = []
            for o in oges:
                item = dict(o)
                guid = o.get("data")
                item["is_installed"] = self.server.books_manager.is_activity_installed(guid) if guid else False
                enriched.append(item)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = json.dumps({"oges": enriched}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return


        if path == "/api/shutdown":
            if hasattr(self.server, "request_shutdown"):
                self.server.request_shutdown()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"status":"shutting_down"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Serve static files with Range support
        self.serve_file_with_range(path)

    @property
    def library_mode(self):
        return bool(getattr(self.server, "library_mode", False))

    def refuse_in_library(self, feature):
        """
        Answer an authoring endpoint that a packaged library cannot serve.

        Refused here rather than in the manager so the client gets a clear
        reason and the response shape it expects, instead of a silent no-op
        that looks like a failure it should retry.
        """
        if not self.library_mode:
            return False
        self.send_json(
            {
                "success": False,
                "error": "unsupported_in_edition",
                "message": f"{feature} is not available in the school edition",
            },
            status=403,
        )
        return True

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/thumbnail":
            book_id = query.get("id", [None])[0]
            if not book_id:
                self.send_error(400, "Missing book id")
                return
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self.send_error(400, "Empty payload")
                return
            data = self.rfile.read(content_length)
            if data.startswith(b"data:image/"):
                try:
                    _, b64 = data.split(b",", 1)
                    img_bytes = base64.b64decode(b64)
                    self.server.books_manager.save_thumbnail(book_id, img_bytes)
                except Exception as e:
                    self.send_error(400, f"Invalid image: {e}")
                    return
            else:
                self.server.books_manager.save_thumbnail(book_id, data)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"success":true}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return


        if path == "/api/client_perf":
            content_length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                parsed_perf = json.loads(data.decode("utf-8"))
            except Exception:
                parsed_perf = {}
            if not hasattr(self.server, "client_perf_log"):
                self.server.client_perf_log = []
            self.server.client_perf_log.append({
                "stage": query.get("stage", [""])[0],
                "data": parsed_perf,
                "time": time.time(),
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            body = b'{"success":true}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/api/"):
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                self.rfile.read(content_length)
            self.do_GET()
            return

        self.send_error(405, "Method Not Allowed")

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/stream":
            book_id = query.get("id", [None])[0]
            local_path = self.server.books_manager.get_local_path(book_id) if book_id else None
            if local_path and os.path.isfile(local_path):
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(os.path.getsize(local_path)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return

            book = self.server.books_manager.books_by_id.get(book_id) if book_id else None
            if book:
                try:
                    req = urllib.request.Request(
                        book["url"],
                        headers={"Range": "bytes=0-0", "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Interaktiv/1.0"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        cr = resp.headers.get("Content-Range", "")
                        total_len = cr.split("/")[-1] if "/" in cr else "0"
                        self.send_response(200)
                        self.send_header("Content-Type", "application/pdf")
                        self.send_header("Content-Length", total_len)
                        self.send_header("Accept-Ranges", "bytes")
                        self.end_headers()
                        return
                except Exception:
                    pass
            self.send_error(404, "Book not found")
            return

        if path.startswith("/api/"):
            self.send_response(200)
            self.end_headers()
            return

        super().do_HEAD()

    def proxy_remote_stream(self, remote_url):
        """Proxy remote PDF requests forwarding Range headers with CORS support."""
        range_header = self.headers.get("Range")

        # Request upstream with or without Range
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Interaktiv/1.0",
        }
        if range_header:
            headers["Range"] = range_header

        req = urllib.request.Request(remote_url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = resp.status
                self.send_response(status)
                self.send_header("Content-Type", "application/pdf")
                if resp.headers.get("Content-Range"):
                    self.send_header("Content-Range", resp.headers.get("Content-Range"))
                if resp.headers.get("Content-Length"):
                    self.send_header("Content-Length", resp.headers.get("Content-Length"))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()

                chunk_size = 64 * 1024
                while True:
                    buf = resp.read(chunk_size)
                    if not buf:
                        break
                    self.wfile.write(buf)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            if e.headers.get("Content-Range"):
                self.send_header("Content-Range", e.headers.get("Content-Range"))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self.send_error(502, f"Proxy error: {e}")
            except Exception:
                pass

    def serve_file_with_range_direct(self, filepath):
        """Serve a specific local file path with RFC 7233 range support."""
        if not os.path.exists(filepath) or os.path.isdir(filepath):
            self.send_error(404, "File not found")
            return

        file_size = os.path.getsize(filepath)
        content_type, _ = mimetypes.guess_type(filepath)
        if not content_type:
            content_type = "application/octet-stream"

        is_pdf = filepath.lower().endswith(".pdf")
        is_thumb = filepath.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        cache_control = "public, max-age=86400" if (is_pdf or is_thumb) else "no-store, no-cache, must-revalidate, max-age=0"

        range_header = self.headers.get("Range")

        if range_header:
            match = RANGE_RE.match(range_header.strip())
            if not match:
                self.send_error(400, "Invalid Range Header")
                return

            first, last = match.groups()

            if not first and not last:
                self.send_error(400, "Invalid Range Header")
                return

            if not first:
                suffix_len = int(last)
                if suffix_len == 0:
                    start = file_size
                    end = file_size - 1
                elif suffix_len > file_size:
                    start = 0
                    end = file_size - 1
                else:
                    start = file_size - suffix_len
                    end = file_size - 1
            elif not last:
                start = int(first)
                end = file_size - 1
            else:
                start = int(first)
                end = int(last)

            if start > end or start >= file_size or end >= file_size or start < 0:
                self.send_response(416, "Range Not Satisfiable")
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            length = end - start + 1

            self.send_response(206, "Partial Content")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", cache_control)
            if not is_pdf and not is_thumb:
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
            self.end_headers()
            t_req_start = time.perf_counter()
            t_read_total = 0.0
            t_write_total = 0.0
            try:
                with open(filepath, "rb") as f:
                    t_seek_0 = time.perf_counter()
                    f.seek(start)
                    t_seek_ms = (time.perf_counter() - t_seek_0) * 1000
                    bytes_remaining = length
                    chunk_size = 64 * 1024
                    while bytes_remaining > 0:
                        read_bytes = min(bytes_remaining, chunk_size)
                        t_r0 = time.perf_counter()
                        buf = f.read(read_bytes)
                        t_read_total += (time.perf_counter() - t_r0)
                        if not buf:
                            break
                        t_w0 = time.perf_counter()
                        self.wfile.write(buf)
                        t_write_total += (time.perf_counter() - t_w0)
                        bytes_remaining -= len(buf)
                t_req_end = time.perf_counter()
                if not hasattr(self.server, "range_log"):
                    self.server.range_log = []
                self.server.range_log.append({
                    "path": os.path.basename(filepath),
                    "range": f"bytes {start}-{end}/{file_size}",
                    "length": length,
                    "duration_ms": (t_req_end - t_req_start) * 1000,
                    "seek_ms": t_seek_ms,
                    "read_ms": t_read_total * 1000,
                    "write_ms": t_write_total * 1000,
                })
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Cache-Control", cache_control)
            if not is_pdf and not is_thumb:
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
            self.end_headers()

            try:
                with open(filepath, "rb") as f:
                    chunk_size = 64 * 1024
                    while True:
                        buf = f.read(chunk_size)
                        if not buf:
                            break
                        self.wfile.write(buf)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def resolve_activity_asset(self, clean_path):
        """
        Map `/activities/<guid>/<rest>` to a real file.

        The directory is the manager's answer -- JIT cache first, then a bundle
        that shipped the activity -- and the rest is confined to it, so a
        `..` in the URL cannot climb out of an activity.
        """
        manager = getattr(self.server, "books_manager", None)
        parts = clean_path.split("/", 2)
        if len(parts) < 2 or not parts[1]:
            return None
        guid = parts[1]
        rest = parts[2] if len(parts) > 2 else "index.html"
        if not re.fullmatch(r"[A-Fa-f0-9\-]{36}", guid):
            return None
        root = manager.activity_dir(guid) if manager else None
        if not root:
            return None
        root = os.path.abspath(root)
        filepath = os.path.abspath(os.path.join(root, rest))
        if not filepath.startswith(root + os.sep):
            return None
        if not os.path.isfile(filepath):
            return None
        return filepath

    def serve_file_with_range(self, url_path):
        clean_path = urllib.parse.unquote(url_path.lstrip("/"))
        if not clean_path:
            clean_path = "index.html"

        # Interactive activity assets are fetched JIT, so each activity lives in
        # its own directory -- the writable cache, or a bundle that shipped it --
        # neither of which is necessarily under the app directory. In the school
        # edition the app tree may be read-only. Anything else resolves against
        # the app directory as before.
        if clean_path == "activities" or clean_path.startswith("activities/"):
            filepath = self.resolve_activity_asset(clean_path)
            if filepath is None:
                self.send_error(404, "Activity asset not found")
                return
            self.serve_file_with_range_direct(filepath)
            return

        base_dir = os.path.abspath(self.directory if hasattr(self, "directory") and self.directory else ".")
        filepath = os.path.abspath(os.path.join(base_dir, clean_path))

        # Security check: must reside inside base_dir
        if not filepath.startswith(base_dir):
            self.send_error(403, "Access denied")
            return

        if not os.path.exists(filepath) or os.path.isdir(filepath):
            if os.path.isdir(filepath):
                index_candidate = os.path.join(filepath, "index.html")
                if os.path.exists(index_candidate):
                    filepath = index_candidate
                else:
                    self.send_error(404, "File not found")
                    return
            else:
                self.send_error(404, "File not found")
                return

        self.serve_file_with_range_direct(filepath)


class ThreadedTCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, default_pdf=None, directory=None, auto_shutdown=False,
                 library_dir=None, edition=None, activities_cache_dir=None):
        self.default_pdf = default_pdf
        self.directory = directory or os.getcwd()
        self.auto_shutdown = auto_shutdown
        self.books_manager = BooksManager(
            base_dir=self.directory,
            library_dir=library_dir,
            activities_cache_dir=activities_cache_dir,
            edition=edition,
        )
        self.edition = self.books_manager.edition
        self.library_mode = self.books_manager.library_mode
        # The JIT cache is created up front so that a fetch never races the
        # first request for an activity, and so a read-only app tree is caught
        # at startup rather than at the moment a pupil taps an activity.
        if self.library_mode:
            try:
                os.makedirs(self.books_manager.activities_dir, exist_ok=True)
            except OSError as e:
                print(f"Warning: activity cache is not writable ({e}); activities will load from the CDN only.")
        self.start_time = time.time()
        self.active_sessions = {}  # session_id -> last_seen_time
        self.sessions_lock = threading.Lock()
        self.has_connected = False
        self._empty_since = None
        self._is_shutting_down = False
        super().__init__(server_address, RequestHandlerClass)

        if self.auto_shutdown:
            self._start_watchdog()

    def record_heartbeat(self, session_id="default"):
        with self.sessions_lock:
            self.has_connected = True
            self.active_sessions[session_id] = time.time()
            self._empty_since = None

    def record_pagehide(self, session_id="default"):
        with self.sessions_lock:
            self.active_sessions.pop(session_id, None)
            if not self.active_sessions and self._empty_since is None:
                self._empty_since = time.time()

    def request_shutdown(self):
        if not self._is_shutting_down:
            self._is_shutting_down = True
            threading.Thread(target=self.shutdown_server, daemon=True).start()

    def shutdown_server(self):
        print("\nShutting down Interaktiv server.")
        try:
            self.shutdown()
        except Exception:
            pass

    def _start_watchdog(self):
        def watchdog_loop():
            GRACE_STARTUP = 30.0
            SESSION_TIMEOUT = 25.0
            IDLE_SHUTDOWN_GRACE = 5.0

            while not self._is_shutting_down:
                time.sleep(0.5)
                now = time.time()

                with self.sessions_lock:
                    if not self.has_connected:
                        if now - self.start_time > GRACE_STARTUP:
                            print("No browser connected within startup grace period. Auto-shutting down.")
                            self.request_shutdown()
                            break
                    else:
                        expired = [sid for sid, last_seen in self.active_sessions.items() if now - last_seen > SESSION_TIMEOUT]
                        for sid in expired:
                            del self.active_sessions[sid]

                        if not self.active_sessions:
                            if self._empty_since is None:
                                self._empty_since = now
                            elif now - self._empty_since >= IDLE_SHUTDOWN_GRACE:
                                print(f"All browser sessions closed ({IDLE_SHUTDOWN_GRACE}s grace expired). Auto-shutting down server.")
                                self.request_shutdown()
                                break
                        else:
                            self._empty_since = None

        t = threading.Thread(target=watchdog_loop, daemon=True)
        t.start()


def run_server(port=8080, default_pdf=None, directory=None, auto_shutdown=False,
               library_dir=None, edition=None, activities_cache_dir=None):
    base_dir = directory or os.path.dirname(os.path.abspath(__file__))
    server_address = ("127.0.0.1", port)
    httpd = ThreadedTCPServer(
        server_address,
        RangeHTTPRequestHandler,
        default_pdf=default_pdf,
        directory=base_dir,
        auto_shutdown=auto_shutdown,
        library_dir=library_dir,
        edition=edition,
        activities_cache_dir=activities_cache_dir,
    )
    print(f"Interaktiv PDF Server listening on http://127.0.0.1:{port}")
    print(f"Edition: {httpd.edition}" + (" (packaged library)" if httpd.library_mode else ""))
    if httpd.library_mode:
        print(f"Library: {httpd.books_manager.library_dir}")
        print(f"Activity cache: {httpd.books_manager.activities_dir}")
    if default_pdf:
        print(f"Default PDF: {default_pdf}")
    else:
        print("Starting in Dashboard Mode (no PDF specified).")
    if auto_shutdown:
        print("Auto-shutdown watchdog enabled (shuts down when window closes).")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    pdf = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
    auto_sd = "--auto-shutdown" in sys.argv
    edition = None
    if "--edition" in sys.argv:
        edition = sys.argv[sys.argv.index("--edition") + 1]
    library = None
    if "--library" in sys.argv:
        library = sys.argv[sys.argv.index("--library") + 1]
    run_server(port=port, default_pdf=pdf, auto_shutdown=auto_sd, library_dir=library, edition=edition)
