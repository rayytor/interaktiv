"""
Joining the publisher's interactive activities to the regions on the page.

This is the one implementation of a rule that used to exist twice: once in
`js/interactive-links.js`, for the web reader that has since been removed, and
once, in part, inside `tools/hotspot_extraction/scanner/anchors.py`, for the
baker. The baker only ever asked which manifest entries were *left over* and so
kept a private copy of the pairing loop; the native reader needs the pairing
itself, and the scorer in `scanner/score.py` needs it a third time. All three
now call `link_oges` and read what they need off the same answer.

The rule:

A region is detected from the sheet -- a letter, a column, a rectangle in PDF
user space. Its interactive version comes from the publisher's manifest, one
entry carrying a hand-typed title, the *printed* page, and the position of the
icon the publisher's own reader draws, as a percentage of the sheet. Neither
identifies an activity alone. The title carries the label, which tells `b` from
`c`, but a page routinely runs two columns that both restart at `a`, so the label
aliases; the icon position says which of the two it is, but on its own it is only
a point in a margin where two activities can sit within a few percent of each
other. So candidates are drawn by label and settled by position, and the result
is an assignment: one region to one entry, never one entry claimed by two.
"""

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# How far from its region an icon may sit and still belong to it.
#
# The icon is set beside the activity it opens, near its first line, so the two
# tops agree closely. A label match tolerates the wider drift, because the letter
# is already strong evidence and the icon is sometimes hung beside the second
# line of a long instruction; an entry with no label to match has only its
# position to argue with and is held to the tight one.
LABELLED_DRIFT = 10.0      # % of sheet height
UNLABELLED_DRIFT = 5.0     # % of sheet height

# How far, across the sheet, an icon may sit outside the region it opens.
#
# The icon is hung beside its activity, which for a column of exercises means
# the margin next to it, and for a banner activity means a box set into the
# activity's own line -- a QR code at the far end of a full-width bar is still
# that bar's icon. Measuring the gap to the region's own edges covers both,
# where asking only which half of the sheet each one falls in covers neither: it
# rejects the QR code beside its own activity, and it accepts a margin icon
# level with a region it has nothing to do with.
ICON_REACH = 20.0          # % of sheet width
REACH_WEIGHT = 0.5         # how much of that gap counts against a pair

# Why an entry did not link, when a caller asks. Ordered by how far the entry
# got, nearest-miss first, so a scorecard can attribute each one to a single
# cause without rebuilding the rule in the harness.
LINK_REASONS = (
    "no-position",
    "confidence-gated",
    "label-mismatch",
    "drift",
    "wrong-side",
)

# An id of the form `p12-oge-<ogeid>` says the region was grown from that entry's
# own icon, so it carries its match by construction.
_ANCHORED_ID_RE = re.compile(r"-oge-(.+)$")


def parse_oge_label(title: str, printed_page: Optional[int] = None) -> Optional[str]:
    """
    A title's printed page number and revision marks stripped off, or None.

    The title usually repeats the printed page the activity is on -- "42/a",
    and, typed without the separator, "56b-ed2". Only the page this entry
    actually belongs to is stripped, so a book whose activities are numbered
    rather than lettered keeps its number ("42/1" -> "1", not "").
    """
    if not title:
        return None
    s = str(title).lower().strip()

    if printed_page is not None and printed_page > 0:
        s = re.sub(rf"^0*{printed_page}\s*[-/\\.:]?\s*", "", s).strip()
        # In Turkish books the title often carries the 0-indexed or PDF page
        # number (printed_page - 1), e.g. "18" for page 19, "41_5.Soru" for 42.
        s = re.sub(rf"^0*{printed_page - 1}\s*[-/\\.:_]?\s*", "", s).strip()

    s = re.sub(r"^\s*\d{1,4}\s*[-/:]\s*", "", s).strip()

    # Editorial revision marks the house appends: "-ed", " .ed", "ed2", and
    # "b ed ed" where a title was revised twice.
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"[\s._\-]*\bed\s*\d*$", "", s).strip()
        s = re.sub(r"[\s._\-]+$", "", s).strip()

    soru = re.match(r"^(\d{1,2})\s*\.?\s*soru", s, re.IGNORECASE)
    if soru:
        return soru.group(1)

    if re.fullmatch(r"[a-zçğıöşü]", s) or re.fullmatch(r"\d{1,2}", s):
        return s

    # A page number typed into the label without a separator ("247n .ed" for n
    # on page 24) leaves digits in front of the label once the page strip misses.
    tail = re.match(r"^\d{1,4}\s*[/\-.:]?\s*([a-zçğıöşü])$", s)
    if tail:
        return tail.group(1)

    return None


@dataclass(frozen=True, slots=True)
class RegionView:
    """
    One detected region, reduced to what the join actually argues with.

    The two callers hold different objects -- the baker an `ActivityRegion` whose
    rect is a tuple, the reader an `Activity` whose rect is a `Rect` -- so both
    normalise into this before asking.
    """

    id: str
    label: str          # lower-cased, "" when the region has no letter
    top_pct: float      # top of the label-bearing piece, % of sheet height
    bottom_pct: float   # foot of it, % of sheet height, counted from the top
    left_pct: float     # % of sheet width
    right_pct: float    # % of sheet width
    anchored: bool
    oge_id: Optional[str] = None   # the entry a publisher icon bound it to


