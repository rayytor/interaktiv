"""
Content hotspots: everything a page holds besides its activities.

An activity hotspot says "this is an exercise". A teacher at the board also
wants the passage the exercise is about, the poem, the explanation, the
picture, the table: to touch it and have it fill the screen, and to step from
one to the next until the page is done. So once the activities of a sheet are
settled, what is left of the sheet is cut into content regions here.

No model decides anything in this module. What is content is whatever the
activities did not take, and where one piece ends is read off the page: a
drawn panel, a figure with its caption and a ruled table are each one thing;
lines of type are blocks where the page leaves white between them.

The cutting is a recursive XY-cut over the sheet's atoms (lines of type,
figures, panels, tables, and the activity regions as opaque obstacles): a node
is split down a clear channel that runs its whole height, or across at the
clear bands between its rows, until nothing in it asks to be split. Because
every split is a straight clear line, two leaves can never overlap, and no
leaf can overlap an activity: the invariant the bake asserts holds by
construction. The order the leaves come out in is the order the page is read
in, activities included, which is what "next" means in focus mode.

A page that is furniture rather than content gets nothing: a chapter's opening
page (`is_opener`), the front matter (`front_matter_end`), and the back matter
(`back_matter_start`): the bibliography and the map plates after it.

One kind of page is the other way round: a form that fills its sheet -- a
performance task, a self-assessment form, a rubric. It is one thing, and a
teacher opens it whole, so `page_form` makes the sheet a single activity.
"""

from dataclasses import dataclass, field
import re
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .figures import detect_figures
from .layout import BODY_LINE_CHARS, BODY_LINE_WIDTH, COLUMN_MIN_W, FOOTER_BAND, HEADER_BAND, PageLayout
from .markers import _is_bold
from .primitives import PagePrimitives
from .regions import (
    HEADING_RATIO, MIN_HOTSPOT, TALL_REGION, ActivityRegion, PageGeometry, rect_area, rect_overlap,
)

Rect = Tuple[float, float, float, float]

CONTENT_TALL = 0.5        # share of the sheet past which a block of type is cut at a paragraph seam
BLOCK_GAP = 0.9           # multiple of the line height: white this deep between two rows parts two blocks
LINE_SLACK = 0.2          # share of a line's height, top and bottom, that is room for ascenders, not ink
CHANNEL = 8.0             # pt: narrowest clear channel that parts two columns
WORD_GAP = 6.0            # pt: white inside a line of type past which it is two runs side by side
COLUMN_LINES = 3          # lines of type a side of a channel must hold to be a column
PARA_GAP = 0.15           # multiple of the line height by which a paragraph seam is deeper than a line seam
PAD = 3.0                 # pt: breathing room round a block of type
MIN_CHARS = 12            # letters a block of type must carry to be worth a hotspot
MIN_FIGURE = 60.0         # pt: longest side a picture must reach to be a hotspot of its own
MIN_FIGURE_AREA = 3000.0  # pt^2
HELD = 0.5                # share of a line or block inside a rect past which it is that rect's
INK_THIN = 4.0            # pt: a drawn shape no thicker than this is a stroke, not a body
INK_TOUCH = 3.0           # pt: a stroke this near a drawn body is part of the same drawing
INK_GROUND = 0.2          # share of the sheet past which a drawn shape is a ground, not part of a diagram
INK_LIMIT = 1500          # drawn shapes on one sheet past which strokes are not looked at one by one
FORCED_CHANNEL = 1.5      # pt: channel a node too tall to be a hotspot may be parted down
SOLID_SEAM = 0.3          # multiple of the line height: a seam this shallow does not part type from a drawn block
SOLID_EDGE = 1.0          # pt: taken off each side of a drawn block, so two that touch have a seam between them
SMALL_TYPE = 0.95         # share of the body size under which type is a label or a caption, not running text
LABEL_GAP = 3.0           # multiple of the line height: white this deep parts even a diagram from its labels
DISPLAY_RATIO = 3.5       # multiple of the body size at which a chapter's number is set on its opening page
DISPLAY_MIN = 36.0        # pt
FRONT_LIMIT = 0.2         # share of the book the front matter may run to
FRONT_PAGES = 40

_WORD = re.compile(r"\w", re.UNICODE)
_CONTENTS = re.compile(r"^\s*(i̇çi̇ndeki̇ler|içindekiler|contents|table of contents|inhalt|inhaltsverzeichnis)\s*$",
                       re.IGNORECASE)
_TOC_LINE = re.compile(r"\S(\s*\.{4,}|\s*…{2,}|\s{2,})\s*\d{1,3}\s*$")


@dataclass
class Atom:
    """One thing on the sheet the cut may not go through."""
    rect: Rect
    kind: str                       # "line", "rule", "ink", "figure", "panel", "table", "taken"
    line: Optional[dict] = None     # the line of type, for kind "line"
    lines: List[dict] = field(default_factory=list)   # lines a block atom holds
    ref: Any = None                 # the activity region, for kind "taken"


@dataclass
class Leaf:
    """A node nothing asks to split: one content region, or one activity standing where it stood."""
    atoms: List[Atom]

    @property
    def taken(self) -> Optional[Atom]:
        return self.atoms[0] if len(self.atoms) == 1 and self.atoms[0].kind == "taken" else None


def _bbox(rects: Sequence[Rect]) -> Rect:
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def _line_rect(ln: dict) -> Rect:
    return (ln["x0"], ln["y0"], ln["x1"], ln["y1"])


def _line_text(ln: dict) -> str:
    return re.sub(r"\s+", " ", " ".join(s.text for s in ln["spans"])).strip()


def _line_size(ln: dict) -> float:
    spans = [s for s in ln["spans"] if s.text.strip()]
    return max((s.size for s in spans), default=0.0)


def _held(rect: Rect, inner: Rect, share: float = HELD) -> bool:
    area = rect_area(inner)
    return area > 0.0 and rect_overlap(rect, inner) / area >= share


