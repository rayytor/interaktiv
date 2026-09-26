#!/usr/bin/env python3
"""
The reader layer of the native School Edition.

Nothing here opens a window either. What is tested is the arithmetic the reader
runs on -- which pages a spread shows, where next and previous go, how big a
page is drawn -- the texture budget that keeps a board's memory bounded, the
pipe the renderer speaks, and the renderer process itself against a PDF built
on the spot.

The paging tests are worth more than they look. They are the contract between
this build and the web one: a teacher who knows that 42 faces 43 must find 42
facing 43 here too, and the awkward cases -- the cover that stands alone, the
odd last page, typing an odd number into the page box -- are exactly where a
reimplementation drifts.
"""

import os
import struct
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from interaktiv_core.geometry import PageTransform  # noqa: E402
from interaktiv_gtk.reader import paging  # noqa: E402
from interaktiv_gtk.render.cache import TextureCache  # noqa: E402
from interaktiv_gtk.render.document import DocumentHandle  # noqa: E402
from interaktiv_gtk.render.protocol import read_message, write_message  # noqa: E402
from interaktiv_gtk.render.requests import (  # noqa: E402
    MAX_RENDER_PIXELS,
    DocumentInfo,
    RenderRequest,
)

BOOK = "book"
SINGLE = "single"


class TestPairing(unittest.TestCase):
    """`pages_for` -- the port of `renderBookMode`'s spread alignment."""

    def test_cover_stands_alone(self):
        self.assertEqual(paging.pages_for(1, 289, BOOK), [1])

    def test_even_page_faces_the_odd_one_after_it(self):
        self.assertEqual(paging.pages_for(42, 289, BOOK), [42, 43])

    def test_an_odd_page_snaps_back_onto_its_spread(self):
        # Typing 43 into the page box must land on the same sheet as typing 42.
        self.assertEqual(
            paging.pages_for(43, 289, BOOK), paging.pages_for(42, 289, BOOK)
        )

    def test_last_page_of_an_even_book_stands_alone(self):
        self.assertEqual(paging.pages_for(10, 10, BOOK), [10])

    def test_last_page_of_an_odd_book_has_a_partner(self):
        self.assertEqual(paging.pages_for(9, 9, BOOK), [8, 9])

    def test_single_mode_never_pairs(self):
        for page in (1, 2, 42, 43, 289):
            self.assertEqual(paging.pages_for(page, 289, SINGLE), [page])

    def test_out_of_range_is_clamped_not_raised(self):
        self.assertEqual(paging.pages_for(0, 289, SINGLE), [1])
        self.assertEqual(paging.pages_for(9999, 289, SINGLE), [289])

    def test_an_empty_document_shows_nothing(self):
        self.assertEqual(paging.pages_for(1, 0, BOOK), [])


class TestStepping(unittest.TestCase):
    """`next_page` / `prev_page` -- the port of `viewer.js:626` and `:646`."""

    def test_next_from_the_cover_is_two_not_three(self):
        self.assertEqual(paging.next_page(1, 289, BOOK), 2)

    def test_next_moves_a_whole_spread(self):
        self.assertEqual(paging.next_page(42, 289, BOOK), 44)

    def test_prev_from_anywhere_in_the_first_spread_is_the_cover(self):
        self.assertEqual(paging.prev_page(2, 289, BOOK), 1)
        self.assertEqual(paging.prev_page(3, 289, BOOK), 1)

    def test_prev_at_the_cover_stays(self):
        self.assertEqual(paging.prev_page(1, 289, BOOK), 1)

    def test_next_stops_once_the_last_page_is_already_showing(self):
        # 289 pages: the final spread is 288|289, so next must do nothing
        # rather than jump back to a page that is already on screen.
        self.assertEqual(paging.next_page(288, 289, BOOK, [288, 289]), 288)

    def test_next_reaches_a_lone_last_page(self):
        # 290 pages: the spread before the end is 288|289 and the last page
        # stands alone, so next lands on it exactly.
        self.assertEqual(paging.next_page(288, 290, BOOK, [288, 289]), 290)

    def test_single_mode_steps_one_and_stops_at_the_ends(self):
        self.assertEqual(paging.next_page(41, 289, SINGLE), 42)
        self.assertEqual(paging.next_page(289, 289, SINGLE), 289)
        self.assertEqual(paging.prev_page(1, 289, SINGLE), 1)

    def test_paging_forward_then_back_returns_to_the_same_spread(self):
        page, total = 1, 289
        seen = [page]
        for _ in range(12):
            page = paging.next_page(page, total, BOOK, paging.pages_for(page, total, BOOK))
            seen.append(page)
        for expected in reversed(seen[:-1]):
            page = paging.prev_page(page, total, BOOK)
            self.assertEqual(page, expected)

    def test_every_page_of_the_book_is_reachable_going_forward(self):
        total = 289
        page, shown = 1, set()
        for _ in range(total):
            shown.update(paging.pages_for(page, total, BOOK))
            nxt = paging.next_page(page, total, BOOK, paging.pages_for(page, total, BOOK))
            if nxt == page:
                break
            page = nxt
        self.assertEqual(shown, set(range(1, total + 1)))


