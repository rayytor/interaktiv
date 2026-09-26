"""
How good are the activity hotspots, book by book?

This is the Python port of `tools/hotspot_extraction/tests/scorecard.mjs`, and
it exists because the detector is Python: a scorer in another language cannot
share the detector's own geometry, so it kept private mirrors of
`MIN_HOTSPOT`, `TALL_REGION` and `WHOLE_TOL` that could drift out of step, and
it could not be called from a fitting loop at all. Here `cuts`, `rect_overlap`
and the thresholds are imported from `.regions`, so there is one implementation
of "did this region cut that block", and the join is
`interaktiv_core.linking.link_oges`, which the GTK reader already uses.

Three kinds of number come out, unchanged from the JS:

  - **yield**, what the detector found at all. A book that quietly finds
    nothing looks exactly like a book with nothing to find, and only the
    calibration evidence beside it tells the two apart.
  - **join**, how many of the publisher's interactive entries were matched to a
    region, and how far each region's top sits from the icon the publisher hung
    beside it. The manifest is the one piece of outside truth there is, and it
    validates presence and the top edge -- never extent.
  - **violations**, rules that are bugs by definition rather than by taste: a
    region may not cover part of a drawn block, may not overlap another region,
    and may not be a sliver or most of the sheet. These need no labelled data,
    so they run over the whole catalogue -- and they are the only check on
    extent there is.

There is deliberately no hand-labelled ground truth, and the recorded baseline
is a snapshot of the detector's own prior output, so "meets baseline" means
"did not regress", never "is correct".

One deliberate difference from the JS. `link_oges` returns region *indices*
rather than ids, because a region id is not unique on a sheet -- a page running
two columns prints `a` twice. The JS `linkInteractiveOges` keys its answer by
activity id, so the second `a` overwrites the first, which frees the first
entry to be scored as unplaced. Numbers from this module and from the JS
therefore differ on exactly those pages, and this module is the correct one.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .profile import RULER
from .regions import (
    cuts,
    encloses,
    rect_area,
    rect_overlap,
)

# The rules are written in these three numbers, and the detector is written in
# them too -- `scanner/profile.py` explains why the scorer reads a frozen copy
# rather than the live constants. A fitting loop that could widen `WHOLE_TOL`
# would erase every cut violation without moving a single region edge, and the
# same is true of `MIN_HOTSPOT` for slivers and `TALL_REGION` for tall regions.
# `RULER` is pinned to the shipped defaults, so a profile changes what the
# detector draws and never what counts as a violation.
MIN_HOTSPOT = RULER.MIN_HOTSPOT
TALL_REGION = RULER.TALL_REGION
WHOLE_TOL = RULER.WHOLE_TOL

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from interaktiv_core.linking import (  # noqa: E402
    LINK_REASONS,
    link_oges,
    oge_view,
    parse_oge_label,
    region_view,
)

Rect = Tuple[float, float, float, float]

# Provenance stamped into every scorecard this module writes. Bumped when a
# change here would move the numbers, so a stale baseline is detectable rather
# than merely wrong.
SCORER = "python-1"

# Every interactive entry in a book's manifest lands in exactly one of these.
#
# The point is attribution. A single match rate says how much is missing but not
# what to fix next, and the rate used to be computed over the entries whose
# printed page had already resolved -- so a folio failure removed an entry from
# the denominator instead of counting against it, and the two largest classes of
# failure were invisible by construction. Here the denominator is the manifest,
# and everything that is not `ok` names its own cause.
BUCKETS: Tuple[str, ...] = (
    "ok",                # matched a region that breaks no rule
    "rule-violation",    # matched a region, but that region is a bug by definition
    "unresolved-page",   # sayfano reached no PDF page through the folio map
    "page-not-read",     # resolved to a page outside this run's page slice
    "page-blank",        # the sheet was read and no region was found on it at all
    "anchor-excluded",   # posx/posy are (0,0): the entry marks no point on a sheet
    "anchor-nogrow",     # handed to the detector as an anchor; no region grew
    "contested",         # a candidate region existed and another entry won it
    *LINK_REASONS,       # no-position, confidence-gated, label-mismatch, drift, wrong-side
)


def bucket_for(why: Optional[Dict[str, Any]]) -> str:
    """
    Which gate an entry reached, given everything the page walk learned about it.

    `LINK_REASONS` is ordered nearest-miss first, so the first reason present is
    the furthest the entry actually got. An entry rejected on drift by one region
    and on its label by another is a drift problem: the label test never had to
    be the thing standing in the way.
    """
    if not why:
        return "page-blank"
    if why.get("candidates", 0) > 0:
        return "contested"
    reasons = why.get("reasons") or set()
    for reason in LINK_REASONS:
        if reason in reasons:
            return reason
    return "page-blank"


def quantiles(xs: Sequence[float]) -> Optional[Dict[str, float]]:
    if not xs:
        return None
    s = sorted(xs)
    at = lambda f: s[min(len(s) - 1, int(len(s) * f))]
    return {
        "median": round(at(0.5), 2),
        "p75": round(at(0.75), 2),
        "p90": round(at(0.9), 2),
        "p99": round(at(0.99), 2),
        "max": round(s[-1], 2),
    }


def takeable(
    blocks: Iterable[Rect],
    rect: Rect,
    page_width: float,
    page_height: float,
    require_length: bool,
) -> List[Rect]:
    """
    Blocks a region could sensibly have taken whole.

    Two kinds of drawn matter have to be dropped first or the count is noise.
    Geometry reaching outside the sheet is an artefact of the path bbox, not
    something on the page -- one workbook sheet 570 pt wide ruled answer cells
    recorded out to x=703. And a shape far larger than the region is a tinted
    background the question is merely standing on: `panel_for` refuses to snap
    out to those for exactly that reason, so clipping one is not a sliced panel.
    """
    out: List[Rect] = []
    for b in blocks:
        if b[0] < -2 or b[1] < -2 or b[2] > page_width + 2 or b[3] > page_height + 2:
            continue
        if (b[3] - b[1]) > TALL_REGION * page_height:
            continue
        if not require_length:
            out.append(b)
            continue
        along = min(rect[3], b[3]) - max(rect[1], b[1])
        if along >= 0.5 * (b[3] - b[1]):
            out.append(b)
    return out


@dataclass
class Tally:
    """
    Every running count a book's row is built from.

    A plain bag rather than locals inside the walk because the same page has to
    be scored two ways -- read live out of the PDF, and replayed out of a bake.
    Those two must agree exactly or the replay is measuring something else, and
    the only way to be sure is for both to run this code.
    """
    regions: int = 0
    parts: int = 0
    questions: int = 0
    empty_regions: int = 0
    pages_with_regions: int = 0
    markers: int = 0
    markers_in_region: int = 0
    panels_cut: int = 0
    solutions_cut: int = 0
    solutions_covered: int = 0
    solutions_total: int = 0
    off_sheet_solutions: int = 0
    tall: int = 0
    slivers: int = 0
    overlaps: int = 0
    # The same five counts again, restricted to regions grown from the
    # publisher's icon. A rule is a rule whoever broke it, but a rise has to be
    # attributable before it can be fixed, and "more regions, so more of
    # everything" is not an explanation.
    by_anchored: Dict[str, int] = field(default_factory=lambda: {
        "panelsCut": 0, "solutionsCut": 0, "tall": 0, "slivers": 0, "overlaps": 0,
    })
    anchored: int = 0
    oges_seen: int = 0
    oges_on_blank_pages: int = 0
    # One bucket per manifest entry, filled as its sheet is walked. Entries
    # whose page is never reached stay unset and are resolved after the walk.
    bucket_of: Dict[str, str] = field(default_factory=dict)
    drifts: List[float] = field(default_factory=list)


def merge_tallies(parts: Sequence[Tally]) -> Tally:
    """
    Add up tallies from slices of one book, as if the book had been walked once.

    A 407-page book takes 23 seconds to replay, and an evaluation that farms out
    whole books waits on that one book however many cores are idle. Slicing it
    into page chunks is only legitimate if the seams are invisible, and two
    fields decide whether they are.

    `bucket_of` files each manifest entry once, on the *first* sheet that claims
    it, so `parts` must arrive in page order and the merge must keep the
    earliest claim -- otherwise a book whose folio maps one printed page to two
    sheets would file the entry from whichever chunk happened to finish first.

    `drifts` is a bag whose only use is quantiles, so its order does not matter.
    Everything else is a counter and simply adds.
    """
    out = Tally()
    for t in parts:
        out.regions += t.regions
        out.parts += t.parts
        out.questions += t.questions
        out.empty_regions += t.empty_regions
        out.pages_with_regions += t.pages_with_regions
        out.markers += t.markers
        out.markers_in_region += t.markers_in_region
        out.panels_cut += t.panels_cut
        out.solutions_cut += t.solutions_cut
        out.solutions_covered += t.solutions_covered
        out.solutions_total += t.solutions_total
        out.off_sheet_solutions += t.off_sheet_solutions
        out.tall += t.tall
        out.slivers += t.slivers
        out.overlaps += t.overlaps
        out.anchored += t.anchored
        out.oges_seen += t.oges_seen
        out.oges_on_blank_pages += t.oges_on_blank_pages
        for k, v in t.by_anchored.items():
            out.by_anchored[k] = out.by_anchored.get(k, 0) + v
        for oid, bucket in t.bucket_of.items():
            out.bucket_of.setdefault(oid, bucket)
        out.drifts.extend(t.drifts)
    return out


def tally_page(
    t: Tally,
    p: int,
    res: Dict[str, Any],
    diag: Optional[Dict[str, List[Rect]]],
    page_oges: Sequence[Dict[str, Any]],
    was_anchor_ids: Set[str],
) -> None:
    """
    Score one sheet: its regions against the rules, its entries against the join.

    `res` is the page as detection returns it or as a bake replays it:
    `pageWidth`, `pageHeight`, `confidence`, and `activities` whose `rect` and
    `parts` are tuples in PDF user space (y-up).
    """
    activities: List[Dict[str, Any]] = res["activities"]
    page_w: float = res["pageWidth"]
    page_h: float = res["pageHeight"]

    if activities:
        t.pages_with_regions += 1
    t.regions += len(activities)

    all_parts: List[Rect] = []
    anchored_parts: Set[int] = set()
    part_owner: Dict[int, str] = {}
    # Which regions on this sheet break a rule. A manifest entry matched to one
    # of these is not a success -- the region exists and is wrong -- so the join
    # below files it under `rule-violation` rather than `ok`.
    bad_regions: Set[str] = set()

    for act in activities:
        own: List[Rect] = act.get("parts") or [act["rect"]]
        from_anchor = bool(act.get("anchored"))
        t.parts += len(own)
        items = act.get("items") or []
        t.questions += len(items)
        if not items:
            t.empty_regions += 1

        for rect in own:
            all_parts.append(rect)
            part_owner[id(rect)] = act["id"]
            if from_anchor:
                anchored_parts.add(id(rect))

            h = rect[3] - rect[1]
            if h > TALL_REGION * page_h:
                t.tall += 1
                bad_regions.add(act["id"])
                if from_anchor:
                    t.by_anchored["tall"] += 1
            if h < MIN_HOTSPOT or (rect[2] - rect[0]) < MIN_HOTSPOT:
                t.slivers += 1
                bad_regions.add(act["id"])
                if from_anchor:
                    t.by_anchored["slivers"] += 1

            if diag is not None:
                markers = diag.get("markers") or []
                panels = diag["panels"]
                if markers:
                    panels = [
                        p for p in panels
                        if sum(1 for m in markers if encloses(p, m, tol=-2.0)) <= 1
                    ]
                for panel in takeable(panels, rect, page_w, page_h, True):
                    if cuts(rect, panel, whole_tol=WHOLE_TOL):
                        t.panels_cut += 1
                        bad_regions.add(act["id"])
                        if from_anchor:
                            t.by_anchored["panelsCut"] += 1
                for block in takeable(diag["solutions"], rect, page_w, page_h, False):
                    if cuts(rect, block, whole_tol=WHOLE_TOL):
                        t.solutions_cut += 1
                        bad_regions.add(act["id"])
                        if from_anchor:
                            t.by_anchored["solutionsCut"] += 1

    # `separate_rects` is supposed to have pushed these apart already, so
    # anything still intersecting is a region stealing another's clicks.
    for i in range(len(all_parts)):
        for j in range(i + 1, len(all_parts)):
            if rect_overlap(all_parts[i], all_parts[j]) > 1.0:
                t.overlaps += 1
                bad_regions.add(part_owner[id(all_parts[i])])
                bad_regions.add(part_owner[id(all_parts[j])])
                if id(all_parts[i]) in anchored_parts or id(all_parts[j]) in anchored_parts:
                    t.by_anchored["overlaps"] += 1

    if diag is not None:
        t.markers += len(diag["markers"])
        for m in diag["markers"]:
            if any(rect_overlap(r, m) > 0 for r in all_parts):
                t.markers_in_region += 1
        on_sheet = [
            b for b in diag["solutions"]
            if b[0] >= -2 and b[1] >= -2 and b[2] <= page_w + 2 and b[3] <= page_h + 2
        ]
        t.off_sheet_solutions += len(diag["solutions"]) - len(on_sheet)
        t.solutions_total += len(on_sheet)
        for block in on_sheet:
            if any(rect_overlap(r, block) >= 0.5 * rect_area(block) for r in all_parts):
                t.solutions_covered += 1

    # ---- the join against the publisher's manifest
    if not page_oges:
        return
    t.oges_seen += len(page_oges)
    if not activities:
        t.oges_on_blank_pages += len(page_oges)

    # A region carrying an entry by construction, keyed by the entry.
    #
    # Two shapes mean the same thing. A region *grown* from an entry's own icon
    # carries the id inside its own. A region the icon merely pointed into is
    # bound to the entry and keeps its own label, so it names the entry in
    # `ogeId` instead -- the icon said which activity the entry meant, not that
    # there was another activity there.
    anchor_region_for: Dict[str, str] = {}
    prefix = f"p{p}-oge-"
    for a in activities:
        if a.get("ogeId"):
            anchor_region_for[str(a["ogeId"])] = a["id"]
        elif a["id"].startswith(prefix):
            anchor_region_for[a["id"][len(prefix):]] = a["id"]
    t.anchored += len(anchor_region_for)

    region_views = [
        region_view(
            region_id=a["id"],
            label=a.get("label"),
            rect=a["rect"],
            page_width=page_w,
            page_height=page_h,
            anchored=bool(a.get("anchored")),
            oge_id=a.get("ogeId"),
        )
        for a in activities
    ]
    oge_views = [
        oge_view(
            oge_id=o["id"],
            title=o.get("baslik") or "",
            printed_page=o.get("sayfano"),
            posx=o.get("posx"),
            posy=o.get("posy"),
        )
        for o in page_oges
    ]

    explain: Dict[str, Dict[str, Any]] = {}
    links = link_oges(region_views, oge_views, confidence=None, explain=explain)
    # Indices, not ids: two activities that share a letter stay two activities.
    linked_region_for: Dict[str, str] = {
        oge_views[oi].id: activities[ri]["id"] for ri, oi in links.items()
    }

    for o in page_oges:
        oid = str(o["id"])
        if oid in t.bucket_of:
            continue   # an entry is filed once, on the first sheet that claims it
        # A match has to be a match to a region that is not itself a bug. The
        # region existing is not the question -- an anchored region always
        # exists, which is exactly why anchoring used to count as success by
        # construction and told us nothing.
        act_id = linked_region_for.get(oid) or anchor_region_for.get(oid)
        if act_id:
            t.bucket_of[oid] = "rule-violation" if act_id in bad_regions else "ok"
            continue
        if not activities:
            t.bucket_of[oid] = "page-blank"
            continue
        # (0, 0) is the publisher's "belongs to no point on this sheet", so the
        # entry is never offered to the detector as an anchor at all.
        if o.get("posx") == 0 and o.get("posy") == 0:
            t.bucket_of[oid] = "anchor-excluded"
            continue
        if oid in was_anchor_ids:
            t.bucket_of[oid] = "anchor-nogrow"
            continue
        t.bucket_of[oid] = bucket_for(explain.get(oid))

    for ri, oi in links.items():
        act = activities[ri]
        top = ((page_h - act["rect"][3]) / page_h) * 100.0 if page_h else 0.0
        posy = page_oges[oi].get("posy")
        if isinstance(posy, (int, float)):
            t.drifts.append(abs(float(posy) - top))


def finish_row(t: Tally, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Turn a finished tally into the book's row, filing whatever the walk never
    reached.

    Those leftovers are the entries the old denominator dropped: their printed
    page resolved to no sheet, so no sheet ever offered them to the join and
    they cost the score nothing at all.
    """
    oges: Sequence[Dict[str, Any]] = ctx["oges"]
    pages: int = ctx["pages"]
    pages_for_printed: Dict[int, List[int]] = ctx["pagesForPrinted"]
    pages_spec = ctx.get("pagesSpec")

    printed_seen = list(pages_for_printed.keys())
    lowest = min(printed_seen) if printed_seen else None
    highest = max(printed_seen) if printed_seen else None

    for o in oges:
        oid = str(o["id"])
        if oid in t.bucket_of:
            continue
        # On a page slice, a printed page beyond the sheets read is not a folio
        # failure, it is simply out of frame; on a whole-book run the two
        # coincide and this reports nothing.
        out_of_frame = bool(
            pages_spec and lowest is not None
            and (o.get("sayfano", 0) < lowest or o.get("sayfano", 0) > highest)
        )
        t.bucket_of[oid] = "page-not-read" if out_of_frame else "unresolved-page"

    counted = Counter(t.bucket_of.values())
    buckets = {b: counted.get(b, 0) for b in BUCKETS}
    for b, n in counted.items():
        buckets.setdefault(b, n)

    n_oges = len(oges)
    row: Dict[str, Any] = {
        "book": os.path.basename(ctx["file"]),
        "bookId": ctx["bookId"],
        "source": ctx.get("source", "bake"),
        "pages": pages,
        "calibration": ctx.get("calibration"),
        "yield": {
            "regions": t.regions,
            "parts": t.parts,
            "questions": t.questions,
            "pagesWithRegions": t.pages_with_regions,
            "blankPageShare": round(1 - t.pages_with_regions / pages, 3) if pages else 0,
            "emptyRegionShare": round(t.empty_regions / t.regions, 3) if t.regions else 0,
        },
        "join": {
            "oges": n_oges,
            "ogesOnReadPages": t.oges_seen,
            "matched": buckets["ok"],
            "anchored": t.anchored,
            # Against every interactive entry in the manifest. A partial run
            # reports its out-of-frame entries as `page-not-read` rather than
            # quietly leaving them out of the denominator.
            "matchRate": round(buckets["ok"] / n_oges, 3) if n_oges else None,
            # Entries whose printed page reached no sheet at all: the folio
            # map's own failure rate, previously invisible.
            "unresolvedPage": buckets["unresolved-page"],
            "folioCollisions": ctx.get("folioCollisions", 0),
            "onPagesWithNoRegion": t.oges_on_blank_pages,
            "drift": quantiles(t.drifts),
        },
        "buckets": buckets,
        "violations": {
            "panelsCut": t.panels_cut,
            "solutionsCut": t.solutions_cut,
            "tall": t.tall,
            "slivers": t.slivers,
            "overlaps": t.overlaps,
        },
        "violationsFromAnchors": dict(t.by_anchored),
        "stats": {
            "markers": t.markers,
            "markersOutsideRegions": t.markers - t.markers_in_region,
            "solutionCoverage": (
                round(t.solutions_covered / t.solutions_total, 3)
                if t.solutions_total else None
            ),
            # Answer blocks recorded outside the sheet. Not a region bug -- a
            # `detect_solution_spaces` one -- but it hides real cuts, so it is watched.
            "offSheetSolutions": t.off_sheet_solutions,
        },
    }
    if ctx.get("peakRssMb") is not None:
        row["peakRssMb"] = ctx["peakRssMb"]
    return row