@dataclass(frozen=True, slots=True)
class OgeView:
    """One manifest entry, reduced the same way."""

    id: str
    label: Optional[str]
    top_pct: Optional[float]   # None when the entry carries no position
    left_pct: Optional[float]  # where the icon hangs, % of sheet width


def region_view(
    region_id: str,
    label: Optional[str],
    rect: Sequence[float],
    page_width: float,
    page_height: float,
    anchored: bool = False,
    oge_id: Optional[str] = None,
) -> RegionView:
    """Build a `RegionView` from a rect in PDF user space (y-up)."""
    x0, y0, x1, y1 = rect[0], rect[1], rect[2], rect[3]
    return RegionView(
        id=str(region_id),
        label=(label or "").lower().strip(),
        # The top of the label-bearing piece: the icon is set against the start
        # of the activity, not against the middle of everything it covers.
        top_pct=((page_height - y1) / page_height) * 100.0 if page_height else 0.0,
        bottom_pct=((page_height - y0) / page_height) * 100.0 if page_height else 100.0,
        left_pct=(x0 / page_width) * 100.0 if page_width else 0.0,
        right_pct=(x1 / page_width) * 100.0 if page_width else 100.0,
        anchored=bool(anchored),
        oge_id=oge_id or None,
    )


def oge_view(
    oge_id: str,
    title: str,
    printed_page: Optional[int],
    posx: Optional[float],
    posy: Optional[float],
) -> OgeView:
    """Build an `OgeView` from a `kitapogeList` entry's fields."""
    return OgeView(
        id=str(oge_id),
        label=parse_oge_label(title, printed_page),
        top_pct=float(posy) if isinstance(posy, (int, float)) else None,
        left_pct=float(posx) if isinstance(posx, (int, float)) else None,
    )