def _centre_in(inner: Rect, rect: Rect) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2.0, (inner[1] + inner[3]) / 2.0
    return rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]


def is_heading(ln: dict, body_size: float, by_weight: bool = True) -> bool:
    """
    Whether a line of type is set as a heading: larger than the body, or bold throughout and short.

    A heading opens what stands under it, so a block is never cut between a
    heading and its first line, and a cut is always allowed above one. Weight
    alone is weaker evidence than size (the name of a poem's book under the
    poem is bold too), so a caller that cannot see anything under the line
    asks with `by_weight` off.
    """
    spans = [s for s in ln["spans"] if s.text.strip()]
    if not spans:
        return False
    if _line_size(ln) >= HEADING_RATIO * body_size:
        return True
    text = _line_text(ln)
    return by_weight and all(_is_bold(s) for s in spans) and len(text) <= 70 and not re.search(r"[.,;]$", text)


# -- atoms -----------------------------------------------------------------

def _figures(prim: PagePrimitives, layout: PageLayout, geom: PageGeometry) -> List[Rect]:
    """
    The sheet's pictures, each with its caption, measured against the sheet and not the text.

    The geometry the activities were settled against clips a picture to the
    content box, which is as wide as the type: right for deciding what an
    activity may take, wrong for a picture set out into the margin, which
    would come back as the part of it that stands over the text.
    """
    columns = sorted(layout.columns, key=lambda c: c.x0)
    box = (0.0, FOOTER_BAND, prim.width, prim.height - HEADER_BAND)
    return [f.rect for f in detect_figures(
        prim.images, prim.drawings, geom.lines, box,
        gutters=[(a.x1 + b.x0) / 2.0 for a, b in zip(columns, columns[1:]) if b.x0 > a.x1],
    )]


def _block_atoms(prim: PagePrimitives, layout: PageLayout, geom: PageGeometry,
                 taken: Sequence[Rect]) -> List[Atom]:
    """The drawn things no activity stands on, outermost only: panels, figures and ruled tables."""
    kinds = {"panel": "panel", "figure": "figure", "grid": "table"}
    found = sorted(
        [b for b in geom.blocks if b.get("kind") in ("panel", "grid") and rect_area(tuple(b["rect"])) > 0.0]
        + [{"rect": r, "kind": "figure"} for r in _figures(prim, layout, geom)],
        key=lambda b: -rect_area(tuple(b["rect"])),
    )
    panels = [tuple(b["rect"]) for b in found if b["kind"] == "panel"]
    out: List[Atom] = []
    for b in found:
        r = tuple(b["rect"])
        if any(rect_overlap(r, t) > 1.0 for t in taken):
            continue
        # Pictures standing in neighbouring panels weld into one "figure" across them (a page of
        # comic frames). The page drew the panels; they are the things, and the weld is not.
        if b["kind"] == "figure" and sum(1 for p in panels if _held(r, p, 0.9)) >= 2:
            continue
        if any(_held(a.rect, r, 0.9) for a in out):
            continue
        kind = kinds[b["kind"]]
        if kind == "figure" and (max(r[2] - r[0], r[3] - r[1]) < MIN_FIGURE or rect_area(r) < MIN_FIGURE_AREA):
            continue
        out.append(Atom(rect=r, kind=kind))
    return out


def _gap(a: Rect, b: Rect) -> float:
    """The clear distance between two rects; 0 when they touch or overlap."""
    return max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3], 0.0)


def _visible(d: Any) -> bool:
    """Whether a drawn shape prints: it has a stroke, or a fill that is not the paper's white."""
    if d.stroke is not None:
        return True
    return d.fill is not None and not all(c >= 0.98 for c in d.fill)


def _ink_atoms(prim: PagePrimitives, taken: Sequence[Rect], blocks: Sequence[Atom]) -> List[Atom]:
    """
    The vector art no block accounts for: the boxes, arrows and curves of a drawn diagram.

    A diagram set in vectors is not a figure to the page geometry (that reads
    pictures), so its labels would be cut into blocks of type with nothing
    between them. Each drawn body is an atom here, and a stroke counts when it
    touches a body: the arrow between two boxes, the axis under a curve. A
    stroke standing alone is a rule -- a column rule, an answer line -- and must
    not close the channel it stands in. Grounds, running heads and whatever an
    activity or a block already holds are left out.
    """
    sheet = prim.width * prim.height
    low, high = FOOTER_BAND, prim.height - HEADER_BAND
    shapes: List[Rect] = []
    for d in prim.drawings:
        if _visible(d):
            shapes.append(tuple(d.rect))
    shapes.extend(tuple(im.bbox) for im in prim.images)

    bodies: List[Rect] = []
    strokes: List[Rect] = []
    for r in shapes:
        w, h = r[2] - r[0], r[3] - r[1]
        mid = (r[1] + r[3]) / 2.0
        if max(w, h) < INK_THIN or not low <= mid <= high or r[0] < -2.0 or r[2] > prim.width + 2.0:
            continue
        if w * h >= INK_GROUND * sheet or w >= 0.9 * prim.width or h >= 0.9 * (high - low):
            continue
        if any(rect_overlap(r, t) > 1.0 for t in taken) or any(_centre_in(r, b.rect) for b in blocks):
            continue
        (bodies if min(w, h) > INK_THIN else strokes).append(r)

    out = [Atom(rect=r, kind="ink") for r in bodies]
    if len(bodies) + len(strokes) <= INK_LIMIT:
        out.extend(Atom(rect=r, kind="ink") for r in strokes if any(_gap(r, b) <= INK_TOUCH for b in bodies))
    return out


