"""
End-to-end checks against a real server process.

Every check asserts. The previous version printed what it got and then printed
"ALL SERVER TESTS PASSED" whatever that was, so a 404 or an empty manifest read
as a pass.

Nothing here talks to the publisher: the sync endpoint is only read, never
posted to, because a POST starts a 155-request crawl of their catalogue.
"""
import gzip
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"

# The book the fixtures below belong to, and one activity extracted from it.
BOOK = "0e966773-5012-4f57-8be5-d892e8c75f22"
ACTIVITY = "5303510f-4ff7-4c40-bfba-ad0cae290efd"

passed = 0


def check(name, fn):
    global passed
    fn()
    passed += 1
    print(f"✔ {name}")


def get(path, headers=None, timeout=10):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


def status_of(path, headers=None):
    """The HTTP status for a path, without raising on a 4xx/5xx."""
    try:
        with get(path, headers) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def wait_for_server(proc, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"server exited early with {proc.returncode}")
        try:
            with get("/api/status", timeout=2):
                return
        except Exception:
            time.sleep(0.25)
    raise AssertionError("server did not answer /api/status in time")


proc = subprocess.Popen(
    [sys.executable, "server.py", str(PORT), "books/full_pdf.pdf"],
    cwd=ROOT,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

try:
    wait_for_server(proc)

    def t_status():
        with get("/api/status") as r:
            assert r.status == 200, r.status
            json.loads(r.read().decode())

    def t_meta():
        with get(f"/api/activities/meta?book_id={BOOK}") as r:
            oges = json.loads(r.read().decode()).get("oges", [])
        assert oges, "the manifest for a catalogued book must not come back empty"
        act = next((o for o in oges if o.get("data") == ACTIVITY), None)
        assert act is not None, f"{ACTIVITY} is missing from the manifest"
        assert isinstance(act.get("is_installed"), bool)

    def t_meta_unknown_book():
        # A book the publisher does not list is a 404, not an empty manifest:
        # "no activities" and "could not be read" are different answers.
        code = status_of("/api/activities/meta?book_id=00000000-0000-0000-0000-000000000000")
        assert code == 404, code

    def t_regions():
        with get(f"/api/activities/regions?book_id={BOOK}") as r:
            baked = json.loads(r.read().decode())
        assert baked["bookId"] == BOOK
        assert baked["version"] >= 1
        assert baked["pageCount"] > 0
        assert baked["pages"], "a baked book must carry pages"
        page = next(iter(baked["pages"].values()))
        assert page["activities"], "a baked page must carry activities"
        first = page["activities"][0]
        assert len(first["rect"]) == 4
        # Pieces are carried whole and never collapsed into one box.
        assert isinstance(first["parts"], list) and first["parts"]

    def t_regions_gzip():
        with get(f"/api/activities/regions?book_id={BOOK}",
                 {"Accept-Encoding": "gzip"}) as r:
            body = r.read()
            encoding = r.headers.get("Content-Encoding")
        assert encoding == "gzip", f"expected a gzipped body, got {encoding!r}"
        json.loads(gzip.decompress(body).decode())

    def t_regions_missing():
        # A well-formed id with no bake behind it, and a malformed one.
        assert status_of(
            "/api/activities/regions?book_id=00000000-0000-0000-0000-000000000000"
        ) == 404
        assert status_of("/api/activities/regions?book_id=not-a-guid") == 400

    def t_activity_html():
        with get(f"/activities/{ACTIVITY}/index.html") as r:
            assert r.status == 200, r.status
            assert "text/html" in (r.headers.get("Content-Type") or "")
            assert len(r.read()) > 0

    def t_activity_range():
        req = urllib.request.Request(
            f"{BASE}/activities/{ACTIVITY}/1.mp3", headers={"Range": "bytes=0-1023"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            chunk = r.read()
            assert r.status == 206, r.status
            assert r.headers.get("Content-Range"), "a partial response must say which part"
            assert len(chunk) == 1024, len(chunk)

    def t_extraction_endpoints_removed():
        assert status_of("/api/activities/sync") == 404
        assert status_of(f"/api/activities/status?book_id={BOOK}") == 404
        assert status_of(f"/api/activities/fetch?guid={ACTIVITY}") == 404

    check("GET /api/status answers JSON", t_status)
    check("GET /api/activities/meta returns the book's manifest", t_meta)
    check("GET /api/activities/meta 404s for a book off the catalogue", t_meta_unknown_book)
    check("GET /api/activities/regions serves the bake", t_regions)
    check("GET /api/activities/regions gzips when asked", t_regions_gzip)
    check("GET /api/activities/regions 404s with no bake, 400s on a bad id", t_regions_missing)
    check("extracted activity HTML is served", t_activity_html)
    check("activity audio answers a range request", t_activity_range)
    check("extraction endpoints (/api/activities/sync, status, fetch) are removed from app server", t_extraction_endpoints_removed)

    print(f"\nAll {passed} server tests passed.")
finally:
    proc.terminate()
    proc.wait()