def link_oges(
    regions: Sequence[RegionView],
    oges: Sequence[OgeView],
    confidence: Optional[str] = None,
    explain: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[int, int]:
    """
    Assign manifest entries to regions, one to one.

    Returns `{region_index: oge_index}` -- **positions, not ids**. A region id is
    not unique on a sheet: a page that runs two columns prints `a` twice, and
    `p36-d` naming two different activities is the ordinary case, not a
    pathology. Keying the answer by id lets the second one overwrite the first,
    which frees the first one's entry to be "unplaced" and grown a second time
    as a phantom anchored region sitting on top of the real one. (The web
    now-removed web reader's `linkInteractiveOges` returned a Map keyed by
    activity id and had exactly that flaw; it is why one of the two `d`s on
    such a page silently lost its interactive marker, and scoring the
    catalogue both ways puts the difference at 22 manifest entries.) Indices
    cannot collide, so callers that want
    ids resolve them themselves and two activities sharing a letter stay two
    activities.

    `confidence` is the book's calibration confidence ("strong", "weak",
    "none"); passing None leaves the gate off, which is how the baker has always
    run. `explain`, when given, is filled with
    `{oge_id: {"reasons": set[str], "candidates": int, "matched": bool}}` -- the
    rule reporting on itself, so a scorecard never has to restate it.
    """
    links: Dict[int, int] = {}

    # Every entry starts out having reached nothing; the walk below records the
    # furthest gate each one got to, and the settle marks the winners.
    if explain is not None:
        for o in oges:
            explain[o.id] = {"reasons": set(), "candidates": 0, "matched": False}

    def note(oge: OgeView, reason: str) -> None:
        if explain is not None:
            explain[oge.id]["reasons"].add(reason)

    if not regions or not oges:
        return links

    pairs: List[Tuple[float, int, int]] = []
    for oi, oge in enumerate(oges):
        if oge.top_pct is None:
            note(oge, "no-position")
            continue
        for ri, region in enumerate(regions):
            # With no confidence in the letters this book was calibrated on,
            # only a region grown from an entry's own icon may be linked.
            if confidence == "none" and not region.anchored and not region.oge_id:
                note(oge, "confidence-gated")
                continue
            # Weak calibration: an entry with no label of its own has nothing
            # but geometry to argue with, so it is held to the anchors.
            if confidence == "weak" and not oge.label and not region.anchored and not region.oge_id:
                note(oge, "confidence-gated")
                continue
            # A label on both sides that disagrees is a different activity,
            # whatever the geometry says.
            if oge.label and region.label and oge.label != region.label:
                note(oge, "label-mismatch")
                continue
            # How far across the sheet the icon sits from the region: nothing
            # while it is over the region's own width, and the shortfall once it
            # is past either edge.
            reach = 0.0
            if oge.left_pct is not None:
                if oge.left_pct < region.left_pct:
                    reach = region.left_pct - oge.left_pct
                elif oge.left_pct > region.right_pct:
                    reach = oge.left_pct - region.right_pct

            both_labelled = bool(oge.label and region.label)
            if reach > ICON_REACH and not both_labelled:
                note(oge, "wrong-side")
                continue

            drift = abs(oge.top_pct - region.top_pct)
            # An icon standing within the region's own box is against the
            # activity by the plainest evidence there is, wherever down it the
            # publisher chose to hang it, so it is not also asked to be level
            # with the first line.
            inside = (
                reach == 0.0
                and region.top_pct <= oge.top_pct <= region.bottom_pct
            )
            limit = LABELLED_DRIFT if both_labelled else UNLABELLED_DRIFT
            if drift > limit and not inside:
                note(oge, "drift")
                continue

            if explain is not None:
                explain[oge.id]["candidates"] += 1
            pairs.append((drift + reach * REACH_WEIGHT, oi, ri))

    # One region to one entry: the closest pair is settled first, and neither of
    # its two halves is offered again. Two activities that share a letter are two
    # activities, so the second `a` on the page cannot inherit the first one's
    # interactive version by being tested second.
    pairs.sort(key=lambda p: p[0])
    used_oge: Set[int] = set()
    used_region: Set[int] = set()
    for _cost, oi, ri in pairs:
        if oi in used_oge or ri in used_region:
            continue
        used_oge.add(oi)
        used_region.add(ri)
        links[ri] = oi
        if explain is not None:
            explain[oges[oi].id]["matched"] = True

    return links


def apply_anchored_ids(
    links: Dict[int, int],
    regions: Sequence[RegionView],
    oges: Sequence[OgeView],
) -> Dict[int, int]:
    """
    Claim, for each region grown from an entry's own icon, that entry.

    A region synthesised from an anchor carries the entry's id inside its own,
    and a region an icon was *bound* to carries it in `oge_id`; either way it is
    matched by construction rather than by the drift join. Claiming it
    is what stops the same activity being drawn twice -- once as its region and
    again as the corner pin for an entry nothing linked.

    A construction claim outranks a positional one, so any other region the
    drift join had paired with the same entry gives it up. That happens for
    real: an icon hung beside a numbered question grows its own region, and the
    question it sits next to is the nearer of the two by a fraction of a
    percent. Without the eviction the entry would be claimed twice -- the
    activity drawn once where it belongs and once on the question beside it.

    Mutates and returns `links`, keyed by index like `link_oges`.
    """
    by_id = {o.id: i for i, o in enumerate(oges)}
    for ri, region in enumerate(regions):
        # A region bound to an entry states it outright; one grown from an
        # entry's icon says it through its id. Both are construction claims.
        if region.oge_id:
            oge_id = region.oge_id
        else:
            m = _ANCHORED_ID_RE.search(region.id)
            if not m:
                continue
            oge_id = m.group(1)
        oi = by_id.get(oge_id)
        if oi is None:
            continue
        for other_ri, other_oi in list(links.items()):
            if other_oi == oi and other_ri != ri:
                del links[other_ri]
        links[ri] = oi
    return links


def linked_oge_ids(links: Dict[int, int], oges: Sequence[OgeView]) -> Set[str]:
    """The ids of the entries some region claimed."""
    return {oges[oi].id for oi in links.values()}


def unlinked_oges(links: Dict[int, int], oges: Sequence[OgeView]) -> List[OgeView]:
    """The entries no region claimed, in manifest order."""
    claimed = set(links.values())
    return [o for i, o in enumerate(oges) if i not in claimed]


@dataclass(frozen=True, slots=True)
class Hit:
    """What sits under a point: an activity, and which of its pieces."""

    activity: Any
    part_index: int
    rect: Any


def hit_test(page: Any, x: float, y: float) -> Optional[Hit]:
    """
    The smallest region covering a point in PDF user space, or None.

    Smallest-area-wins, so a question nested inside an activity is reachable
    without having to miss the activity around it. Parts are tested separately
    and never unioned: a region that flows into a second column claims clicks in
    each of its pieces, and nothing in between.

    `page` is anything with an `activities` sequence whose members carry `parts`
    (or a `rect`) as 4-tuples of floats -- both the reader's `PageRegions` and a
    plain dict from a bake satisfy this.
    """
    activities = getattr(page, "activities", None)
    if activities is None and isinstance(page, dict):
        activities = page.get("activities")
    if not activities:
        return None

    best: Optional[Hit] = None
    best_area = float("inf")
    for act in activities:
        parts = getattr(act, "parts", None)
        if parts is None and isinstance(act, dict):
            parts = act.get("parts")
        if not parts:
            rect = getattr(act, "rect", None)
            if rect is None and isinstance(act, dict):
                rect = act.get("rect")
            parts = [rect] if rect else []
        for part_index, r in enumerate(parts):
            if x < r[0] or x > r[2] or y < r[1] or y > r[3]:
                continue
            area = (r[2] - r[0]) * (r[3] - r[1])
            if area < best_area:
                best_area = area
                best = Hit(activity=act, part_index=part_index, rect=r)
    return best