class TestScale(unittest.TestCase):
    """`fit_scale` -- the port of `calculateScale`."""

    def test_fit_page_takes_the_tighter_of_the_two(self):
        # A 1140 x 797 pt spread in a short, wide viewport is height-bound.
        self.assertAlmostEqual(
            paging.fit_scale(1140, 797, 1152, 551, "fit-page"), 551 / 797, places=6
        )

    def test_fit_width_ignores_the_height(self):
        self.assertAlmostEqual(
            paging.fit_scale(570, 797, 1152, 551, "fit-width"), 1152 / 570, places=6
        )

    def test_custom_is_taken_verbatim(self):
        self.assertEqual(paging.fit_scale(570, 797, 1152, 551, "custom", 1.5), 1.5)

    def test_a_tiny_viewport_does_not_produce_a_tiny_page(self):
        # `calculateScale` clamps the viewport at 320 px and the scale at 0.2,
        # so a window dragged down to nothing still leaves a legible page.
        self.assertGreaterEqual(paging.fit_scale(1140, 797, 10, 10, "fit-page"), 0.2)

    def test_a_spread_is_the_sum_of_its_pages(self):
        self.assertEqual(paging.spread_size([(570, 797), (570, 797)]), (1140, 797))

    def test_a_mixed_book_measures_each_page_and_not_the_first(self):
        # The 443 MB chemistry book really does mix these two widths.
        self.assertEqual(paging.spread_size([(1167.9, 796.5)]), (1167.9, 796.5))
        self.assertEqual(
            paging.spread_size([(569.8, 796.5), (569.8, 796.5)])[0], 1139.6
        )

    def test_rotation_swaps_the_spread(self):
        self.assertEqual(paging.spread_size([(570, 797)], rotation=90), (797, 570))


class TestPinchZoom(unittest.TestCase):
    """
    The arithmetic behind the touch gestures.

    `Gtk.GestureZoom` reports the pinch relative to where the fingers started,
    which is the one thing easy to get wrong here: applying each report to the
    live zoom instead of to the zoom the gesture began at compounds it, and a
    pinch held still would then drift to the ceiling on its own.
    """

    def test_a_pinch_scales_the_zoom_it_started_from(self):
        self.assertAlmostEqual(paging.pinch_zoom(1.0, 1.5), 1.5)
        self.assertAlmostEqual(paging.pinch_zoom(0.8, 1.5), 1.2)

    def test_a_held_pinch_does_not_compound(self):
        # Three reports of the same 1.5x are one 1.5x, not 3.4x.
        base = 1.0
        for _ in range(3):
            zoom = paging.pinch_zoom(base, 1.5)
        self.assertAlmostEqual(zoom, 1.5)

    def test_a_pinch_is_clamped_to_the_same_range_as_the_dropdown(self):
        self.assertAlmostEqual(paging.pinch_zoom(2.0, 8.0), paging.ZOOM_MAX)
        self.assertAlmostEqual(paging.pinch_zoom(0.5, 0.01), paging.ZOOM_MIN)

    def test_a_pinch_with_no_distance_is_ignored(self):
        self.assertAlmostEqual(paging.pinch_zoom(1.25, 0.0), 1.25)