def _trim(rect: Rect, taken: Sequence[Rect]) -> Optional[Rect]:
    """A line's rect with whatever an activity region covers of it cut away, or None when little is left."""
    r = list(rect)
    for t in taken:
        if rect_overlap(tuple(r), t) <= 0.0:
            continue
        if t[1] <= r[1] and t[3] >= r[3]:           # covered top to bottom: give up the covered end
            if t[0] <= r[0]:
                r[0] = t[2]
            elif t[2] >= r[2]:
                r[2] = t[0]
            else:
                return None
        elif (r[3] + r[1]) / 2.0 >= (t[3] + t[1]) / 2.0:
            r[1] = t[3]
        else:
            r[3] = t[1]
        if r[2] - r[0] < MIN_HOTSPOT or r[3] - r[1] < 2.0:
            return None
    return (r[0], r[1], r[2], r[3])


def _runs(ln: dict) -> List[dict]:
    """
    A line of type as the runs it is set in: split wherever it leaves more white than words do.

    Two texts set side by side come back as one line when their columns stand
    close; a line read whole would then bridge the channel between them and the
    cut could never find it.
    """
    out: List[dict] = []
    for s in sorted((s for s in ln["spans"] if s.text.strip()), key=lambda s: s.bbox[0]):
        if out and s.bbox[0] - out[-1]["x1"] < WORD_GAP:
            run = out[-1]
            run["x1"] = max(run["x1"], s.bbox[2])
            run["y0"], run["y1"] = min(run["y0"], s.bbox[1]), max(run["y1"], s.bbox[3])
            run["spans"].append(s)
        else:
            out.append({"x0": s.bbox[0], "x1": s.bbox[2], "y0": s.bbox[1], "y1": s.bbox[3], "spans": [s]})
    return out


def page_atoms(prim: PagePrimitives, layout: PageLayout, geom: PageGeometry,
               activities: Sequence[ActivityRegion]) -> List[Atom]:
    """Everything the cut works with: the activities as obstacles, and what the sheet holds outside them."""
    taken_atoms = [Atom(rect=tuple(p), kind="taken", ref=a) for a in activities for p in (a.parts or [a.rect])]
    taken = [a.rect for a in taken_atoms]
    blocks = _block_atoms(prim, layout, geom, taken)
    atoms: List[Atom] = taken_atoms + blocks

    for ln in (run for whole in geom.lines for run in _runs(whole)):
        r = _line_rect(ln)
        if any(_centre_in(r, t) for t in taken):
            continue
        owner = next((b for b in blocks if _centre_in(r, b.rect)), None)
        if owner is not None:
            owner.lines.append(ln)
            continue
        trimmed = _trim(r, taken)
        if trimmed is not None:
            atoms.append(Atom(rect=trimmed, kind="line", line=ln))

    # Answer rules and boxes the student left outside its box: they join the block above them, and
    # never make a region on their own.
    for b in geom.blocks:
        if b.get("kind") != "cell":
            continue
        r = tuple(b["rect"])
        if any(rect_overlap(r, t) > 1.0 for t in taken) or any(_held(a.rect, r) for a in blocks):
            continue
        atoms.append(Atom(rect=r, kind="rule"))
    atoms.extend(_ink_atoms(prim, taken, blocks))
    return atoms


# -- the cut ---------------------------------------------------------------

def _extent(a: Atom, axis: int) -> Tuple[float, float]:
    """
    An atom's stretch along one axis. Down the sheet a line of type counts by its ink: its box
    reserves room for ascenders and descenders, and in tightly set text the boxes of two lines
    overlap although nothing printed does.
    """
    lo, hi = a.rect[axis], a.rect[axis + 2]
    if axis == 1 and a.kind == "line":
        slack = LINE_SLACK * (hi - lo)
        return lo + slack, hi - slack
    # Two panels drawn edge to edge are two things; the seam between them is where they meet.
    if a.kind in ("figure", "panel", "table") and hi - lo > 4.0 * SOLID_EDGE:
        return lo + SOLID_EDGE, hi - SOLID_EDGE
    return lo, hi


def _valleys(atoms: Sequence[Atom], axis: int) -> List[Tuple[float, float]]:
    """The clear stretches between the atoms' projections on one axis (0: x, 1: y), low to high."""
    spans = sorted(_extent(a, axis) for a in atoms)
    out: List[Tuple[float, float]] = []
    reach = spans[0][1]
    for lo, hi in spans[1:]:
        if lo > reach:
            out.append((reach, lo))
        reach = max(reach, hi)
    return out


def _split(atoms: Sequence[Atom], axis: int, cuts: Sequence[float]) -> List[List[Atom]]:
    """The atoms in the bands the cuts make along one axis, low to high."""
    edges = sorted(cuts)
    bands: List[List[Atom]] = [[] for _ in range(len(edges) + 1)]
    for a in atoms:
        mid = (a.rect[axis] + a.rect[axis + 2]) / 2.0
        bands[sum(1 for e in edges if mid > e)].append(a)
    return [b for b in bands if b]


def _line_height(atoms: Sequence[Atom], fallback: float) -> float:
    heights = [a.rect[3] - a.rect[1] for a in atoms if a.kind == "line"]
    return median(heights) if heights else fallback


def _small(a: Atom, body_size: float) -> bool:
    """
    Whether an atom is diagram matter: drawn ink, a picture, or type that is not running text -- set
    smaller than the body (a caption), or too short to be a line of prose (a label).
    """
    if a.kind in ("ink", "figure"):
        return True
    if a.kind != "line":
        return False
    return (_line_size(a.line) < SMALL_TYPE * body_size or a.rect[2] - a.rect[0] < BODY_LINE_WIDTH
            or len(_line_text(a.line)) < BODY_LINE_CHARS)


def _side(side: Sequence[Atom], body_size: float) -> Optional[str]:
    """
    What one side of a channel is: a "solid" drawn thing, a column of running "text", part of a
    "drawing" (ink and pictures with their labels) -- or None for a list's numbers or a margin
    note, which are no column at all.
    """
    box = _bbox([a.rect for a in side])
    if any(a.kind in ("panel", "table", "taken") for a in side):
        return "solid"
    if box[2] - box[0] < COLUMN_MIN_W:
        return "drawing" if any(a.kind == "figure" for a in side) else None
    if sum(1 for a in side if a.kind == "line" and not _small(a, body_size)) >= COLUMN_LINES:
        return "text"
    drawn = [a.rect for a in side if a.kind in ("ink", "figure")]
    return "drawing" if drawn and _drawing(_bbox(drawn)) else None


