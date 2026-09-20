"""
End-to-end checks against a real server process.

Every check asserts. Wrapped in unittest.TestCase so pytest and unittest
runners properly collect, execute, and report test results.

Nothing here talks to the publisher: the sync endpoint is only read, never
posted to, because a POST starts a 155-request crawl of their catalogue.
"""
import gzip
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"

# The book the fixtures below belong to, and one activity extracted from it.
BOOK = "0e966773-5012-4f57-8be5-d892e8c75f22"
ACTIVITY = "5303510f-4ff7-4c40-bfba-ad0cae290efd"


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


class TestActivitiesServer(unittest.TestCase):
    proc = None

    @classmethod
    def setUpClass(cls):
        cls.proc = subprocess.Popen(
            [sys.executable, "server.py", str(PORT), "books/full_pdf.pdf"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_for_server(cls.proc)
        except Exception:
            cls.proc.terminate()
            cls.proc.wait()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.proc is not None:
            cls.proc.terminate()
            cls.proc.wait()

    def test_status(self):
        with get("/api/status") as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode())
            self.assertIsInstance(data, dict)

    def test_meta(self):
        with get(f"/api/activities/meta?book_id={BOOK}") as r:
            oges = json.loads(r.read().decode()).get("oges", [])
        self.assertTrue(oges, "the manifest for a catalogued book must not come back empty")
        act = next((o for o in oges if o.get("data") == ACTIVITY), None)
        self.assertIsNotNone(act, f"{ACTIVITY} is missing from the manifest")
        self.assertIsInstance(act.get("is_installed"), bool)

    def test_meta_unknown_book(self):
        # A book the publisher does not list is a 404, not an empty manifest:
        # "no activities" and "could not be read" are different answers.
        code = status_of("/api/activities/meta?book_id=00000000-0000-0000-0000-000000000000")
        self.assertEqual(code, 404)

    def test_regions(self):
        with get(f"/api/activities/regions?book_id={BOOK}") as r:
            baked = json.loads(r.read().decode())
        self.assertEqual(baked["bookId"], BOOK)
        self.assertGreaterEqual(baked["version"], 1)
        self.assertGreater(baked["pageCount"], 0)
        self.assertTrue(baked["pages"], "a baked book must carry pages")
        page = next(iter(baked["pages"].values()))
        self.assertTrue(page["activities"], "a baked page must carry activities")
        first = page["activities"][0]
        self.assertEqual(len(first["rect"]), 4)
        # Pieces are carried whole and never collapsed into one box.
        self.assertIsInstance(first["parts"], list)
        self.assertTrue(first["parts"])

    def test_regions_gzip(self):
        with get(f"/api/activities/regions?book_id={BOOK}",
                 {"Accept-Encoding": "gzip"}) as r:
            body = r.read()
            encoding = r.headers.get("Content-Encoding")
        self.assertEqual(encoding, "gzip", f"expected a gzipped body, got {encoding!r}")
        data = json.loads(gzip.decompress(body).decode())
        self.assertEqual(data["bookId"], BOOK)

    def test_regions_missing(self):
        # A well-formed id with no bake behind it, and a malformed one.
        self.assertEqual(
            status_of("/api/activities/regions?book_id=00000000-0000-0000-0000-000000000000"),
            404,
        )
        self.assertEqual(status_of("/api/activities/regions?book_id=not-a-guid"), 400)

    def test_activity_html(self):
        with get(f"/activities/{ACTIVITY}/index.html") as r:
            self.assertEqual(r.status, 200)
            self.assertIn("text/html", r.headers.get("Content-Type") or "")
            self.assertGreater(len(r.read()), 0)

    def test_activity_range(self):
        req = urllib.request.Request(
            f"{BASE}/activities/{ACTIVITY}/1.mp3", headers={"Range": "bytes=0-1023"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            chunk = r.read()
            self.assertEqual(r.status, 206)
            self.assertTrue(r.headers.get("Content-Range"), "a partial response must say which part")
            self.assertEqual(len(chunk), 1024)

    def test_extraction_endpoints_removed(self):
        self.assertEqual(status_of("/api/activities/sync"), 404)
        self.assertEqual(status_of(f"/api/activities/status?book_id={BOOK}"), 404)
        self.assertEqual(status_of(f"/api/activities/fetch?guid={ACTIVITY}"), 404)


if __name__ == "__main__":
    unittest.main()