class TestWheelZoom(unittest.TestCase):
    def test_scrolling_down_zooms_out_and_up_zooms_in(self):
        self.assertLess(paging.wheel_zoom(1.0, 1.0), 1.0)
        self.assertGreater(paging.wheel_zoom(1.0, -1.0), 1.0)

    def test_a_touchpad_fraction_moves_less_than_a_wheel_notch(self):
        notch = paging.wheel_zoom(1.0, -1.0) - 1.0
        nudge = paging.wheel_zoom(1.0, -0.25) - 1.0
        self.assertAlmostEqual(nudge, notch / 4)

    def test_a_report_larger_than_a_notch_is_still_one_notch(self):
        self.assertAlmostEqual(paging.wheel_zoom(1.0, -4.0),
                               paging.wheel_zoom(1.0, -1.0))

    def test_the_clamp_holds(self):
        self.assertAlmostEqual(paging.wheel_zoom(paging.ZOOM_MAX, -1.0),
                               paging.ZOOM_MAX)
        self.assertAlmostEqual(paging.wheel_zoom(paging.ZOOM_MIN, 1.0),
                               paging.ZOOM_MIN)

    def test_a_scroll_with_no_delta_leaves_the_zoom_alone(self):
        self.assertAlmostEqual(paging.wheel_zoom(1.4, 0.0), 1.4)


class TestTextureCache(unittest.TestCase):
    def test_lru_evicts_the_least_recently_used(self):
        cache = TextureCache(budget=250)
        cache.put(("a",), "A", 100)
        cache.put(("b",), "B", 100)
        cache.get(("a",))               # a is now the newer of the two
        cache.put(("c",), "C", 100)     # over budget: b goes
        self.assertIsNotNone(cache.get(("a",)))
        self.assertIsNone(cache.get(("b",)))
        self.assertIsNotNone(cache.get(("c",)))

    def test_the_entry_just_inserted_is_never_evicted(self):
        # A single very deep zoom can exceed the whole budget; it must still
        # display rather than being thrown away on the way in.
        cache = TextureCache(budget=100)
        cache.put(("huge",), "H", 10_000)
        self.assertIsNotNone(cache.get(("huge",)))

    def test_replacing_a_key_does_not_double_count_its_bytes(self):
        cache = TextureCache(budget=10_000)
        cache.put(("a",), "A", 500)
        cache.put(("a",), "A2", 700)
        self.assertEqual(cache.nbytes, 700)
        self.assertEqual(len(cache), 1)

    def test_drop_pages_keeps_only_what_is_on_screen(self):
        cache = TextureCache(budget=10_000)
        for page in range(1, 6):
            cache.put((page, 1.0, 0, None), f"T{page}", 100)
        cache.drop_pages({2, 3})
        self.assertEqual(len(cache), 2)
        self.assertEqual(cache.nbytes, 200)

    def test_clear_releases_everything(self):
        cache = TextureCache(budget=10_000)
        cache.put(("a",), "A", 500)
        cache.clear()
        self.assertEqual((len(cache), cache.nbytes), (0, 0))

    def test_the_budget_can_be_raised_from_the_environment(self):
        previous = os.environ.get("INTERAKTIV_TEXTURE_BUDGET")
        os.environ["INTERAKTIV_TEXTURE_BUDGET"] = str(300 * 1024 * 1024)
        try:
            self.assertEqual(TextureCache().budget, 300 * 1024 * 1024)
        finally:
            if previous is None:
                del os.environ["INTERAKTIV_TEXTURE_BUDGET"]
            else:
                os.environ["INTERAKTIV_TEXTURE_BUDGET"] = previous