def _drawing(box: Rect) -> bool:
    """Whether a box of drawn shapes is large enough to be a diagram rather than an ornament."""
    return max(box[2] - box[0], box[3] - box[1]) >= MIN_FIGURE and rect_area(box) >= MIN_FIGURE_AREA


def _column_cuts(atoms: Sequence[Atom], body_size: float) -> List[float]:
    """
    Where a node parts into columns: every clear channel wide enough, with a column on each side of it.

    The white inside a drawing is not a channel: a diagram's two halves, each
    ink and labels, stay one diagram.
    """
    cuts: List[float] = []
    for lo, hi in _valleys(atoms, 0):
        if hi - lo < CHANNEL:
            continue
        mid = (lo + hi) / 2.0
        left = _side([a for a in atoms if a.rect[2] <= mid], body_size)
        right = _side([a for a in atoms if a.rect[0] >= mid], body_size)
        if left and right and not (left == right == "drawing"):
            cuts.append(mid)
    return cuts


def _row_cuts(atoms: Sequence[Atom], body_size: float, page_h: float) -> List[float]:
    """
    Where a node parts into blocks down the sheet.

    A clear band parts two blocks when it is deep (`BLOCK_GAP` lines), when a
    heading stands under it, or when a drawn thing or an activity stands on
    either side of it: those are whole by themselves. It never parts a heading
    from what it opens. A node of type alone that is still taller than a
    hotspot should be (`CONTENT_TALL`) is cut once more at its deepest
    paragraph seam nearest the middle.
    """
    valleys = _valleys(atoms, 1)
    if not valleys:
        return []
    lh = _line_height(atoms, body_size * 1.2)
    cuts: List[float] = []
    soft: List[Tuple[float, float]] = []
    for lo, hi in valleys:
        mid = (lo + hi) / 2.0
        above = [a for a in atoms if _extent(a, 1)[0] >= hi - 0.01]
        below = [a for a in atoms if _extent(a, 1)[1] <= lo + 0.01]
        # The row standing on the band, and the row hanging under it.
        foot = min(a.rect[1] for a in above)
        head = max(a.rect[3] for a in below)
        upper = [a for a in above if a.rect[1] <= foot + lh / 2.0]
        lower = [a for a in below if a.rect[3] >= head - lh / 2.0]
        # A drawn thing parts the rows only when it stands over or under the other row: a picture
        # set beside a paragraph is not between that paragraph's lines.
        facing = [(u, w) for u in upper for w in lower if min(u.rect[2], w.rect[2]) > max(u.rect[0], w.rect[0])]
        solids = ("figure", "panel", "table")
        solid = any(a.kind in solids for pair in facing for a in pair)
        # Type set hard against a picture (the last line of the paragraph that wraps it) is not
        # parted from it by a seam shallower than the room between two lines.
        if solid and hi - lo < SOLID_SEAM * lh and not any(u.kind in solids and w.kind in solids for u, w in facing):
            solid = False
        # A bold line opens something only when something stands close under it.
        floor = min(a.rect[1] for a in lower)
        opens = any(a.kind != "rule" and a.rect[3] < floor + 0.01
                    and floor - a.rect[3] < (BLOCK_GAP + 2.0 * LINE_SLACK) * lh for a in below)
        upper_heading = _heading_row(upper, body_size, True)
        lower_heading = _heading_row(lower, body_size, opens)
        answer = all(a.kind == "rule" for a in lower)
        # A drawing and the labels and caption set round it are one thing; two pictures set one
        # over the other are two.
        # A label is often bold, and a caption too; a heading that opens the next section is set
        # at the body's size or larger, which they are not.
        section = lower_heading and not upper_heading and all(
            a.kind == "line" and _line_size(a.line) >= SMALL_TYPE * body_size for a in lower)
        reach = LABEL_GAP * lh
        drawn = any(a.kind in ("ink", "figure") for a in
                    [a for a in above if a.rect[1] - hi < reach] + [a for a in below if lo - a.rect[3] < reach])
        diagram = (all(_small(a, body_size) for a in upper + lower) and drawn
                   and not any(u.kind == w.kind == "figure" for u, w in facing)
                   and hi - lo < LABEL_GAP * lh and not section)
        if any(a.kind == "taken" for a in upper + lower):
            cuts.append(mid)
        elif diagram:
            continue
        elif answer and not solid:
            continue                                   # answer space stays with the question above it
        elif upper_heading and not lower_heading and not any(a.kind == "taken" for a in lower):
            continue                                   # a heading opens what stands under it
        elif (solid or (lower_heading and not upper_heading)
              or hi - lo >= (BLOCK_GAP + 2.0 * LINE_SLACK) * lh):
            cuts.append(mid)
        else:
            soft.append((lo, hi))

    if cuts:
        return cuts
    box = _bbox([a.rect for a in atoms])
    if box[3] - box[1] > CONTENT_TALL * page_h and soft:
        # Only at a paragraph seam: a band deeper than the seams between this node's lines. A long
        # paragraph is left whole rather than cut through the middle of a sentence.
        usual = median(v[1] - v[0] for v in soft)
        seams = [v for v in soft if v[1] - v[0] >= usual + PARA_GAP * lh]
        if seams:
            centre = (box[1] + box[3]) / 2.0
            half = (box[3] - box[1]) / 2.0
            # Deep and near the middle: a seam that leaves two pieces of like size.
            lo, hi = max(seams, key=lambda v: (v[1] - v[0]) * (1.0 - abs((v[0] + v[1]) / 2.0 - centre) / half))
            return [(lo + hi) / 2.0]
    return []


