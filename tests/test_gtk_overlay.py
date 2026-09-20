"""
The activity overlay: joining a bake to the publisher's manifest, and what the
reader is then allowed to draw.

These run without a display. Everything under test is either pure
(`build_overlay`, `PageOverlay.hit_test`) or reachable through
`DocumentSession` with a stand-in for the document, which is deliberate: the
rules about *which* rectangle is an activity must be checkable without a
window, because that is what makes them checkable at all.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interaktiv_core.oges import Oge, OgeIndex
from interaktiv_core.regions import Activity, Item, PageRegions, RegionsBook
from interaktiv_gtk.reader.overlay import build_overlay

PAGE_W, PAGE_H = 600.0, 800.0


def activity(aid, label, rect, parts=(), items=(), anchored=False, headline=""):
    return Activity(
        id=aid, page_num=5, label=label, column=0, rect=rect, parts=tuple(parts),
        headline=headline, anchored=anchored, items=tuple(items),
    )


def item(iid, rect, part_index=0, number=None):
    return Item(id=iid, label=None, number=number, part_index=part_index, rect=rect)


def page(activities, page_num=5):
    return PageRegions(
        page_num=page_num, page_width=PAGE_W, page_height=PAGE_H,
        columns=(), activities=tuple(activities),
    )


def oge(oid, title, posx, posy, printed_page=5, guid=None):
    return Oge(
        id=oid, guid=guid or f"guid-{oid}", title=title,
        printed_page=printed_page, posx=posx, posy=posy,
    )


def top_pct_to_y(pct):
    """A y in PDF user space that sits `pct` of the way down the sheet."""
    return PAGE_H - PAGE_H * (pct / 100.0)


class TestSpots(unittest.TestCase):
    def test_a_region_with_no_parts_is_one_spot(self):
        overlay = build_overlay(page([activity("a1", "a", (50, 600, 550, 700))]))
        self.assertEqual(len(overlay.spots), 1)
        self.assertEqual(overlay.spots[0].rect, (50, 600, 550, 700))

    def test_a_region_that_flows_across_a_column_break_is_two_spots(self):
        act = activity("a1", "a", (50, 200, 550, 700),
                       parts=[(50, 200, 280, 700), (320, 400, 550, 700)])
        overlay = build_overlay(page([act]))
        self.assertEqual(len(overlay.spots), 2)
        self.assertEqual([s.part_index for s in overlay.spots], [0, 1])
        # Never unioned: the gap between the columns belongs to neither piece.
        self.assertIsNone(overlay.hit_test(300, 500))

    def test_questions_are_counted_per_piece(self):
        act = activity(
            "a1", "a", (50, 200, 550, 700),
            parts=[(50, 400, 280, 700), (320, 200, 550, 700)],
            items=[item("q1", (60, 600, 270, 650), 0),
                   item("q2", (60, 500, 270, 550), 0),
                   item("q3", (330, 300, 540, 350), 1)],
        )
        overlay = build_overlay(page([act]))
        self.assertEqual([s.item_count for s in overlay.spots], [2, 1])

    def test_an_anchored_region_is_named_by_its_headline(self):
        act = activity("p5-oge-77", None, (50, 600, 550, 700),
                       anchored=True, headline="Hazır mıyız?")
        overlay = build_overlay(page([act]))
        self.assertEqual(overlay.spots[0].label, "Hazır mıyız?")

    def test_a_lettered_region_is_named_by_its_letter(self):
        overlay = build_overlay(page([activity("a1", "a", (50, 600, 550, 700))]))
        self.assertEqual(overlay.spots[0].label, "a")


class TestHitTest(unittest.TestCase):
    def test_the_smallest_region_covering_a_point_wins(self):
        outer = activity("a1", "a", (50, 200, 550, 700))
        inner = activity("a2", "b", (100, 300, 300, 400))
        overlay = build_overlay(page([outer, inner]))
        hit = overlay.hit_test(200, 350)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.act_index, 1)

    def test_a_point_outside_every_region_hits_nothing(self):
        overlay = build_overlay(page([activity("a1", "a", (50, 600, 550, 700))]))
        self.assertIsNone(overlay.hit_test(20, 100))

    def test_two_activities_sharing_a_letter_stay_two_activities(self):
        left = activity("p36-d", "d", (50, 400, 280, 700))
        right = activity("p36-d", "d", (320, 400, 550, 700))
        overlay = build_overlay(page([left, right]))
        self.assertEqual(overlay.hit_test(100, 500).act_index, 0)
        self.assertEqual(overlay.hit_test(400, 500).act_index, 1)


class TestLinking(unittest.TestCase):
    def test_an_entry_beside_a_lettered_region_marks_it_interactive(self):
        act = activity("a1", "a", (50, top_pct_to_y(22), 280, top_pct_to_y(18)))
        entry = oge("77", "a", posx=8.0, posy=18.0)
        overlay = build_overlay(page([act]), [entry])
        self.assertTrue(overlay.spots[0].interactive)
        self.assertEqual(overlay.spots[0].guid, "guid-77")
        # Claimed, so it is not also drawn as a pin.
        self.assertEqual(overlay.pins, [])

    def test_an_entry_that_matches_nothing_stays_a_pin(self):
        act = activity("a1", "a", (50, top_pct_to_y(80), 280, top_pct_to_y(75)))
        entry = oge("77", "Tema etkinliği", posx=8.0, posy=12.0)
        overlay = build_overlay(page([act]), [entry])
        self.assertFalse(overlay.spots[0].interactive)
        self.assertEqual([p.oge.id for p in overlay.pins], ["77"])

    def test_an_entry_without_a_position_is_neither_linked_nor_pinned(self):
        act = activity("a1", "a", (50, top_pct_to_y(22), 280, top_pct_to_y(18)))
        entry = oge("77", "a", posx=0.0, posy=0.0)
        overlay = build_overlay(page([act]), [entry])
        self.assertFalse(overlay.spots[0].interactive)
        self.assertEqual(overlay.pins, [])

    def test_an_anchored_region_claims_the_entry_it_was_grown_from(self):
        """
        The override that stops one activity being drawn twice.

        The anchored region carries the entry's id, and a neighbouring question
        happens to sit a hair closer to the icon. Without the claim the entry
        would go to the neighbour and the anchored region -- which exists only
        because of that entry -- would show as a plain box beside a pin.
        """
        anchored = activity("p5-oge-77", None, (50, top_pct_to_y(21), 280, top_pct_to_y(19)),
                            anchored=True, headline="Hazır mıyız?")
        neighbour = activity("a1", None, (50, top_pct_to_y(18.2), 280, top_pct_to_y(17)))
        entry = oge("77", "Hazır mıyız?", posx=8.0, posy=18.0)
        overlay = build_overlay(page([neighbour, anchored]), [entry])
        by_index = {s.act_index: s for s in overlay.spots}
        self.assertTrue(by_index[1].interactive)
        self.assertFalse(by_index[0].interactive)
        self.assertEqual(overlay.pins, [])

    def test_the_confidence_gate_only_ever_removes_links(self):
        act = activity("a1", "a", (50, top_pct_to_y(22), 280, top_pct_to_y(18)))
        entry = oge("77", "a", posx=8.0, posy=18.0)
        ungated = build_overlay(page([act]), [entry])
        gated = build_overlay(page([act]), [entry], confidence="none")
        self.assertTrue(ungated.spots[0].interactive)
        self.assertFalse(gated.spots[0].interactive)
        # What the gate drops does not vanish -- it becomes a reachable pin.
        self.assertEqual([p.oge.id for p in gated.pins], ["77"])

    def test_installed_state_is_asked_of_the_manager_not_assumed(self):
        act = activity("a1", "a", (50, top_pct_to_y(22), 280, top_pct_to_y(18)))
        entry = oge("77", "a", posx=8.0, posy=18.0)
        overlay = build_overlay(page([act]), [entry], is_installed=lambda g: True)
        self.assertTrue(overlay.spots[0].installed)


# -- the session's guards --------------------------------------------------


class FakeInfo:
    def __init__(self, page_count, sizes=None):
        self.page_count = page_count
        self._sizes = sizes or {}

    def size(self, page):
        return self._sizes.get(page, (PAGE_W, PAGE_H))


class FakeManager:
    def __init__(self, regions_path=None, oges=()):
        self._regions_path = regions_path
        self._oges = list(oges)

    def get_regions_path(self, _book_id):
        return self._regions_path

    def get_book_oges(self, _book_id):
        return self._oges

    def is_activity_installed(self, _guid):
        return False


def bake(pages, page_count=10, enabled=True):
    return {
        "version": 2,
        "bookId": "b",
        "fingerprint": "",
        "pageCount": page_count,
        "calibration": {"enabled": enabled, "confidence": "strong"},
        "folio": {"offset": 0},
        "pages": pages,
    }


def baked_page(page_num, activities, width=PAGE_W, height=PAGE_H):
    return {
        str(page_num): {
            "pageWidth": width, "pageHeight": height, "columns": [],
            "activities": activities,
        }
    }


def raw_activity(aid, label, rect, items=()):
    return {"id": aid, "label": label, "rect": list(rect), "parts": [],
            "items": [{"id": q, "rect": list(r), "partIndex": 0}
                      for q, r in items]}


class TestSessionGuards(unittest.TestCase):
    def session(self, data, info, oges=()):
        from interaktiv_gtk.reader.session import DocumentSession

        s = DocumentSession("b", "t", "/nonexistent.pdf",
                            manager=FakeManager(oges=oges))
        s.regions = RegionsBook.from_dict(data)
        s.oges = OgeIndex.from_kitapoge_list(list(oges))
        s.info = info
        s.validate_activities(info.page_count)
        return s

    def test_a_bake_for_a_different_edition_is_dropped_whole(self):
        data = bake(baked_page(5, [raw_activity("a1", "a", (50, 600, 550, 700))]),
                    page_count=10)
        s = self.session(data, FakeInfo(289))
        self.assertFalse(s.activities_ready)
        self.assertEqual(s.bake_state, "mismatch")
        self.assertIsNone(s.overlay(5))

    def test_a_sheet_whose_size_disagrees_loses_only_its_own_overlay(self):
        pages = {}
        pages.update(baked_page(5, [raw_activity("a1", "a", (50, 600, 550, 700))]))
        pages.update(baked_page(6, [raw_activity("b1", "b", (50, 600, 550, 700))]))
        s = self.session(bake(pages), FakeInfo(10, {5: (PAGE_W + 30, PAGE_H)}))
        self.assertTrue(s.activities_ready)
        self.assertIsNone(s.overlay(5))
        self.assertIsNotNone(s.overlay(6))

    def test_a_bake_with_activities_switched_off_draws_nothing(self):
        data = bake(baked_page(5, [raw_activity("a1", "a", (50, 600, 550, 700))]),
                    enabled=False)
        s = self.session(data, FakeInfo(10))
        self.assertFalse(s.activities_ready)

    def test_a_sheet_with_no_regions_still_shows_the_publishers_pin(self):
        """
        A page the detector found nothing on can still carry an activity: the
        publisher hung it there, and dropping it because no letter was detected
        would make it unreachable.
        """
        entry = {"id": "77", "data": "guid-77", "baslik": "Tema etkinliği",
                 "ogeturu": 1, "sayfaustuoge": True, "sayfano": 7,
                 "posx": 8.0, "posy": 12.0}
        s = self.session(bake(baked_page(5, [])), FakeInfo(10), oges=[entry])
        overlay = s.overlay(7)
        self.assertIsNotNone(overlay)
        self.assertEqual(len(overlay.pins), 1)
        self.assertEqual(overlay.spots, [])

    def test_an_empty_sheet_has_no_overlay_at_all(self):
        s = self.session(bake(baked_page(5, [])), FakeInfo(10))
        self.assertIsNone(s.overlay(9))

    def test_overlays_are_cached_and_identical_between_asks(self):
        data = bake(baked_page(5, [raw_activity("a1", "a", (50, 600, 550, 700))]))
        s = self.session(data, FakeInfo(10))
        self.assertIs(s.overlay(5), s.overlay(5))

    def test_the_summaries_walk_every_baked_sheet_once(self):
        pages = {}
        pages.update(baked_page(3, [raw_activity("a1", "a", (50, 600, 550, 700),
                                                 items=[("q1", (60, 610, 300, 640))])]))
        pages.update(baked_page(8, [raw_activity("b1", "b", (50, 600, 550, 700)),
                                    raw_activity("c1", "c", (50, 300, 550, 400))]))
        s = self.session(bake(pages), FakeInfo(10))
        rows = list(s.activity_summaries())
        self.assertEqual([(r[0], r[1], r[2], r[3]) for r in rows],
                         [(3, 0, "a", 1), (8, 0, "b", 0), (8, 1, "c", 0)])


if __name__ == "__main__":
    unittest.main()
