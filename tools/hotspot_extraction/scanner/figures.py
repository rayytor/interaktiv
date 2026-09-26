"""
Whole-Figure Detection.

A picture on a textbook page is almost never one image. It is a stack of tiles
the exporter cut it into, a photograph with a drawn frame around it, a diagram
whose arrows and labels are vector strokes laid over a bitmap, and underneath
it a caption line that names it. Every one of those is a separate primitive,
and a region grown against the primitives takes whichever of them happens to
fall inside the text it read -- which is how a hotspot comes to cover the top
half of a diagram and stop.

This module reassembles them. Adjacent picture primitives are welded into one
figure, the vector art drawn over a figure is pulled into it, the caption
beneath it joins it, and page furniture -- the full-bleed ground, the margin
rule -- is thrown out. What comes back is the set of things the page means as
single pictures, which is the unit a hotspot has to take whole or leave alone.
"""

from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .primitives import ImageRect, VectorDrawing

Rect = Tuple[float, float, float, float]

FIGURE_WELD = 6.0        # pt: gap between two picture primitives that still reads as one picture
FIGURE_REACH = 0.6       # a tile welds across a gap this fraction of its own smaller side
FIGURE_SPAN = 30.0       # pt: widest gap a weld may ever bridge
FIGURE_MIN = 24.0        # pt: longest side a picture must reach to be a figure, not an icon
FIGURE_AREA = 600.0      # pt^2: ink a picture must cover to be worth holding together
FIGURE_TILE = 4.0        # pt: smallest side a primitive can have and still join a figure
FIGURE_SHARE = 0.6       # fraction of the content box past which a picture is the page's ground
CAPTION_GAP = 14.0       # pt: how far under a figure its caption may sit
CAPTION_SIDE = 24.0      # pt: how far beside a figure a margin caption may sit
CAPTION_LINES = 4        # how many lines of caption follow the first
ART_COVER = 0.5          # fraction of a stroke that must lie over a figure to belong to it
MARKER_REACH = 44.0      # pt: how far left of a picture its activity's label may stand

# What a caption calls itself, in the Turkish and English books in the library.
CAPTION_RE = re.compile(
    r"^\s*(görsel|şekil|resim|tablo|grafik|harita|fotoğraf|çizelge|"
    r"figure|fig\.?|image|table|chart|picture|photo|map)\b",
    re.IGNORECASE | re.UNICODE,
)


@dataclass
class Figure:
    """One picture as the page means it: art, frame and caption together."""
    rect: Rect              # everything, caption included
    art: Rect               # the picture itself, without the caption
    tiles: int              # how many primitives were welded into it
    caption: Optional[str] = None


def _area(r: Rect) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _union(a: Rect, b: Rect) -> Rect:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _overlap(a: Rect, b: Rect) -> float:
    return (
        max(0.0, min(a[2], b[2]) - max(a[0], b[0])) *
        max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    )


def _reach(a: Rect, b: Rect) -> float:
    """
    How wide a gap these two primitives may weld across.

    A fixed clearance cannot serve both cases a page puts up. Tiles an exporter
    sliced a photograph into abut exactly, and six points is generous. A diagram
    is the opposite: a constellation of small pictures with the arrows, labels
    and white space of the drawing between them, and its pieces stand tens of
    points apart while still plainly being one picture. What separates that from
    two pictures that merely sit near each other is scale -- the pieces of one
    drawing are close relative to their own size -- so the clearance is measured
    from the primitives rather than fixed, and capped so it can never bridge a
    column gutter.
    """
    side = min(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1])
    return min(max(FIGURE_WELD, FIGURE_REACH * side), FIGURE_SPAN)


def _touches(
    a: Rect,
    b: Rect,
    slack: Optional[float] = None,
    gutters: Sequence[float] = (),
) -> bool:
    """Whether two rectangles overlap or sit within welding distance."""
    if slack is None:
        slack = _reach(a, b)
    if not (
        a[0] - slack <= b[2] and b[0] - slack <= a[2] and
        a[1] - slack <= b[3] and b[1] - slack <= a[3]
    ):
        return False
    # A column gutter is the page saying these two things are read apart. The
    # reach weld is wide enough to step over a narrow one, and stepping over it
    # joined a photograph in the left column to a QR code in the right -- one
    # figure across two questions, which no hotspot can then take whole.
    # Compared at the centres rather than the edges: a photograph set to the
    # full measure of its column overhangs the channel by a point or two, and
    # asking whether the bare edges straddle the gutter let exactly those
    # through. Where each tile's middle falls is not a near thing.
    ca = (a[0] + a[2]) / 2.0
    cb = (b[0] + b[2]) / 2.0
    lo, hi = min(ca, cb), max(ca, cb)
    return not any(lo < g < hi for g in gutters)