def _heading_row(row: Sequence[Atom], body_size: float, by_weight: bool) -> bool:
    """Whether a row is a heading: every line of type in it is set as one (its banner is not asked)."""
    lines = [a for a in row if a.kind == "line"]
    return bool(lines) and all(a.kind in ("line", "ink") for a in row) and all(
        is_heading(a.line, body_size, by_weight) for a in lines)


def xy_cut(atoms: Sequence[Atom], body_size: float, page_h: float, depth: int = 0) -> List[Leaf]:
    """The sheet's atoms as leaves, in reading order. See the module docstring."""
    atoms = list(atoms)
    if not atoms:
        return []
    if len(atoms) == 1:
        return [Leaf(atoms)]
    if depth > 40:
        return _apart(atoms) if any(a.kind == "taken" for a in atoms) else [Leaf(atoms)]

    columns = _column_cuts(atoms, body_size)
    if columns:
        out: List[Leaf] = []
        for band in _split(atoms, 0, columns):
            out.extend(xy_cut(band, body_size, page_h, depth + 1))
        return out

    rows = _row_cuts(atoms, body_size, page_h)
    if rows:
        out = []
        for band in reversed(_split(atoms, 1, rows)):       # top of the sheet first
            out.extend(xy_cut(band, body_size, page_h, depth + 1))
        return out

    if any(a.kind == "taken" for a in atoms):
        return _apart(atoms)

    # Nothing asks to be split, and the node is taller than a hotspot may be. Leaving it out would
    # leave a whole page without one, so it is parted where it can be: down any channel at all, or
    # across at the clear band nearest its middle.
    box = _bbox([a.rect for a in atoms])
    if box[3] - box[1] > (TALL_REGION - 0.005) * page_h:
        channels = [v for v in _valleys(atoms, 0) if v[1] - v[0] >= FORCED_CHANNEL]
        bands = _valleys(atoms, 1)
        if channels:
            lo, hi = max(channels, key=lambda v: v[1] - v[0])
            parts = _split(atoms, 0, [(lo + hi) / 2.0])
        elif bands:
            centre = (box[1] + box[3]) / 2.0
            lo, hi = min(bands, key=lambda v: abs((v[0] + v[1]) / 2.0 - centre))
            parts = list(reversed(_split(atoms, 1, [(lo + hi) / 2.0])))
        else:
            parts = []
        if len(parts) > 1:
            out = []
            for band in parts:
                out.extend(xy_cut(band, body_size, page_h, depth + 1))
            return out
    return [Leaf(atoms)]


def _apart(atoms: Sequence[Atom]) -> List[Leaf]:
    """
    A node no straight line parts, with an activity in it: the activity stands, and what is left of
    the node is kept only as far as it lies wholly to one side of every activity in it.
    """
    taken = [a for a in atoms if a.kind == "taken"]
    rest = [a for a in atoms if a.kind != "taken"]
    out = [Leaf([t]) for t in sorted(taken, key=lambda t: (-t.rect[3], t.rect[0]))]
    sides: Dict[Tuple[int, ...], List[Atom]] = {}
    for a in rest:
        key = []
        for t in taken:
            if a.rect[1] >= t.rect[3]:
                key.append(0)
            elif a.rect[3] <= t.rect[1]:
                key.append(1)
            elif a.rect[2] <= t.rect[0]:
                key.append(2)
            elif a.rect[0] >= t.rect[2]:
                key.append(3)
            else:
                key = None
                break
        if key is not None:
            sides.setdefault(tuple(key), []).append(a)
    for group in sides.values():
        box = _bbox([a.rect for a in group])
        if all(rect_overlap(box, t.rect) <= 1.0 for t in taken):
            out.append(Leaf(group))
    return out


# -- leaves to regions -----------------------------------------------------

def _leaf_lines(leaf: Leaf) -> List[dict]:
    lines = [a.line for a in leaf.atoms if a.kind == "line"]
    for a in leaf.atoms:
        lines.extend(a.lines)
    return sorted(lines, key=lambda ln: (-ln["y1"], ln["x0"]))


def _worth(leaf: Leaf, body_size: float) -> bool:
    """Whether a leaf is something to open: a drawn thing, or type that says something."""
    if any(a.kind in ("figure", "panel", "table") for a in leaf.atoms):
        return True
    ink = [a.rect for a in leaf.atoms if a.kind == "ink"]
    if ink and _drawing(_bbox(ink)):
        return True
    lines = [a.line for a in leaf.atoms if a.kind == "line"]
    if not lines:
        return False                                    # answer rules with nothing above them
    if all(is_heading(ln, body_size) for ln in lines) and len(lines) <= 2:
        return False                                    # a heading with nothing under it
    return sum(len(_WORD.findall(_line_text(ln))) for ln in lines) >= MIN_CHARS


def _headline(leaf: Leaf) -> str:
    lines = _leaf_lines(leaf)
    return _line_text(lines[0])[:140] if lines else ""


def _column_of(rect: Rect, layout: PageLayout) -> int:
    cx = (rect[0] + rect[2]) / 2.0
    for i, c in enumerate(sorted(layout.columns, key=lambda c: c.x0)):
        if c.x0 <= cx <= c.x1:
            return i
    return 0


def _parted(rects: List[List[float]], fixed: Sequence[Rect]) -> None:
    """
    Part the boxes of two leaves that share a sliver, in place.

    The cut ran between the ink of two rows; the boxes are drawn round the
    whole lines, ascender room included, so two neighbours can overlap by that
    room and no more. Two content boxes meet in the middle of it; a content box
    gives way to an activity.
    """
    def shallow(a: Sequence[float], b: Sequence[float]) -> Optional[int]:
        ox = min(a[2], b[2]) - max(a[0], b[0])
        oy = min(a[3], b[3]) - max(a[1], b[1])
        if ox <= 0.0 or oy <= 0.0:
            return None
        return 1 if oy <= ox else 0

    for _ in range(3):
        moved = False
        for i, a in enumerate(rects):
            for t in fixed:
                axis = shallow(a, t)
                if axis is None:
                    continue
                if (a[axis] + a[axis + 2]) / 2.0 >= (t[axis] + t[axis + 2]) / 2.0:
                    a[axis] = max(a[axis], t[axis + 2])
                else:
                    a[axis + 2] = min(a[axis + 2], t[axis])
                moved = True
            for b in rects[i + 1:]:
                axis = shallow(a, b)
                if axis is None:
                    continue
                hi, lo = (a, b) if (a[axis] + a[axis + 2]) / 2.0 >= (b[axis] + b[axis + 2]) / 2.0 else (b, a)
                mid = (max(a[axis], b[axis]) + min(a[axis + 2], b[axis + 2])) / 2.0
                hi[axis], lo[axis + 2] = max(hi[axis], mid), min(lo[axis + 2], mid)
                moved = True
        if not moved:
            break