# ------------------------------------------------------------------ inputs

def interactive_oges(book_id: str, root: str = _PROJECT_ROOT) -> List[Dict[str, Any]]:
    """
    The publisher's interactive entries for a book, or [] when none are cached.

    `sayfaustuoge` is the publisher's own flag for an entry that sits on a page.
    The ones without it carry sayfano 0 and posx/posy 0 -- book-level activities
    that belong to no sheet, and would otherwise all be placed in the top-left
    corner of the first one.
    """
    if not book_id:
        return []
    meta_path = os.path.join(root, "activities_meta", f"{book_id}.json")
    if not os.path.isfile(meta_path):
        return []
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return [
        o for o in (meta.get("kitapogeList") or [])
        if o.get("ogeturu") == 1 and o.get("data")
        and o.get("sayfaustuoge") and o.get("sayfano")
    ]


def page_list(spec: Optional[str], total: int) -> List[int]:
    """Parse "16", "16,23", "10-20" into a page list."""
    if not spec:
        return list(range(1, total + 1))
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            if a.strip().isdigit() and b.strip().isdigit():
                out.extend(range(int(a), int(b) + 1))
                continue
        if part.isdigit():
            out.append(int(part))
    return [p for p in out if 1 <= p <= total]


def _invert_folio(entries: Iterable[Tuple[int, Optional[int]]]) -> Dict[int, List[int]]:
    by_printed: Dict[int, List[int]] = {}
    for p, printed in entries:
        if printed is None:
            continue
        by_printed.setdefault(printed, []).append(p)
    return by_printed