def _weld(
    rects: Sequence[Rect],
    slack: Optional[float] = None,
    gutters: Sequence[float] = (),
) -> List[List[int]]:
    """
    Group rectangles into connected clusters by proximity.

    Union-find over `_touches`, which is transitive in effect: a column of
    tiles welds end to end even though the first and last are far apart.
    """
    n = len(rects)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _touches(rects[i], rects[j], slack, gutters):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _caption_for(
    art: Rect,
    lines: List[dict],
    content_box: Rect,
) -> Tuple[Optional[Rect], Optional[str]]:
    """
    The caption that names this figure, when the page sets one.

    A caption sits under the picture, or in the margin beside it, and opens by
    saying what it is -- "Görsel 1.3", "Tablo 2". Requiring that word keeps the
    next paragraph of body text from being read as a caption merely for
    following a picture closely.
    """
    best: Optional[dict] = None
    for ln in lines:
        text = _line_text(ln)
        if not CAPTION_RE.match(text):
            continue
        under = (
            art[1] - CAPTION_GAP <= ln["y1"] <= art[1] + 2.0 and
            ln["x0"] < art[2] and ln["x1"] > art[0]
        )
        beside = (
            ln["y0"] < art[3] and ln["y1"] > art[1] and
            (art[0] - CAPTION_SIDE <= ln["x1"] <= art[0] + 2.0 or
             art[2] - 2.0 <= ln["x0"] <= art[2] + CAPTION_SIDE)
        )
        if not (under or beside):
            continue
        if best is None or ln["y1"] > best["y1"]:
            best = ln

    if best is None:
        return None, None

    # A caption runs on: the lines directly below it, set at its own left edge
    # and in its own measure, belong to it too.
    rect = (best["x0"], best["y0"], best["x1"], best["y1"])
    words = [_line_text(best)]
    cursor = best["y0"]
    for ln in sorted(
        (l for l in lines if l["y1"] <= best["y0"] + 1.0 and abs(l["x0"] - best["x0"]) <= 6.0),
        key=lambda l: -l["y1"],
    )[:CAPTION_LINES]:
        if cursor - ln["y1"] > CAPTION_GAP:
            break
        if ln["x1"] > best["x1"] + CAPTION_SIDE:
            break
        rect = _union(rect, (ln["x0"], ln["y0"], ln["x1"], ln["y1"]))
        words.append(_line_text(ln))
        cursor = ln["y0"]

    # Clamp to the sheet's content, so a caption in the foot margin cannot drag
    # a figure down past the bottom of the page.
    rect = (
        max(rect[0], content_box[0]),
        max(rect[1], content_box[1]),
        min(rect[2], content_box[2]),
        min(rect[3], content_box[3]),
    )
    return rect, " ".join(words)[:160].strip()


def _line_text(line: dict) -> str:
    return " ".join(s.text for s in line["spans"]).strip()