def _padded(rects: List[List[float]], fixed: Sequence[Rect], box: Rect, drawn: Sequence[bool]) -> None:
    """
    Give every block of type its breathing room, in place, wherever that touches nothing else.

    A panel, a picture or a table standing alone (`drawn`) keeps the edge the
    page drew for it.
    """
    for i, r in enumerate(rects):
        if drawn[i]:
            continue
        for edge in range(4):
            trial = list(r)
            trial[edge] += PAD if edge >= 2 else -PAD
            trial[edge] = min(max(trial[edge], box[edge % 2]), box[edge % 2 + 2])
            others = [o for j, o in enumerate(rects) if j != i] + list(fixed)
            if all(rect_overlap(tuple(trial), tuple(o)) <= 0.0 for o in others):
                r[edge] = trial[edge]


def content_regions(
    prim: PagePrimitives,
    layout: PageLayout,
    geom: PageGeometry,
    activities: Sequence[ActivityRegion],
    page_num: int,
) -> List[ActivityRegion]:
    """
    One sheet's regions in reading order: its activities as they stand, and its content between them.

    The activities are returned untouched (the same objects); the content
    regions carry `kind = "content"`, no label, and their first line as a
    headline.
    """
    body = layout.body_font_size
    leaves = xy_cut(page_atoms(prim, layout, geom, activities), body, prim.height)
    taken = [tuple(p) for a in activities for p in (a.parts or [a.rect])]

    kept: List[Tuple[Leaf, Optional[List[float]]]] = []
    for leaf in leaves:
        if leaf.taken is not None:
            kept.append((leaf, None))
            continue
        if not _worth(leaf, body):
            continue
        # A leaf may be as tall as the sheet: a full-page map is one thing, the cut has already
        # parted whatever could be parted, and leaving it out would leave the page with no way in.
        # (An *activity* that tall is left out by snap.settle; what it held comes back here as
        # content, which claims nothing about being one exercise.)
        kept.append((leaf, list(_bbox([a.rect for a in leaf.atoms]))))

    rects = [r for _, r in kept if r is not None]
    _parted(rects, taken)
    drawn = [len(leaf.atoms) == 1 and leaf.atoms[0].kind in ("figure", "panel", "table")
             for leaf, r in kept if r is not None]
    _padded(rects, taken, (0.0, 0.0, prim.width, prim.height), drawn)

    out: List[ActivityRegion] = []
    seen: set = set()
    n = 0
    for leaf, r in kept:
        if r is None:
            # A region that flows across a column break is several obstacles and one activity.
            if id(leaf.taken.ref) not in seen:
                seen.add(id(leaf.taken.ref))
                out.append(leaf.taken.ref)
            continue
        rect = (round(max(r[0], 0.0), 4), round(max(r[1], 0.0), 4),
                round(min(r[2], prim.width), 4), round(min(r[3], prim.height), 4))
        others = [tuple(o) for o in rects if o is not r] + taken
        # What the parting could not settle is left out: no hotspot is better than one lying on another.
        if (rect[2] - rect[0] < MIN_HOTSPOT or rect[3] - rect[1] < MIN_HOTSPOT
                or any(rect_overlap(rect, o) > 0.5 for o in others)):
            r[2], r[3] = r[0], r[1]                       # an empty box is in nobody's way
            continue
        n += 1
        out.append(ActivityRegion(
            id=f"p{page_num}-c{n}", label=None, column=_column_of(rect, layout), rect=rect, parts=[rect],
            headline=_headline(leaf) or None, items=None, anchored=False, kind="content",
        ))
    # An activity the cut lost sight of (it cannot, but the bake must never drop one) keeps its place.
    for a in activities:
        if id(a) not in seen:
            out.append(a)
    return out


# -- pages that are not content --------------------------------------------

_CHAPTER = r"(ünite|ünі̇te|üni̇te|bölüm|tema|kısım|unit|theme|chapter|module|modül)"
_NUMERAL = re.compile(rf"^\s*\d{{1,2}}\s*\.?\s*{_CHAPTER}?\s*$|^\s*{_CHAPTER}\s*\d{{1,2}}\s*$", re.IGNORECASE)
_FIRST = re.compile(rf"^\s*(1\s*\.(?!\d)|{_CHAPTER}\s*1\b)", re.IGNORECASE)
_PAGE_NO = re.compile(r"(\d{1,3})\s*$")


def is_opener(prim: PagePrimitives, layout: PageLayout) -> bool:
    """
    Whether a sheet opens a chapter: it carries the chapter's number as a poster does.

    The opening page of a theme or unit sets its number ("1.", "2. ÜNİTE") in
    type several times the body's. Large type alone is not enough (a newspaper
    masthead in a history book, a feature heading in an English one), and
    neither is a photograph for ground: both are ordinary on content pages.
    """
    display = max(DISPLAY_MIN, DISPLAY_RATIO * layout.body_font_size)
    return any(s.size >= display and _NUMERAL.match(s.text) for s in prim.spans)