def _collisions_in(m: Dict[int, List[int]]) -> int:
    return sum(1 for v in m.values() if len(v) > 1)


def _group_by_printed_page(
    oges: Sequence[Dict[str, Any]]
) -> Dict[int, List[Dict[str, Any]]]:
    by: Dict[int, List[Dict[str, Any]]] = {}
    for o in oges:
        by.setdefault(o["sayfano"], []).append(o)
    return by


def _to_rect(a: Sequence[float]) -> Rect:
    return (float(a[0]), float(a[1]), float(a[2]), float(a[3]))


# ------------------------------------------------------------------ scoring

def score_baked(
    book_id: str,
    root: str = _PROJECT_ROOT,
    pages_spec: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Score a book from what `scan.py` already wrote, without the PDF.

    Re-parsing every PDF to answer a question about the *join* is what made this
    loop expensive: the regions did not change, only the rule applied to them.
    A bake plus its geometry sidecar carries everything the scoring reads, so
    the whole catalogue can be rescored in seconds. Only a change inside the
    detector needs a re-bake.
    """
    d = os.path.join(root, "activities", "books", book_id)
    regions_path = os.path.join(d, "regions.json")
    diag_path = os.path.join(d, "diagnostics.json.gz")
    if not os.path.isfile(regions_path):
        raise FileNotFoundError(f"not baked: {book_id}")
    with open(regions_path, "r", encoding="utf-8") as f:
        baked = json.load(f)

    # Without the sidecar there is no drawn geometry, so every violation check
    # silently passes and the book scores as clean -- which is worse than not
    # scoring it, because it looks like a result.
    if not os.path.isfile(diag_path):
        raise FileNotFoundError(f"no diagnostics sidecar; re-bake {book_id}")
    with gzip.open(diag_path, "rb") as f:
        diag_all = json.loads(f.read().decode("utf-8"))
    if diag_all.get("fingerprint") != baked.get("fingerprint"):
        raise ValueError(f"diagnostics sidecar is stale; re-bake {book_id}")
    diag_pages = diag_all.get("pages") or {}

    pages = page_list(pages_spec, baked["pageCount"])
    folio_by_page = baked["folio"]["byPage"]
    pages_for_printed = _invert_folio(
        (p, folio_by_page.get(str(p))) for p in pages
    )
    oges = interactive_oges(book_id, root)
    oges_by_printed = _group_by_printed_page(oges)
    anchors_by_page = baked.get("anchors") or {}
    baked_pages = baked.get("pages") or {}

    t = Tally()
    for p in pages:
        key = str(p)
        page = baked_pages.get(key)
        dp = diag_pages.get(key)
        printed = folio_by_page.get(key)
        page_oges = oges_by_printed.get(printed, []) if printed is not None else []

        res = {
            "pageWidth": page["pageWidth"] if page else (dp["pageWidth"] if dp else 0),
            "pageHeight": page["pageHeight"] if page else (dp["pageHeight"] if dp else 0),
            "confidence": baked["calibration"]["confidence"],
            "activities": [
                {
                    **a,
                    "rect": _to_rect(a["rect"]),
                    "parts": [_to_rect(r) for r in (a.get("parts") or [])],
                    "items": a.get("items") or [],
                }
                for a in (page["activities"] if page else [])
            ],
        }
        # A sheet with no regions is baked as nothing at all, but it may still
        # be the page the publisher put an entry on -- and that entry is a
        # `page-blank` failure, not an unreachable one. Skipping it here would
        # quietly move it into the folio's column instead.
        page_diag = None
        if dp:
            page_diag = {
                "panels": [_to_rect(r) for r in dp["panels"]],
                "solutions": [_to_rect(r) for r in dp["solutions"]],
                "markers": [_to_rect(r) for r in dp["markers"]],
            }
        tally_page(t, p, res, page_diag, page_oges, set(anchors_by_page.get(key) or []))

    return finish_row(t, {
        "file": f"{book_id}.pdf",
        "bookId": book_id,
        "pages": len(pages),
        "oges": oges,
        "calibration": {
            "enabled": baked["calibration"]["enabled"],
            "confidence": baked["calibration"]["confidence"],
            "markerSize": baked["calibration"].get("markerSize"),
            "styles": baked["calibration"].get("markerStyles") or [],
            "folioOffset": baked["folio"]["offset"],
        },
        "pagesForPrinted": pages_for_printed,
        "pagesSpec": pages_spec,
        "folioCollisions": _collisions_in(pages_for_printed),
        "source": "bake",
    })


def tally_pages(
    book_id: str,
    folio_map: Dict[int, int],
    pages: Iterable[Tuple[int, float, float, List[Dict[str, Any]], Optional[Dict[str, Any]], Sequence[str]]],
    *,
    root: str = _PROJECT_ROOT,
    calibration: Optional[Dict[str, Any]] = None,
) -> Tuple[Tally, List[int]]:
    """
    Walk some sheets of a book and return the running counts, unfinished.

    Split out from `score_pages` so that a slice of a book can be tallied in one
    process and the book finished in another: `finish_row` needs the whole
    manifest and the whole folio to file what the walk never reached, and a
    chunk has neither. Returns the page numbers it actually saw, because the
    caller has to know which sheets a `page-not-read` verdict may be based on.
    """
    oges = interactive_oges(book_id, root)
    oges_by_printed = _group_by_printed_page(oges)

    t = Tally()
    seen_pages: List[int] = []
    for page_num, width, height, activities, diag, anchor_ids in pages:
        seen_pages.append(page_num)
        printed = folio_map.get(page_num)
        page_oges = oges_by_printed.get(printed, []) if printed is not None else []
        res = {
            "pageWidth": width,
            "pageHeight": height,
            "confidence": (calibration or {}).get("confidence"),
            "activities": [
                {
                    **a,
                    "rect": _to_rect(a["rect"]),
                    "parts": [_to_rect(r) for r in (a.get("parts") or [])],
                    "items": a.get("items") or [],
                }
                for a in activities
            ],
        }
        page_diag = None
        if diag is not None:
            page_diag = {
                "panels": [_to_rect(r) for r in diag["panels"]],
                "solutions": [_to_rect(r) for r in diag["solutions"]],
                "markers": [_to_rect(r) for r in diag["markers"]],
            }
        tally_page(t, page_num, res, page_diag, page_oges, set(anchor_ids or ()))
    return t, seen_pages


def finish_book(
    book_id: str,
    tally: Tally,
    seen_pages: Sequence[int],
    folio_map: Dict[int, int],
    page_count: int,
    *,
    root: str = _PROJECT_ROOT,
    source: str = "replay",
    pages_spec: Optional[str] = None,
    calibration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Turn a whole book's tally -- however many chunks it was gathered in -- into its row."""
    pages_for_printed = _invert_folio((p, folio_map.get(p)) for p in seen_pages)
    return finish_row(tally, {
        "file": f"{book_id}.pdf",
        "bookId": book_id,
        "pages": len(seen_pages) or page_count,
        "oges": interactive_oges(book_id, root),
        "calibration": calibration,
        "pagesForPrinted": pages_for_printed,
        "pagesSpec": pages_spec,
        "folioCollisions": _collisions_in(pages_for_printed),
        "source": source,
    })


def score_pages(
    book_id: str,
    page_count: int,
    folio_map: Dict[int, int],
    pages: Iterable[Tuple[int, float, float, List[Dict[str, Any]], Optional[Dict[str, Any]], Sequence[str]]],
    *,
    root: str = _PROJECT_ROOT,
    source: str = "replay",
    pages_spec: Optional[str] = None,
    calibration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Score a book from detection output held in memory, with no bake on disk.

    `score_baked` reads regions and geometry back off disk, which is right for
    grading the catalogue and useless inside a fitting loop: a candidate profile
    produces regions that are never written anywhere. This takes the same two
    things -- the serialized activities and the page's drawn blocks -- straight
    from the detector and runs the identical rules over them, so a replayed
    score and a baked score of the same regions are the same number.

    `pages` yields `(page_num, width, height, activities, diagnostics, anchor_ids)`
    per sheet, where `activities` have already been through
    `clean_page_activities` and `serialize_activity`, exactly as the bake writes
    them. A sheet with no regions still has to be offered, because an entry the
    publisher put on it is a `page-blank` failure rather than an unreachable one.
    """
    tally, seen = tally_pages(book_id, folio_map, pages, root=root, calibration=calibration)
    return finish_book(
        book_id, tally, seen, folio_map, page_count,
        root=root, source=source, pages_spec=pages_spec, calibration=calibration,
    )


def baked_book_ids(root: str = _PROJECT_ROOT) -> List[str]:
    """Every book with a bake on disk, in a stable order."""
    d = os.path.join(root, "activities", "books")
    if not os.path.isdir(d):
        return []
    return sorted(
        name for name in os.listdir(d)
        if os.path.isfile(os.path.join(d, name, "regions.json"))
    )


def score_catalogue(
    book_ids: Optional[Sequence[str]] = None,
    root: str = _PROJECT_ROOT,
    pages_spec: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Score every baked book, in the same shape `scorecard.mjs --json` wrote.

    A book whose bake cannot be scored is reported with its error rather than
    skipped: a book quietly missing from the catalogue looks like a book with
    nothing wrong.
    """
    ids = list(book_ids) if book_ids is not None else baked_book_ids(root)
    books: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for book_id in ids:
        try:
            books.append(score_baked(book_id, root, pages_spec))
        except (FileNotFoundError, ValueError, KeyError) as exc:
            errors.append({"bookId": book_id, "error": str(exc)})
    out: Dict[str, Any] = {
        "builtAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        # Which scorer produced these numbers. A baseline recorded by the old
        # JS scorer is not comparable to one recorded here -- the join differs
        # by about 22 entries catalogue-wide -- so a comparison that silently
        # mixes the two reports a scorer change as an algorithm change.
        "scorer": SCORER,
        "pagesSpec": pages_spec,
        "books": books,
    }
    if errors:
        out["errors"] = errors
    return out