class TestRenderRequest(unittest.TestCase):
    def test_the_key_is_what_makes_two_renders_the_same_picture(self):
        a = RenderRequest(page=5, scale=1.5, rotation=0, lane=0, generation=3)
        b = RenderRequest(page=5, scale=1.5, rotation=0, lane=2, generation=9)
        # Lane and generation are about scheduling, not about pixels.
        self.assertEqual(a.key, b.key)

    def test_a_hair_of_scale_difference_re_uses_the_texture(self):
        # Fit-page recomputed after a one-pixel resize must not re-render.
        a = RenderRequest(page=5, scale=0.69134)
        b = RenderRequest(page=5, scale=0.69136)
        self.assertEqual(a.key, b.key)

    def test_rotation_is_part_of_the_picture(self):
        self.assertNotEqual(
            RenderRequest(page=5, scale=1.0, rotation=0).key,
            RenderRequest(page=5, scale=1.0, rotation=90).key,
        )

    def test_a_clip_is_part_of_the_picture(self):
        self.assertNotEqual(
            RenderRequest(page=5, scale=1.0).key,
            RenderRequest(page=5, scale=1.0, clip=(0, 0, 10, 10)).key,
        )


class TestRenderCap(unittest.TestCase):
    """`DocumentHandle._capped_scale` -- the bound on one pixmap."""

    def test_an_ordinary_page_is_rendered_at_the_scale_asked_for(self):
        request = RenderRequest(page=1, scale=2.0)
        self.assertEqual(DocumentHandle._capped_scale(request, 570, 797), 2.0)

    def test_a_deep_zoom_is_lowered_to_the_cap(self):
        request = RenderRequest(page=1, scale=12.0)
        scale = DocumentHandle._capped_scale(request, 570, 797)
        self.assertLess(scale, 12.0)
        self.assertLessEqual(570 * 797 * scale * scale, MAX_RENDER_PIXELS + 1)

    def test_a_crop_keeps_its_resolution(self):
        # Cropping is how focus mode affords a deep zoom at all, so the cap is
        # measured against the clip and not against the whole page.
        request = RenderRequest(page=1, scale=6.0, clip=(0, 0, 200, 200))
        self.assertEqual(DocumentHandle._capped_scale(request, 570, 797), 6.0)

    def test_a_degenerate_clip_does_not_divide_by_zero(self):
        request = RenderRequest(page=1, scale=3.0, clip=(10, 10, 10, 10))
        self.assertEqual(DocumentHandle._capped_scale(request, 570, 797), 3.0)


class TestDocumentInfo(unittest.TestCase):
    def test_each_page_reports_its_own_size(self):
        info = DocumentInfo("x.pdf", 3, ((1168, 797), (570, 797), (570, 797)))
        self.assertEqual(info.size(1), (1168, 797))
        self.assertEqual(info.size(2), (570, 797))

    def test_a_page_out_of_range_is_clamped(self):
        info = DocumentInfo("x.pdf", 2, ((1168, 797), (570, 797)))
        self.assertEqual(info.size(99), (570, 797))
        self.assertEqual(info.size(0), (1168, 797))


class TestProtocol(unittest.TestCase):
    """The pipe between the reader and its renderer."""

    def _roundtrip(self, header, payload=b""):
        with tempfile.TemporaryFile() as f:
            write_message(f, header, payload)
            f.seek(0)
            return read_message(f)

    def test_a_header_survives_the_trip(self):
        got, payload = self._roundtrip({"op": "render", "page": 7, "clip": None})
        self.assertEqual(got["op"], "render")
        self.assertEqual(got["page"], 7)
        self.assertEqual(payload, b"")

    def test_pixels_survive_the_trip(self):
        pixels = bytes(range(256)) * 400
        got, payload = self._roundtrip({"op": "result"}, pixels)
        self.assertEqual(payload, pixels)
        self.assertEqual(got["nbytes"], len(pixels))

    def test_turkish_text_survives_the_trip(self):
        got, _ = self._roundtrip({"op": "error", "message": "Kitap açılamadı: ığşç"})
        self.assertEqual(got["message"], "Kitap açılamadı: ığşç")

    def test_a_truncated_stream_is_an_eof_and_not_a_hang(self):
        with tempfile.TemporaryFile() as f:
            f.write(struct.pack("<I", 200))
            f.write(b'{"op": "resul')
            f.seek(0)
            with self.assertRaises(EOFError):
                read_message(f)


def _tiny_pdf(path, pages=3):
    """A real PDF, built here so the test needs nothing from the catalogue."""
    import pymupdf

    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=200, height=400)
        page.insert_text((20, 40 + index * 10), f"page {index + 1}")
    doc.save(path)
    doc.close()