def _rows(prim: PagePrimitives) -> List[str]:
    """The sheet's type as rows of text, top first, each read left to right."""
    rows: List[List[Any]] = []
    for s in sorted((s for s in prim.spans if s.text.strip()), key=lambda s: -(s.bbox[1] + s.bbox[3]) / 2.0):
        mid = (s.bbox[1] + s.bbox[3]) / 2.0
        first = rows[-1][0].bbox if rows else None
        if first and abs((first[1] + first[3]) / 2.0 - mid) < 0.6 * min(first[3] - first[1], s.bbox[3] - s.bbox[1]):
            rows[-1].append(s)
        else:
            rows.append([s])
    return ["  ".join(s.text.strip() for s in sorted(row, key=lambda s: s.bbox[0])) for row in rows]


def contents_page(prim: PagePrimitives) -> Tuple[Optional[str], Optional[int]]:
    """
    Whether a sheet is a table of contents, and the printed page it gives for the first chapter.

    A contents page says it is one ("named"), or most of its rows end in a page
    number ("listed"). The second is weak evidence on its own -- a page of
    numeric tables reads the same -- so it counts only for a sheet that follows
    a named one (`contents_run`). The row that opens with the first chapter
    ("1. ÜNİTE ...", "Theme 1 ...") and ends in a number says where the book
    proper starts.
    """
    rows = _rows(prim)
    hits = sum(1 for t in rows if _TOC_LINE.search(t))
    named = any(_CONTENTS.match(s.text) for s in prim.spans)
    if not named and not (hits >= 8 and hits >= 0.4 * len(rows)):
        return None, None
    kind = "named" if named else "listed"
    for i, t in enumerate(rows):
        if not _FIRST.match(t):
            continue
        number = _PAGE_NO.search(t)
        if number and number.start() > 2:
            return kind, int(number.group(1))
        # The number is set a little off the title's line, and reads as the row after it.
        if i + 1 < len(rows) and re.fullmatch(r"\s*\d{1,3}\s*", rows[i + 1]):
            return kind, int(rows[i + 1])
    return kind, None


def contents_run(named: Sequence[int], listed: Sequence[int]) -> List[int]:
    """The sheets of the table of contents: the ones that say so, and the listed sheets that follow them."""
    run = sorted(named)
    for p in sorted(listed):
        if run and 0 < p - max(run) <= 2:
            run.append(p)
    return run


def _fold(text: str) -> str:
    """Lower case, with the Turkish capitals folded to what a pattern written in lower case expects."""
    return text.replace("İ", "i").replace("I", "ı").lower()


_FORM = re.compile(
    # "değerlemdirme" is how one book prints it.
    r"performans\s+görevi|değerle[nm]dirme\s+formu"
    r"|değerle[nm]dirme\s+ölçütleri|dereceli\s+puanlama\s+anahtarı|kontrol\s+listesi"
    r"|self[- ]assessment|peer[- ]assessment|performance\s+(task|assignment)|\brubric\b")
_BIBLIOGRAPHY = re.compile(r"^\W*(görsel\s+)?(kaynakça|kaynaklar|bibliyografya|references|bibliography)\W*$")
# An entry of a bibliography: "Surname, A. (2002).", or the address a picture was taken from.
_ENTRY = re.compile(r"^\W*[^\W\d][\w’'\-]+,\s*[^\W\d]\.|https?://|www\.|\berişim\b")
FORM_TOP = 0.25           # share of the sheet, from its top, a form's title must stand in
FORM_WORDS = 8            # words past which a row is a sentence about a form, not its title
BACK_REACH = 12           # sheets from the end of the book within which its bibliography must end
ENTRY_SHARE = 0.3         # share of a sheet's rows that are bibliography entries past which it is one
ENTRY_ROWS = 8
PLATE_PROSE = 6           # rows of running text past which a sheet is a page of a lesson, not a plate
PLATE_ROW = 40            # letters a row must carry to be running text
PLATE_GROUND = 0.45       # share of the sheet a picture must cover to be the sheet's plate
PLATE_LABELS = 60         # short rows past which a sheet is a drawn map's place names


def _row_spans(prim: PagePrimitives) -> List[List[Any]]:
    """The sheet's spans grouped into rows, top first, each left to right."""
    rows: List[List[Any]] = []
    for s in sorted((s for s in prim.spans if s.text.strip()), key=lambda s: -(s.bbox[1] + s.bbox[3]) / 2.0):
        mid = (s.bbox[1] + s.bbox[3]) / 2.0
        first = rows[-1][0].bbox if rows else None
        if first and abs((first[1] + first[3]) / 2.0 - mid) < 0.6 * min(first[3] - first[1], s.bbox[3] - s.bbox[1]):
            rows[-1].append(s)
        else:
            rows.append([s])
    return [sorted(row, key=lambda s: s.bbox[0]) for row in rows]


def page_form(prim: PagePrimitives, geom: PageGeometry) -> Optional[Tuple[Rect, str]]:
    """
    The box and title of a form that fills this sheet, or None.

    A performance task, a self- or peer-assessment form and a rubric are set
    as one sheet: a title at the top, then instructions, a table and room to
    write. Cut into its paragraphs and table rows it is a handful of fragments;
    a teacher opens the form. The sheet is one when a row at its top names it
    as such and is set as a title (a few words, capitals or bold, not a
    sentence that mentions a form), and no second such title stands on the
    sheet -- two forms printed on one page to be cut apart are two things, and
    are left to the boxes drawn for them.
    """
    low, high = FOOTER_BAND, prim.height - HEADER_BAND
    titles = []
    for row in _row_spans(prim):
        top = max(s.bbox[3] for s in row)
        if top < low:
            continue
        text = re.sub(r"\s+", " ", " ".join(s.text for s in row)).strip()
        set_as_title = text.upper() == text or all(_is_bold(s) for s in row)
        if (_FORM.search(_fold(text)) and len(text.split()) <= FORM_WORDS and text[:1].isupper()
                and not text.endswith(".") and set_as_title):
            titles.append((top, text, _bbox([s.bbox for s in row])))
    if len(titles) != 1 or titles[0][0] < prim.height * (1.0 - FORM_TOP):
        return None
    # On a short sheet the title stands in what is elsewhere the running head's band; it is the
    # form's all the same.
    high = max(high, titles[0][0])
    held = [_line_rect(ln) for ln in geom.lines if any(s.text.strip() for s in ln["spans"])]
    held += [tuple(b["rect"]) for b in geom.blocks] + [tuple(titles[0][2])]
    held = [(max(r[0], 0.0), max(r[1], low), min(r[2], prim.width), min(r[3], high)) for r in held]
    held = [r for r in held if r[2] > r[0] and r[3] > r[1]]
    if not held:
        return None
    box = _bbox(held)
    rect = (round(max(box[0] - PAD, 0.0), 4), round(max(box[1] - PAD, 0.0), 4),
            round(min(box[2] + PAD, prim.width), 4), round(min(box[3] + PAD, prim.height), 4))
    return rect, titles[0][1]


