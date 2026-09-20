"""
One sheet's activities, resolved into things that can be drawn and clicked.

The bake says where the regions are; the publisher's manifest says which of them
have an interactive version behind them. Joining the two is
`interaktiv_core.linking`, and this module is the thin layer that turns one
page's join into a flat list of pieces -- because a region that flows across a
column break is drawn and hit as two independent rectangles, never as the box
that would contain both of them and everything in between.

Two properties are load-bearing:

  * **Nothing here re-decides the join.** `link_oges` answers in region
    *indices* rather than ids, and so does everything below it, because a sheet
    that prints two activities lettered `d` is ordinary -- keying anything by id
    would let the second one inherit the first one's interactive version.
  * **Hit testing is `linking.hit_test`, not a second implementation.** The
    drawn rectangle and the clickable rectangle have to be the same rectangle,
    and the only way to be sure of that is for one of them not to exist.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from interaktiv_core import linking
from interaktiv_core.oges import Oge
from interaktiv_core.regions import Activity, PageRegions

Rect = Tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Spot:
    """One drawable piece of one activity, in PDF user space."""

    act_index: int
    part_index: int
    rect: Rect
    label: str          # what the chip says, without the interactive bolt
    item_count: int     # numbered questions inside *this* piece
    guid: Optional[str] = None      # the interactive bundle, when there is one
    installed: bool = False
    activity: Optional[Activity] = None

    @property
    def key(self) -> Tuple[int, int]:
        return (self.act_index, self.part_index)

    @property
    def interactive(self) -> bool:
        return bool(self.guid)


@dataclass(frozen=True, slots=True)
class Pin:
    """
    A manifest entry no region claimed, kept where the publisher hung it.

    These are the whole-section tasks -- a warm-up, a consolidation, an
    in-theme activity -- which belong to the sheet rather than to any lettered
    question on it. Dropping them would make them unreachable, so they stay as
    the corner marker the web reader draws.
    """

    oge: Oge
    x_pct: float        # % of sheet width, from the left
    y_pct: float        # % of sheet height, from the top

    @property
    def label(self) -> str:
        return self.oge.name()


class PageOverlay:
    """Everything drawn on top of one rendered sheet."""

    __slots__ = ("page_num", "page_width", "page_height", "spots", "pins",
                 "_page", "_index_of")

    def __init__(self, page: PageRegions, spots: List[Spot], pins: List[Pin]):
        self.page_num = page.page_num
        self.page_width = page.page_width
        self.page_height = page.page_height
        self.spots = spots
        self.pins = pins
        self._page = page
        # Identity, not equality: two activities on a sheet can be equal field
        # for field (same letter, same shape, different column) and still be
        # two activities.
        self._index_of = {id(a): i for i, a in enumerate(page.activities)}

    # -- queries ----------------------------------------------------------

    @property
    def activities(self) -> Sequence[Activity]:
        return self._page.activities

    @property
    def is_empty(self) -> bool:
        return not self.spots and not self.pins

    def hit_test(self, x: float, y: float) -> Optional[Spot]:
        """The smallest piece covering a point in PDF user space, or None."""
        hit = linking.hit_test(self._page, x, y)
        if hit is None:
            return None
        act_index = self._index_of.get(id(hit.activity))
        if act_index is None:
            return None
        for spot in self.spots:
            if spot.act_index == act_index and spot.part_index == hit.part_index:
                return spot
        return None

    def spots_of(self, act_index: int) -> List[Spot]:
        return [s for s in self.spots if s.act_index == act_index]

    def first_spot_of(self, act_index: int) -> Optional[Spot]:
        for spot in self.spots:
            if spot.act_index == act_index:
                return spot
        return None


def activity_label(activity: Activity) -> str:
    """
    The chip's text.

    A lettered region is named by its letter. A region grown from the
    publisher's icon has no letter -- that is why it was anchored in the first
    place -- so it goes by the headline read off its own first line, exactly as
    `renderActivityLayer` does.
    """
    return (activity.label or activity.headline or "").strip()


def build_overlay(
    page: PageRegions,
    oges: Sequence[Oge] = (),
    confidence: Optional[str] = None,
    is_installed: Optional[Callable[[str], bool]] = None,
) -> PageOverlay:
    """
    Join one sheet's regions to the manifest entries sitting on it.

    `confidence` is the book's calibration confidence, or None to leave the gate
    off -- which is how the web reader has always run, because `bakedPage()`
    never copies the value onto the page it builds.
    """
    region_views = [
        linking.region_view(
            a.id, a.label, a.rect, page.page_width, page.page_height, a.anchored
        )
        for a in page.activities
    ]
    oge_views = [
        linking.oge_view(o.id, o.title, o.printed_page, o.posx, o.posy)
        for o in oges
    ]
    links: Dict[int, int] = linking.link_oges(
        region_views, oge_views, confidence=confidence
    )
    # A region grown from an entry's own icon carries that entry's id inside its
    # own and is matched by construction. Claiming it here is what stops the
    # same activity being drawn twice -- once as its region, once as the pin.
    links = linking.apply_anchored_ids(links, region_views, oge_views)

    spots: List[Spot] = []
    for act_index, activity in enumerate(page.activities):
        oge = oges[links[act_index]] if act_index in links else None
        guid = oge.guid if oge is not None else None
        installed = bool(oge.is_installed) if oge is not None else False
        if guid and is_installed is not None:
            installed = bool(is_installed(guid))
        label = activity_label(activity)
        for part_index, rect in enumerate(activity.piece_rects):
            spots.append(Spot(
                act_index=act_index,
                part_index=part_index,
                rect=rect,
                label=label,
                item_count=len(activity.items_in_part(part_index)),
                guid=guid,
                installed=installed,
                activity=activity,
            ))

    claimed = {oges[oi].id for oi in links.values()}
    pins = [
        Pin(oge=o, x_pct=o.posx, y_pct=o.posy)
        for o in oges
        if o.id not in claimed and o.has_position
    ]
    return PageOverlay(page, spots, pins)
