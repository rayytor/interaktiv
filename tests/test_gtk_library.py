#!/usr/bin/env python3
"""
The library layer of the native School Edition.

Nothing here opens a window: the parts under test are the catalogue rules
(which books a filter shows, ported from `js/dashboard.js`), the download
plumbing `BooksManager` grew for previews, the preview cache, and the settings
file. The widgets that draw them are exercised by running the app.
"""

import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from books_manager import BooksManager  # noqa: E402
from interaktiv_core import jobs  # noqa: E402
from interaktiv_gtk.library.model import (  # noqa: E402
    EMPTY_NO_INSTALLS,
    EMPTY_NO_MATCHES,
    FILTERS,
    BookItem,
    LibraryModel,
)


class FakeManager:
    """Just enough of `BooksManager` for the catalogue rules."""

    library_mode = False

    def __init__(self, records):
        self._records = records

    def get_all_books(self):
        return [dict(r) for r in self._records]


def record(book_id, title, grade, grade_label, installed=False, size=None, confidence=None):
    return {
        "id": book_id,
        "title": title,
        "grade": grade,
        "gradeLabel": grade_label,
        "subject": None,
        "pageCount": None,
        "interactiveCount": None,
        "url": f"https://example.invalid/Etkilesimlikitap/{book_id}/pdf.pdf",
        "index": 1,
        "isInstalled": installed,
        "fileSize": size,
        "confidence": confidence,
        "hasThumbnail": False,
        "download": None,
    }


SAMPLE = [
    record("a", "Biyoloji Ders Kitabı", 9, "9. Sınıf", installed=True, size=108 * 1024 * 1024),
    record("b", "Fizik Ders Kitabı", 9, "9. Sınıf"),
    record("c", "Matematik Ders Kitabı", 10, "10. Sınıf", installed=True, size=95 * 1024 * 1024),
    record("d", "Tarih Ders Kitabı", 12, "12. Sınıf"),
    record("e", "Astronomi ve Uzay Bilimleri", 0, "Seçmeli Dersler"),
]


class TestFilters(unittest.TestCase):
    """The seven tabs, answering exactly as `dashboard.js:render` does."""

    def setUp(self):
        self.model = LibraryModel(FakeManager(SAMPLE))
        self.model.reload()

    def titles(self, sections):
        return [(s.key, [i.id for i in s.items]) for s in sections]

    def test_all_lifts_installed_to_its_own_group(self):
        sections = self.model.sections("all", "")
        self.assertEqual(
            self.titles(sections),
            [
                ("installed", ["a", "c"]),
                ("grade-9", ["b"]),
                ("grade-12", ["d"]),
                ("grade-0", ["e"]),
            ],
        )

    def test_all_never_shows_a_book_twice(self):
        sections = self.model.sections("all", "")
        seen = [item.id for s in sections for item in s.items]
        self.assertEqual(sorted(seen), sorted(r["id"] for r in SAMPLE))
        self.assertEqual(len(seen), len(set(seen)))

    def test_installed_shows_no_grade_groups(self):
        sections = self.model.sections("installed", "")
        self.assertEqual(self.titles(sections), [("installed", ["a", "c"])])

    def test_grade_tab_keeps_installed_and_uninstalled_together(self):
        # `renderInstalledSection` hides itself for a grade tab, so grade 9
        # shows both its books in one group -- not one in each.
        sections = self.model.sections("9", "")
        self.assertEqual(self.titles(sections), [("grade-9", ["a", "b"])])

    def test_elective_tab_is_grade_zero(self):
        sections = self.model.sections("0", "")
        self.assertEqual(self.titles(sections), [("grade-0", ["e"])])

    def test_every_tab_name_resolves(self):
        for name in FILTERS:
            self.model.sections(name, "")  # must not raise

    def test_search_matches_title_and_grade_label(self):
        self.assertEqual(
            [i.id for s in self.model.sections("all", "fizik") for i in s.items], ["b"]
        )
        self.assertEqual(
            sorted(i.id for s in self.model.sections("all", "9. sınıf") for i in s.items),
            ["a", "b"],
        )

    def test_search_survives_turkish_casing(self):
        # "İ".lower() is an "i" plus a combining dot in both Python and
        # JavaScript, so the web dashboard finds nothing for either of these.
        for typed in ("BİYOLOJİ", "Biyoloji", "biyoloji", "BIYOLOJI"):
            self.assertEqual(
                [i.id for s in self.model.sections("all", typed) for i in s.items],
                ["a"],
                f"searching {typed!r} found nothing",
            )

    def test_search_ignores_diacritics_a_plain_keyboard_lacks(self):
        model = LibraryModel(FakeManager([
            record("x", "Coğrafya Ders Kitabı", 9, "9. Sınıf"),
            record("y", "Türk Dili ve Edebiyatı", 9, "9. Sınıf"),
        ]))
        model.reload()
        for typed, expected in (("cografya", "x"), ("COĞRAFYA", "x"),
                                ("turk dili", "y"), ("edebiyati", "y")):
            self.assertEqual(
                [i.id for s in model.sections("all", typed) for i in s.items],
                [expected],
                f"searching {typed!r} found nothing",
            )

    def test_search_and_tab_compose(self):
        self.assertEqual(
            [i.id for s in self.model.sections("installed", "matematik") for i in s.items],
            ["c"],
        )
        self.assertEqual(self.model.sections("installed", "fizik"), [])

    def test_empty_message_depends_on_the_tab(self):
        self.assertEqual(self.model.empty_message("installed"), EMPTY_NO_INSTALLS)
        self.assertEqual(self.model.empty_message("all"), EMPTY_NO_MATCHES)
        self.assertEqual(self.model.empty_message("12"), EMPTY_NO_MATCHES)