def is_bibliography(prim: PagePrimitives) -> bool:
    """
    Whether a sheet is the book's bibliography: it is headed so, or it is a list of entries.

    A bibliography runs over several sheets and only the first is headed, so
    the rows are read too: "Surname, A. (2002). Title", or the address a
    picture was taken from.
    """
    rows = [re.sub(r"\s+", " ", t).strip() for t in _rows(prim)]
    if any(_BIBLIOGRAPHY.match(_fold(t)) for t in rows):
        return True
    entries = sum(1 for t in rows if _ENTRY.search(_fold(t)))
    return entries >= ENTRY_ROWS and entries >= ENTRY_SHARE * len(rows)


def is_plate(prim: PagePrimitives) -> bool:
    """
    Whether a sheet is a plate: one picture or drawing for the whole page, and no lesson round it.

    A map plate at the back of a book, the back cover, a blank sheet. It
    carries next to no running text, and is one picture or a drawing lettered
    with names. Only where a book *ends* in plates are they furniture
    (`back_matter_start`); a full-page map inside a lesson is that lesson's.
    """
    rows = [re.sub(r"\s+", " ", t).strip() for t in _rows(prim)]
    if sum(1 for t in rows if len(t) >= PLATE_ROW) > PLATE_PROSE:
        return False
    sheet = max(1.0, prim.width * prim.height)
    page = (0.0, 0.0, prim.width, prim.height)
    ground = max((rect_overlap(tuple(im.bbox), page) for im in prim.images), default=0.0)
    return not rows or ground >= PLATE_GROUND * sheet or len(rows) >= PLATE_LABELS


def back_matter_start(bibliography: Sequence[int], plates: Sequence[int], page_count: int) -> int:
    """
    The first sheet of the back matter (`page_count + 1` when the book has none that can be told).

    A book ends in its bibliography and, after it, map plates and the back
    cover. The run of plates the book ends in is back matter; so is the last
    run of bibliography sheets, when it ends near the end of the book, with
    everything after it. A sheet inside a lesson that is headed "Kaynakça"
    (how to write one) is far from the end and stays content.
    """
    plate, bib = set(plates), set(bibliography)
    start = page_count + 1
    while start - 1 >= 1 and (start - 1 in plate or start - 1 in bib):
        start -= 1
    # A book that is plates from end to end is a scan with no text layer, not a book of maps.
    if page_count + 1 - start > max(BACK_REACH, int(0.1 * page_count)):
        start = page_count + 1
    if bib:
        last = max(bib)
        first = last
        while first - 1 in bib:
            first -= 1
        if last >= min(start - 1, page_count) - BACK_REACH:
            start = min(start, first)
    return start


def front_matter_end(
    openers: Sequence[int],
    contents: Sequence[int],
    first_chapter: Optional[int],
    page_count: int,
    first_activity: Optional[int] = None,
) -> int:
    """
    The last sheet of the front matter (0 when the book has none that can be told).

    Everything before the book proper is cover, imprint, anthem, contents and
    "how to use this book". The book proper starts at its first chapter
    opener; in a book whose openers cannot be read (their numbers are drawn,
    not set), at the sheet its contents page gives for the first chapter
    (`first_chapter`, already a sheet number); failing both, after its last
    contents page. Wherever those put it, it ends before the first sheet with
    an activity on it (`first_activity`): a starter unit set before "Theme 1"
    is the book, not its front matter.
    """
    limit = max(FRONT_PAGES, int(FRONT_LIMIT * page_count))
    toc = [p for p in contents if p <= limit]
    after = max(toc) if toc else 2
    starts = [p for p in openers if after < p <= limit][:1]
    if first_chapter is not None and (max(toc) if toc else 0) < first_chapter <= limit:
        starts.append(first_chapter)
    end = min(starts) - 1 if starts else (max(toc) if toc else 0)
    if first_activity is not None:
        end = min(end, first_activity - 1)
    return max(end, 0)


def furniture(pages: Dict[int, Sequence[ActivityRegion]], page_count: int) -> Dict[int, List[ActivityRegion]]:
    """
    The content regions that are the book's furniture, by sheet: a running head, a corner ornament.

    Nothing on one sheet tells a running head from a line of text set high on
    the page. The book does: furniture stands in the same place on sheet after
    sheet. A content region whose box recurs on many sheets of the same hand
    (left or right) is furniture, on every sheet it recurs on. "Many" grows
    with the book, so that a heading banner that opens a few pages of a long
    book at the same height is not mistaken for one.
    """
    repeats = max(4, int(0.03 * page_count))
    seen: Dict[Tuple[int, ...], List[Tuple[int, ActivityRegion]]] = {}
    for p, acts in pages.items():
        for a in acts:
            if a.kind == "content":
                key = (p % 2,) + tuple(int(round(v / 3.0)) for v in a.rect)
                seen.setdefault(key, []).append((p, a))
    out: Dict[int, List[ActivityRegion]] = {}
    for hits in seen.values():
        if len({p for p, _ in hits}) >= repeats:
            for p, a in hits:
                out.setdefault(p, []).append(a)
    return out
