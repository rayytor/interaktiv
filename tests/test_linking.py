"""
The rule that joins the publisher's interactive activities to the regions on a
page (`interaktiv_core.linking`).

These are the cases `tools/hotspot_extraction/tests/test_interactive_links.mjs`
covers for the web reader, ported so that the one shared Python implementation
is held to the same standard, plus the two the JS version gets wrong.
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from interaktiv_core.linking import (  # noqa: E402
    apply_anchored_ids,
    hit_test,
    link_oges,
    oge_view,
    parse_oge_label,
    region_view,
    unlinked_oges,
)

PAGE_W = 570.0
PAGE_H = 800.0


def region(region_id, label, x0, top, height=100.0, width=220.0, anchored=False):
    """A detected region, given its top as the manifest states positions."""
    return region_view(
        region_id,
        label,
        (x0, PAGE_H - top - height, x0 + width, PAGE_H - top),
        PAGE_W,
        PAGE_H,
        anchored,
    )


def top_pct(r):
    """Top of a region as a percentage of the sheet, counted from the top."""
    return r.top_pct


def oge(oge_id, title, printed_page, posx, posy):
    return oge_view(oge_id, title, printed_page, posx, posy)


class TestOgeLabel(unittest.TestCase):
    def test_titles_yield_their_label_whatever_shape_they_were_typed_in(self):
        self.assertEqual(parse_oge_label("42/a", 42), "a")
        self.assertEqual(parse_oge_label("42/b", 42), "b")
        # typed without the separator, with the editorial revision mark attached
        self.assertEqual(parse_oge_label("56b-ed2", 56), "b")
        self.assertEqual(parse_oge_label("56e-ed3", 56), "e")
        self.assertEqual(parse_oge_label("29/e ed", 29), "e")
        self.assertEqual(parse_oge_label("33/h .ed2", 33), "h")
        self.assertEqual(parse_oge_label("92/c.ed", 92), "c")
        self.assertEqual(parse_oge_label("30/b ed ed", 30), "b")
        # a page number mistyped into the label still yields the label
        self.assertEqual(parse_oge_label("247n .ed", 24), "n")
        # a book that numbers its activities keeps the number
        self.assertEqual(parse_oge_label("42/1", 42), "1")
        self.assertEqual(parse_oge_label("42/12", 42), "12")
        # the Turkish convention of carrying the 0-indexed page
        self.assertEqual(parse_oge_label("41_5.Soru", 42), "5")

    def test_whole_section_activities_have_no_label_at_all(self):
        self.assertIsNone(parse_oge_label("47/Gamification", 47))
        self.assertIsNone(parse_oge_label("95/Warm Up", 95))
        self.assertIsNone(parse_oge_label("In-theme Activity", 105))
        self.assertIsNone(parse_oge_label("61/LD1", 61))
        self.assertIsNone(parse_oge_label("", 12))


class TestLinking(unittest.TestCase):
    def test_each_region_takes_the_entry_carrying_its_own_letter(self):
        a = region("p1-a", "a", 45, 80)
        b = region("p1-b", "b", 45, 200)
        regions = [a, b]
        oges = [
            oge("o-b", "50b-ed", 50, 5, top_pct(b)),
            oge("o-a", "50/a", 50, 5, top_pct(a)),
        ]
        links = link_oges(regions, oges)
        self.assertEqual(oges[links[0]].id, "o-a")
        self.assertEqual(oges[links[1]].id, "o-b")

    def test_two_activities_sharing_a_letter_are_told_apart_by_position(self):
        # The left column runs d; the right column restarts and prints d again.
        regions = [region("p1-d", "d", 45, 120), region("p1-d-2", "d", 300, 520)]
        oges = [oge("o-right", "35/d", 35, 93, top_pct(regions[1]))]
        links = link_oges(regions, oges)
        self.assertEqual(len(links), 1)
        self.assertEqual(oges[links[1]].id, "o-right")
        self.assertNotIn(0, links, "the other d must stay plain")

    def test_an_entry_is_spent_once_on_its_nearest_region(self):
        regions = [region("p1-a", "a", 45, 100), region("p1-a-2", "a", 300, 130)]
        oges = [oge("o-a", "20/a", 20, 5, top_pct(regions[0]))]
        links = link_oges(regions, oges)
        self.assertEqual(len(links), 1)
        self.assertEqual(links, {0: 0})

    def test_a_disagreeing_letter_is_not_linked_on_position_alone(self):
        c = region("p1-c", "c", 45, 300)
        links = link_oges([c], [oge("o-f", "12/f", 12, 5, top_pct(c))])
        self.assertEqual(links, {})

    def test_an_entry_with_no_label_is_held_to_the_tight_distance(self):
        d = region("p1-d", "d", 300, 600)
        near = link_oges([d], [oge("o-in", "In-theme Activity", 49, 78, top_pct(d) + 1)])
        self.assertEqual(near, {0: 0}, "an icon set against the region links")
        far = link_oges([d], [oge("o-in", "In-theme Activity", 49, 78, top_pct(d) + 20)])
        self.assertEqual(far, {}, "an icon well away from it does not")

    def test_an_unlabelled_entry_in_the_other_margin_is_not_linked(self):
        left = region("p1-a", "a", 45, 600)
        links = link_oges([left], [oge("o-warm", "Warm Up", 39, 93, top_pct(left))])
        self.assertEqual(links, {})

    def test_an_entry_with_no_position_reaches_nothing(self):
        a = region("p1-a", "a", 45, 80)
        explain = {}
        links = link_oges([a], [oge_view("o", "20/a", 20, None, None)], explain=explain)
        self.assertEqual(links, {})
        self.assertIn("no-position", explain["o"]["reasons"])


class TestIdCollision(unittest.TestCase):
    """
    The regression the port was written against.

    A sheet that runs two columns prints the same letter twice, so two distinct
    activities legitimately carry the id `p36-d`. Keying the assignment by id
    lets the second overwrite the first, which frees the first one's entry to be
    reported unplaced and grown a second time as a phantom anchored region
    sitting on top of the real one. Indices cannot collide.
    """

    def test_duplicate_region_ids_each_keep_their_own_entry(self):
        regions = [region("p36-d", "d", 45, 120), region("p36-d", "d", 300, 520)]
        oges = [
            oge("o-left", "35/d", 35, 5, top_pct(regions[0])),
            oge("o-right", "35/d", 35, 93, top_pct(regions[1])),
        ]
        links = link_oges(regions, oges)
        self.assertEqual(len(links), 2, "both regions link, despite the shared id")
        self.assertEqual(oges[links[0]].id, "o-left")
        self.assertEqual(oges[links[1]].id, "o-right")
        self.assertEqual(unlinked_oges(links, oges), [], "nothing is left to re-grow")


class TestConfidenceGate(unittest.TestCase):
    def test_no_confidence_links_only_anchored_regions(self):
        lettered = region("p1-a", "a", 45, 100)
        anchored = region("p1-oge-o-x", None, 45, 100, anchored=True)
        entry = [oge("o-x", "20/a", 20, 5, top_pct(lettered))]
        self.assertEqual(link_oges([lettered], entry, confidence="none"), {})
        self.assertEqual(link_oges([anchored], entry, confidence="none"), {0: 0})

    def test_weak_confidence_requires_a_label_for_a_lettered_region(self):
        lettered = region("p1-a", "a", 45, 100)
        labelled = [oge("o-a", "20/a", 20, 5, top_pct(lettered))]
        unlabelled = [oge("o-w", "Warm Up", 20, 5, top_pct(lettered))]
        self.assertEqual(link_oges([lettered], labelled, confidence="weak"), {0: 0})
        self.assertEqual(link_oges([lettered], unlabelled, confidence="weak"), {})

    def test_strong_confidence_gates_nothing(self):
        lettered = region("p1-a", "a", 45, 100)
        entry = [oge("o-w", "Warm Up", 20, 5, top_pct(lettered))]
        self.assertEqual(link_oges([lettered], entry, confidence="strong"), {0: 0})
        self.assertEqual(link_oges([lettered], entry, confidence=None), {0: 0})


class TestAnchoredIds(unittest.TestCase):
    def test_a_region_grown_from_an_icon_claims_that_entry(self):
        # Its top is nowhere near the icon, so only the id can match it.
        grown = region("p12-oge-o-x", None, 45, 700, anchored=True)
        entry = [oge("o-x", "Tanım-Kavram Eşleştirme", 11, 33.05, 46.29)]
        links = apply_anchored_ids(link_oges([grown], entry), [grown], entry)
        self.assertEqual(links, {0: 0})
        self.assertEqual(unlinked_oges(links, entry), [], "so it is not pinned twice")


class TestHitTest(unittest.TestCase):
    class _Act:
        def __init__(self, parts):
            self.parts = parts
            self.rect = parts[0]

    def test_the_smallest_region_covering_a_point_wins(self):
        outer = self._Act([(0.0, 0.0, 100.0, 100.0)])
        inner = self._Act([(10.0, 10.0, 20.0, 20.0)])
        page = type("P", (), {"activities": [outer, inner]})()
        self.assertIs(hit_test(page, 15, 15).activity, inner)
        self.assertIs(hit_test(page, 50, 50).activity, outer)
        self.assertIsNone(hit_test(page, 500, 500))

    def test_parts_are_tested_separately_and_never_unioned(self):
        # Two pieces in two columns, with a gutter between them.
        act = self._Act([(0.0, 0.0, 40.0, 100.0), (60.0, 0.0, 100.0, 100.0)])
        page = type("P", (), {"activities": [act]})()
        self.assertEqual(hit_test(page, 20, 50).part_index, 0)
        self.assertEqual(hit_test(page, 80, 50).part_index, 1)
        self.assertIsNone(hit_test(page, 50, 50), "the gutter belongs to neither")


class TestAgainstRealBakes(unittest.TestCase):
    """The rule run over every baked book that is on disk, as a smoke test."""

    def _books(self):
        from interaktiv_core.oges import OgeIndex
        from interaktiv_core.regions import RegionsBook

        base = os.path.join(ROOT, "activities", "books")
        if not os.path.isdir(base):
            return
        for book_id in sorted(os.listdir(base)):
            meta = os.path.join(ROOT, "activities_meta", f"{book_id}.json")
            book = RegionsBook.load(os.path.join(base, book_id, "regions.json"))
            if book is None or not os.path.isfile(meta):
                continue
            with open(meta, encoding="utf-8") as f:
                index = OgeIndex.from_kitapoge_list(json.load(f).get("kitapogeList", []))
            yield book_id, book, index

    def test_every_entry_is_claimed_at_most_once_per_sheet(self):
        checked = 0
        for book_id, book, index in self._books():
            for page_num in book.pages_with_activities():
                page = book.page(page_num)
                entries = index.for_printed_page(book.printed_page(page_num))
                if not entries or not page.activities:
                    continue
                regions = [
                    region_view(a.id, a.label, a.rect, page.page_width,
                                page.page_height, a.anchored)
                    for a in page.activities
                ]
                views = [
                    oge_view(o.id, o.title, o.printed_page, o.posx, o.posy)
                    for o in entries
                ]
                links = apply_anchored_ids(link_oges(regions, views), regions, views)
                self.assertEqual(
                    len(set(links.values())), len(links),
                    f"{book_id} p{page_num}: an entry was claimed twice",
                )
                for ri in links:
                    self.assertLess(ri, len(regions))
                checked += 1
        self.assertGreater(checked, 0, "no baked book with a manifest was found")


if __name__ == "__main__":
    unittest.main(verbosity=2)