class TestWorkerProcess(unittest.TestCase):
    """
    The renderer, end to end, as its own process.

    It is a process and not a thread because PyMuPDF holds the GIL while it
    renders: in-process, a page turn stops the main loop for as long as the
    render takes. That is the whole reason this layer exists, so the test runs
    the real child over the real pipe.
    """

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="interaktiv-reader-")
        cls.pdf = os.path.join(cls.dir, "tiny.pdf")
        _tiny_pdf(cls.pdf)

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        self.child = subprocess.Popen(
            [sys.executable, "-u", "-m", "interaktiv_gtk.render.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=PROJECT_ROOT, env=env, bufsize=0,
        )
        self.addCleanup(self._stop)

    def _stop(self):
        try:
            if self.child.poll() is None:
                self.child.kill()
            self.child.wait(timeout=5)
        except Exception:
            pass
        for stream in (self.child.stdin, self.child.stdout):
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                pass

    def _ask(self, message):
        write_message(self.child.stdin, message)
        return read_message(self.child.stdout)

    def test_open_reports_the_shape_of_the_document(self):
        header, _ = self._ask({"op": "open", "path": self.pdf})
        self.assertEqual(header["op"], "ready")
        self.assertEqual(header["page_count"], 3)
        self.assertEqual([round(v) for v in header["page_sizes"][0]], [200, 400])

    def test_a_render_comes_back_as_rgb_pixels(self):
        self._ask({"op": "open", "path": self.pdf})
        header, payload = self._ask(
            {"op": "render", "page": 2, "scale": 2.0, "rotation": 0, "clip": None}
        )
        self.assertEqual(header["op"], "result")
        self.assertEqual((header["width"], header["height"]), (400, 800))
        # Three bytes per pixel, and a stride that matches the width.
        self.assertEqual(header["stride"], 400 * 3)
        self.assertEqual(len(payload), header["stride"] * header["height"])

    def test_rotation_moves_the_origin_the_way_geometry_says_it_does(self):
        # `pix.x` / `pix.y` is what `PageTransform` subtracts to get widget
        # coordinates, so the two have to agree or every hotspot in M3 lands in
        # the wrong place.
        self._ask({"op": "open", "path": self.pdf})
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                header, _ = self._ask({
                    "op": "render", "page": 1, "scale": 2.0,
                    "rotation": rotation, "clip": None,
                })
                expected = PageTransform.for_full_page(200, 400, 2.0, rotation)
                self.assertAlmostEqual(header["origin_x"], expected.origin_x, delta=1)
                self.assertAlmostEqual(header["origin_y"], expected.origin_y, delta=1)

    def test_a_clip_bounds_the_pixmap_by_the_region(self):
        self._ask({"op": "open", "path": self.pdf})
        header, payload = self._ask({
            "op": "render", "page": 1, "scale": 4.0, "rotation": 0,
            "clip": [0, 0, 50, 50],
        })
        self.assertEqual((header["width"], header["height"]), (200, 200))
        self.assertEqual(len(payload), 200 * 3 * 200)

    def test_a_page_that_does_not_exist_fails_without_killing_the_renderer(self):
        self._ask({"op": "open", "path": self.pdf})
        header, _ = self._ask(
            {"op": "render", "page": 99, "scale": 1.0, "rotation": 0, "clip": None}
        )
        self.assertEqual(header["op"], "failed")
        # Still alive and still answering.
        header, _ = self._ask(
            {"op": "render", "page": 1, "scale": 1.0, "rotation": 0, "clip": None}
        )
        self.assertEqual(header["op"], "result")

    def test_opening_something_that_is_not_a_pdf_is_an_error_not_a_crash(self):
        broken = os.path.join(self.dir, "not.pdf")
        with open(broken, "wb") as f:
            f.write(b"this is not a pdf")
        header, _ = self._ask({"op": "open", "path": broken})
        self.assertEqual(header["op"], "error")
        self.assertTrue(header.get("message"))

    def test_quit_closes_the_renderer_and_its_memory_with_it(self):
        self._ask({"op": "open", "path": self.pdf})
        write_message(self.child.stdin, {"op": "quit"})
        self.child.stdin.close()
        self.assertEqual(self.child.wait(timeout=10), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
