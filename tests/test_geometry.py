"""
The transform between a bake's rectangles and a widget's pixels
(`interaktiv_core.geometry`).

Three coordinate systems meet here and getting the flip or the rotation origin
wrong puts a hotspot on the wrong half of the sheet without anything crashing,
so the arithmetic is checked both on its own (round trips, known values) and
against what `pymupdf` actually renders, which is the only authority on where a
rotated or clipped pixmap begins.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from interaktiv_core.geometry import PageTransform  # noqa: E402

W, H = 600.0, 800.0
ROTATIONS = (0, 90, 180, 270)


class TestRoundTrip(unittest.TestCase):
    def test_every_rotation_and_scale_inverts_exactly(self):
        points = [(0, 0), (W, H), (0, H), (W, 0), (123.5, 456.25), (W / 2, H / 3)]
        for rot in ROTATIONS:
            for scale in (0.5, 1.0, 2.0, 6.0):
                t = PageTransform.for_full_page(W, H, scale, rot)
                for x, y in points:
                    wx, wy = t.point_to_widget(x, y)
                    rx, ry = t.point_to_pdf(wx, wy)
                    self.assertAlmostEqual(rx, x, places=6, msg=f"rot{rot} s{scale}")
                    self.assertAlmostEqual(ry, y, places=6, msg=f"rot{rot} s{scale}")

    def test_a_crop_origin_does_not_break_the_inverse(self):
        for rot in ROTATIONS:
            t = PageTransform.for_full_page(W, H, 3.0, rot).with_origin(-317.0, 88.5)
            wx, wy = t.point_to_widget(200.0, 300.0)
            rx, ry = t.point_to_pdf(wx, wy)
            self.assertAlmostEqual(rx, 200.0, places=6)
            self.assertAlmostEqual(ry, 300.0, places=6)


class TestKnownValues(unittest.TestCase):
    def test_pdf_space_is_y_up_and_widget_space_is_y_down(self):
        t = PageTransform.for_full_page(W, H, 1.0, 0)
        # The bottom-left of the page is the bottom-left of the widget.
        self.assertEqual(t.point_to_widget(0.0, 0.0), (0.0, H))
        # The top-left of the page is the widget's origin.
        self.assertEqual(t.point_to_widget(0.0, H), (0.0, 0.0))

    def test_widget_size_swaps_on_a_quarter_turn(self):
        self.assertEqual(PageTransform.for_full_page(W, H, 2.0, 0).widget_size, (1200.0, 1600.0))
        self.assertEqual(PageTransform.for_full_page(W, H, 2.0, 180).widget_size, (1200.0, 1600.0))
        self.assertEqual(PageTransform.for_full_page(W, H, 2.0, 90).widget_size, (1600.0, 1200.0))
        self.assertEqual(PageTransform.for_full_page(W, H, 2.0, 270).widget_size, (1600.0, 1200.0))

    def test_a_rect_is_normalised_however_the_rotation_turns_it(self):
        # A rect near the page's top-left in PDF space.
        rect = (50.0, H - 150.0, 250.0, H - 50.0)
        for rot in ROTATIONS:
            t = PageTransform.for_full_page(W, H, 1.0, rot)
            left, top, width, height = t.rect_to_widget(rect)
            self.assertGreater(width, 0, f"rot{rot} width must be positive")
            self.assertGreater(height, 0, f"rot{rot} height must be positive")
            # The rect stays inside the page, whichever way up it is.
            pw, ph = t.widget_size
            self.assertGreaterEqual(round(left, 6), 0.0, f"rot{rot}")
            self.assertGreaterEqual(round(top, 6), 0.0, f"rot{rot}")
            self.assertLessEqual(round(left + width, 6), pw, f"rot{rot}")
            self.assertLessEqual(round(top + height, 6), ph, f"rot{rot}")
        # Unrotated, it sits where it looks like it should: 50pt in, 50pt down.
        self.assertEqual(
            PageTransform.for_full_page(W, H, 1.0, 0).rect_to_widget(rect),
            (50.0, 50.0, 200.0, 100.0),
        )

    def test_scale_multiplies_both_axes(self):
        t = PageTransform.for_full_page(W, H, 4.0, 0)
        self.assertEqual(t.rect_to_widget((10.0, H - 20.0, 30.0, H - 10.0)),
                         (40.0, 40.0, 80.0, 40.0))

    def test_rect_to_clip_flips_y_for_pymupdf(self):
        t = PageTransform.for_full_page(W, H, 1.0, 0)
        # PDF y-up (y0=100 is nearer the bottom) -> page y-down.
        self.assertEqual(t.rect_to_clip((10.0, 100.0, 50.0, 300.0)),
                         (10.0, H - 300.0, 50.0, H - 100.0))


@unittest.skipUnless(
    os.path.isfile(os.path.join(ROOT, "books", "0e966773-5012-4f57-8be5-d892e8c75f22.pdf")),
    "needs an installed book",
)
class TestAgainstPyMuPDF(unittest.TestCase):
    """
    The transform against the renderer it has to agree with.

    A rotation moves the device origin off zero, which is why a pixmap reports a
    non-zero `pix.x`/`pix.y`; if the derived origin and the rendered one
    disagree, every rect on a rotated page is offset by a page width.
    """

    PDF = os.path.join(ROOT, "books", "0e966773-5012-4f57-8be5-d892e8c75f22.pdf")

    @classmethod
    def setUpClass(cls):
        import pymupdf

        cls.pymupdf = pymupdf
        cls.doc = pymupdf.open(cls.PDF)
        cls.page = cls.doc[20]

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_the_derived_origin_matches_the_rendered_one(self):
        pw, ph = self.page.rect.width, self.page.rect.height
        for rot in ROTATIONS:
            t = PageTransform.for_full_page(pw, ph, 2.0, rot)
            pix = self.page.get_pixmap(matrix=t.pymupdf_matrix(), alpha=False)
            # The pixmap's integer rect rounds outward, so allow a pixel.
            self.assertLessEqual(abs(pix.x - t.origin_x), 1.0, f"rot{rot} x")
            self.assertLessEqual(abs(pix.y - t.origin_y), 1.0, f"rot{rot} y")
            ww, wh = t.widget_size
            self.assertLessEqual(abs(pix.width - ww), 2.0, f"rot{rot} width")
            self.assertLessEqual(abs(pix.height - wh), 2.0, f"rot{rot} height")

    def test_a_clipped_render_puts_the_region_at_the_widget_origin(self):
        """The focus-mode crop: the pixmap covers the region and nothing else."""
        pw, ph = self.page.rect.width, self.page.rect.height
        region = (100.0, 100.0, 300.0, 300.0)
        for rot in ROTATIONS:
            base = PageTransform.for_full_page(pw, ph, 6.0, rot)
            clip = self.pymupdf.Rect(*base.rect_to_clip(region))
            pix = self.page.get_pixmap(matrix=base.pymupdf_matrix(), clip=clip, alpha=False)
            t = base.with_origin(pix.x, pix.y)
            left, top, width, height = t.rect_to_widget(region)
            self.assertLessEqual(abs(left), 2.0, f"rot{rot} left")
            self.assertLessEqual(abs(top), 2.0, f"rot{rot} top")
            self.assertLessEqual(abs(width - pix.width), 2.0, f"rot{rot} width")
            self.assertLessEqual(abs(height - pix.height), 2.0, f"rot{rot} height")
            # Bounded by the region, not the page: this is what keeps focus-mode
            # memory flat however deep the zoom goes.
            self.assertLess(pix.width * pix.height, 8e6, f"rot{rot} pixel budget")

    def test_a_point_clicked_on_a_clipped_render_lands_in_the_region(self):
        pw, ph = self.page.rect.width, self.page.rect.height
        region = (100.0, 100.0, 300.0, 300.0)
        base = PageTransform.for_full_page(pw, ph, 6.0, 90)
        clip = self.pymupdf.Rect(*base.rect_to_clip(region))
        pix = self.page.get_pixmap(matrix=base.pymupdf_matrix(), clip=clip, alpha=False)
        t = base.with_origin(pix.x, pix.y)
        x, y = t.point_to_pdf(pix.width / 2.0, pix.height / 2.0)
        self.assertTrue(region[0] <= x <= region[2], f"x {x} outside {region}")
        self.assertTrue(region[1] <= y <= region[3], f"y {y} outside {region}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