def detect_figures(
    images: Sequence[ImageRect],
    drawings: Sequence[VectorDrawing],
    lines: List[dict],
    content_box: Rect,
    marker_rects: Sequence[Rect] = (),
    gutters: Sequence[float] = (),
) -> List[Figure]:
    """
    Reassemble the page's picture primitives into whole figures.

    Args:
        images: every image rectangle the page declares.
        drawings: every vector shape, for the art drawn over a picture.
        lines: text lines of the content area, as `layout._text_lines` returns
            them, for captions.
        content_box: the sheet's live area; a picture that fills it is ground.
        marker_rects: where the page's activity markers stand. A run of tiles
            that reaches across two of them is a row of separate pictures, one
            per question, and welding it into a single figure would produce a
            block no hotspot can ever take whole.
        gutters: the x of each channel between the page's columns. Nothing
            welds across one.

    Returns:
        Figures in reading order, top of the sheet first.
    """
    cb_area = max(1.0, _area(content_box))

    tiles: List[Rect] = []
    for im in images:
        r = im.bbox
        if r[2] - r[0] < FIGURE_TILE or r[3] - r[1] < FIGURE_TILE:
            continue
        if _overlap(r, content_box) <= 0.0:
            continue
        tiles.append((
            max(r[0], content_box[0]), max(r[1], content_box[1]),
            min(r[2], content_box[2]), min(r[3], content_box[3]),
        ))

    if not tiles:
        return []

    # The reach weld follows a diagram across its own white space, which is what
    # a diagram needs and what a stack of separate pictures must not get: the
    # answer box of question a and the QR code of question b sit five points
    # apart on these pages, and welding them makes one block that both hotspots
    # then cut. Where a cluster reaches past a marker, the marker is the page's
    # own statement that a new question starts there, and the cluster is split
    # along it.
    groups: List[List[int]] = []
    for group in _weld(tiles, gutters=gutters):
        box = tiles[group[0]]
        for i in group[1:]:
            box = _union(box, tiles[i])

        inside = [
            mr for mr in marker_rects
            if box[1] <= (mr[1] + mr[3]) / 2.0 <= box[3]
            and box[0] - MARKER_REACH <= mr[0] and mr[2] <= box[2] + MARKER_REACH
        ]
        if len(inside) < 2 or len(group) == 1:
            groups.append(group)
            continue

        seams = sorted({mr[3] for mr in inside}, reverse=True)
        bands: Dict[int, List[int]] = {}
        for i in group:
            mid = (tiles[i][1] + tiles[i][3]) / 2.0
            band = sum(1 for seam in seams if mid < seam)
            bands.setdefault(band, []).append(i)
        for members in bands.values():
            groups.extend(
                [members[k] for k in sub]
                for sub in _weld([tiles[k] for k in members], gutters=gutters)
            )

    figures: List[Figure] = []
    for group in groups:
        art = tiles[group[0]]
        for i in group[1:]:
            art = _union(art, tiles[i])

        # Size, judged on reach and ink rather than on both sides at once. A
        # scale bar or a labelled arrow is two hundred points long and twenty
        # high; requiring both sides to clear the same bar threw those out as
        # icons, and the activity that said "look at the graphic below" then
        # grew over its own instruction and stopped.
        w, h = art[2] - art[0], art[3] - art[1]
        if max(w, h) < FIGURE_MIN or _area(art) < FIGURE_AREA:
            continue
        # The ground the page is printed on is not a picture of anything.
        if _area(art) >= FIGURE_SHARE * cb_area:
            continue

        # Vector art laid over the picture -- arrows, callouts, a drawn frame --
        # is part of it. Only strokes that sit mostly on the picture count, so a
        # rule running the width of the page does not drag the figure with it.
        for d in drawings:
            dr = d.rect
            d_area = _area(dr)
            if d_area <= 0.0 or d_area >= FIGURE_SHARE * cb_area:
                continue
            if _overlap(dr, art) >= ART_COVER * d_area:
                art = _union(art, dr)

        art = (
            max(art[0], content_box[0]), max(art[1], content_box[1]),
            min(art[2], content_box[2]), min(art[3], content_box[3]),
        )
        if _area(art) >= FIGURE_SHARE * cb_area:
            continue

        # A picture inside a drawn panel stays a figure of its own. Growth may
        # decline to snap out to the panel -- a tinted ground a question merely
        # stands on is not the question's -- and the picture would then have
        # nothing holding it together. Emitting both is safe because a part that
        # takes the panel whole has taken the figure whole by construction, so
        # the inner block can only ever tighten the rule, never loosen it.
        cap_rect, cap_text = _caption_for(art, lines, content_box)
        rect = _union(art, cap_rect) if cap_rect else art

        figures.append(
            Figure(
                rect=rect,
                art=art,
                tiles=len(group),
                caption=cap_text,
            )
        )

    # A figure welded out of tiles can end up enclosing a smaller one it merely
    # sits beside. Keep the outermost.
    figures.sort(key=lambda f: _area(f.rect), reverse=True)
    kept: List[Figure] = []
    for fig in figures:
        if any(_overlap(fig.rect, k.rect) >= 0.9 * _area(fig.rect) for k in kept):
            continue
        kept.append(fig)

    kept.sort(key=lambda f: -f.rect[3])
    return kept