class TestItemIdentity(unittest.TestCase):
    def test_reload_keeps_the_same_object_per_book(self):
        manager = FakeManager(SAMPLE)
        model = LibraryModel(manager)
        model.reload()
        first = model.get("a")
        model.reload()
        self.assertIs(model.get("a"), first)

    def test_a_card_hears_about_a_reload(self):
        manager = FakeManager([record("a", "Kitap", 9, "9. Sınıf")])
        model = LibraryModel(manager)
        model.reload()
        beats = []
        model.get("a").connect("changed", lambda _i: beats.append(1))
        manager._records = [record("a", "Kitap", 9, "9. Sınıf", installed=True, size=10)]
        model.reload()
        self.assertEqual(len(beats), 1)
        self.assertTrue(model.get("a").is_installed)

    def test_an_unchanged_reload_is_silent(self):
        model = LibraryModel(FakeManager(SAMPLE))
        model.reload()
        beats = []
        for item in model.items:
            item.connect("changed", lambda _i: beats.append(1))
        model.reload()
        self.assertEqual(beats, [])

    def test_meta_text_matches_the_web_card(self):
        item = BookItem(record("a", "K", 9, "9. Sınıf", installed=True, size=108 * 1024 * 1024))
        self.assertEqual(item.meta_text, "9. Sınıf • 108 MB")
        self.assertEqual(BookItem(record("b", "K", 9, "9. Sınıf")).meta_text, "9. Sınıf")

    def test_progress_is_a_fraction(self):
        item = BookItem(record("a", "K", 9, "9. Sınıf"))
        item.set_download({"status": "downloading", "progress": 42.5, "kind": "preview"})
        self.assertTrue(item.is_downloading)
        self.assertAlmostEqual(item.progress, 0.425)
        self.assertEqual(item.download_kind, "preview")
        item.set_download(None)
        self.assertFalse(item.is_downloading)
        self.assertEqual(item.progress, 0.0)


class _Payload(http.server.BaseHTTPRequestHandler):
    """Serves one deterministic body, slowly enough to be cancellable."""

    BODY = b"%PDF-1.4\n" + b"x" * (3 * 1024 * 1024)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(self.BODY)))
        self.end_headers()
        for offset in range(0, len(self.BODY), 128 * 1024):
            try:
                self.wfile.write(self.BODY[offset:offset + 128 * 1024])
            except (BrokenPipeError, ConnectionResetError):
                return
            time.sleep(0.01)

    def log_message(self, *_args):
        pass


class TestDownloads(unittest.TestCase):
    """`download_to`, the one thing `books_manager.py` grew for the GTK app."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Payload)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/pdf.pdf"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="interaktiv-dl-")
        with open(os.path.join(self.base, "kitap_pdf_linkleri.txt"), "w", encoding="utf-8") as f:
            f.write("Test Kitabı: https://example.invalid/Etkilesimlikitap/"
                    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/pdf.pdf\n")
        self.manager = BooksManager(base_dir=self.base, edition="full")
        self.book_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        # The catalogue's URL is the publisher's; the transfer under test is not.
        self.manager.books_by_id[self.book_id]["url"] = self.url

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _wait(self, predicate, timeout=30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_download_to_writes_where_it_is_told(self):
        target = os.path.join(self.base, "cache", "preview.pdf")
        done = []
        ok, _ = self.manager.download_to(self.book_id, target, on_done=done.append)
        self.assertTrue(ok)
        self.assertTrue(self._wait(lambda: done), "download never finished")
        self.assertEqual(done[0], target)
        self.assertEqual(os.path.getsize(target), len(_Payload.BODY))
        # A preview does not install the book.
        self.assertFalse(self.manager.is_installed(self.book_id))
        self.assertFalse(os.path.exists(target + ".part"))

    def test_progress_is_reported_while_it_runs(self):
        target = os.path.join(self.base, "preview.pdf")
        seen = []

        def watch():
            while not done:
                info = self.manager.get_download_statuses().get(self.book_id)
                if info:
                    seen.append((info["progress"], info["total_bytes"], info["kind"]))
                time.sleep(0.02)

        done = []
        watcher = threading.Thread(target=watch, daemon=True)
        self.manager.download_to(self.book_id, target, on_done=done.append)
        watcher.start()
        self.assertTrue(self._wait(lambda: done))
        watcher.join(timeout=2)

        self.assertTrue(seen, "no progress was ever visible")
        self.assertTrue(any(0 < p < 100 for p, _t, _k in seen), "progress never moved")
        self.assertTrue(all(t == len(_Payload.BODY) for _p, t, _k in seen if t))
        self.assertTrue(all(k == "preview" for _p, _t, k in seen))

    def test_install_reports_its_own_kind(self):
        seen = []

        def watch():
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                info = self.manager.get_download_statuses().get(self.book_id)
                if info:
                    seen.append(info["kind"])
                if self.manager.is_installed(self.book_id):
                    return
                time.sleep(0.02)

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        ok, _ = self.manager.start_download(self.book_id)
        self.assertTrue(ok)
        self.assertTrue(self._wait(lambda: self.manager.is_installed(self.book_id)))
        watcher.join(timeout=2)
        self.assertTrue(seen and all(k == "install" for k in seen))

    def test_cancel_leaves_nothing_behind(self):
        target = os.path.join(self.base, "preview.pdf")
        done = []
        self.manager.download_to(self.book_id, target, on_done=done.append)
        self.assertTrue(self._wait(
            lambda: (self.manager.get_download_statuses().get(self.book_id) or {})
            .get("downloaded_bytes", 0) > 0
        ), "download never started")

        ok, _ = self.manager.cancel_download(self.book_id)
        self.assertTrue(ok)
        self.assertTrue(self._wait(lambda: done), "cancel never reported back")
        self.assertIsNone(done[0])
        self.assertFalse(os.path.exists(target))
        self.assertFalse(os.path.exists(target + ".part"))
        self.assertNotIn(self.book_id, self.manager.get_download_statuses())

    def test_one_download_at_a_time_per_book(self):
        target = os.path.join(self.base, "preview.pdf")
        self.manager.download_to(self.book_id, target)
        ok, message = self.manager.start_download(self.book_id)
        self.assertTrue(ok)
        self.assertIn("progress", message)
        self.manager.cancel_download(self.book_id)


class TestPreviewCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="interaktiv-cache-")
        self._saved = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = self.tmp

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, book_id, size, age_seconds=0):
        path = jobs.preview_path(book_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\0" * size)
        when = time.time() - age_seconds
        os.utime(path, (when, when))
        return path

    def test_path_is_under_the_cache_directory(self):
        self.assertTrue(jobs.preview_path("abc").startswith(self.tmp))

    def test_cached_preview_reports_only_what_exists(self):
        self.assertIsNone(jobs.cached_preview("abc"))
        path = self._write("abc", 16)
        self.assertEqual(jobs.cached_preview("abc"), path)

    def test_prune_keeps_the_newest_and_the_file_just_written(self):
        old = self._write("old", 16, age_seconds=900)
        middle = self._write("middle", 16, age_seconds=300)
        fresh = self._write("fresh", 16, age_seconds=0)
        jobs.prune_previews(keep=fresh)
        # Two files allowed in total, and `keep` is one of them.
        self.assertTrue(os.path.exists(fresh))
        self.assertTrue(os.path.exists(middle))
        self.assertFalse(os.path.exists(old))

    def test_prune_evicts_over_the_byte_budget(self):
        keep = self._write("fresh", 8)
        big = self._write("big", jobs.PREVIEW_MAX_BYTES + 1, age_seconds=10)
        jobs.prune_previews(keep=keep)
        self.assertTrue(os.path.exists(keep))
        self.assertFalse(os.path.exists(big))

    def test_prune_on_an_absent_directory_is_a_no_op(self):
        jobs.prune_previews()  # must not raise


class TestSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="interaktiv-state-")
        self.path = os.path.join(self.tmp, "state.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self):
        from interaktiv_gtk.state import Settings

        return Settings(self.path)

    def test_defaults_when_there_is_no_file(self):
        settings = self._settings()
        self.assertEqual(settings.get("library_filter"), "all")
        self.assertFalse(settings.get("link_confidence_gate"))

    def test_round_trip(self):
        settings = self._settings()
        settings.set("library_filter", "12")
        settings.set_last_page("book-1", 42)
        settings.flush()

        reloaded = self._settings()
        self.assertEqual(reloaded.get("library_filter"), "12")
        self.assertEqual(reloaded.last_page("book-1"), 42)
        self.assertEqual(reloaded.last_page("book-2"), 1)

    def test_an_unreadable_file_falls_back_to_defaults(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(self._settings().get("library_filter"), "all")

    def test_a_key_this_build_does_not_know_survives(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"from_a_newer_build": 7}, f)
        settings = self._settings()
        settings.set("library_filter", "9")
        settings.flush()
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["from_a_newer_build"], 7)


class TestBookCard(unittest.TestCase):
    """Book card and cover thumbnail sizing rules in the dashboard."""

    def test_picture_content_fit_is_contain(self):
        from gi.repository import Gtk
        from interaktiv_gtk.library.card import BookCard
        from interaktiv_gtk.library.covers import CoverLoader

        card = BookCard(CoverLoader(FakeManager([])))
        self.assertEqual(card.picture.get_content_fit(), Gtk.ContentFit.CONTAIN)

    def test_aspect_cover_measures_height_for_width(self):
        from gi.repository import Gtk
        from interaktiv_gtk.library.card import AspectCover, COVER_ASPECT_RATIO

        cover = AspectCover(COVER_ASPECT_RATIO)
        self.assertEqual(
            cover.get_request_mode(),
            Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH,
        )
        _, nat_h_232, _, _ = cover.measure(Gtk.Orientation.VERTICAL, 232)
        self.assertEqual(nat_h_232, int(round(232 * COVER_ASPECT_RATIO)))

        _, nat_h_300, _, _ = cover.measure(Gtk.Orientation.VERTICAL, 300)
        self.assertEqual(nat_h_300, int(round(300 * COVER_ASPECT_RATIO)))


if __name__ == "__main__":
    unittest.main(verbosity=2)

