"""
Turning a detected region into numbers, for the learned scorer of Stage 4.4.

Every feature here is read off primitives the detector has already extracted --
glyph boxes, their sizes and fonts, drawn rectangles and strokes, and the
region's own place on the sheet. Page pixels are deliberately absent: these are
digital InDesign exports, so rendering a page and reading pixels back would be
strictly lossy about the very things the detector cares about (where a rule is,
how wide a glyph is) and slower by two orders of magnitude.

The features are written as ratios wherever a ratio makes sense -- share of the
page, share of the region, multiples of the body type size -- because the corpus
mixes A4 and other trims, and an absolute point measurement would make the model
learn the page size of the books it was trained on.

Nothing here is a threshold. A feature that already encodes a decision ("is this
taller than TALL_REGION") would hand the model the rule instead of the evidence,
and it would then be unable to disagree with the detector about anything.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

Rect = Tuple[float, float, float, float]

# The order is the contract: a model is a list of weights, and a reordering
# here would silently repoint every one of them at a different feature.
FEATURE_NAMES: Tuple[str, ...] = (
    "bias",
    "width_share",          # of the page width
    "height_share",         # of the page height
    "area_share",
    "aspect",               # width / height, squashed
    "x_centre",             # 0 at the left edge, 1 at the right
    "y_centre",             # 0 at the foot, 1 at the head
    "left_margin_share",
    "parts",                # how many pieces the region is in
    "has_label",
    "label_is_letter",
    "label_is_number",
    "items",                # sub-questions found inside
    "span_count",           # glyph runs inside, log-squashed
    "char_count",
    "text_density",         # glyph area over region area
    "mean_size_ratio",      # mean type size over the page's body size
    "max_size_ratio",
    "panels_inside",
    "panels_cut",
    "solutions_inside",
    "solutions_cut",
    "drawn_share",          # share of the region covered by drawn blocks
    "gap_above",            # to the region above, in body-size multiples
    "gap_below",
    "regions_on_page",
    "is_anchored",
    "column_index",
)

N_FEATURES = len(FEATURE_NAMES)


def _squash(x: float, scale: float) -> float:
    """Map an unbounded count or ratio into [0, 1) so one outlier cannot dominate."""
    return x / (x + scale) if x >= 0 else 0.0


def _area(r: Sequence[float]) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _overlap(a: Sequence[float], b: Sequence[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _cuts(region: Sequence[float], block: Sequence[float], tol: float) -> bool:
    ba = _area(block)
    if ba <= 0:
        return False
    share = _overlap(region, block) / ba
    return tol < share < 1.0 - tol


@dataclass
class PageContext:
    """What a region needs to know about the sheet it is on."""
    width: float
    height: float
    body_size: float
    spans: List[Any]
    panels: Sequence[Rect]
    solutions: Sequence[Rect]
    region_rects: Sequence[Rect]


def page_context(prim, layout, panels, solutions, regions) -> PageContext:
    body = getattr(layout, "body_font_size", 0.0) or 0.0
    if body <= 0:
        sizes = sorted(s.size for s in prim.spans if getattr(s, "size", 0))
        body = sizes[len(sizes) // 2] if sizes else 10.0
    return PageContext(
        width=prim.width or 1.0,
        height=prim.height or 1.0,
        body_size=body,
        spans=list(prim.spans),
        panels=list(panels),
        solutions=list(solutions),
        region_rects=[tuple(a["rect"]) for a in regions],
    )


def features_for(
    activity: Dict[str, Any],
    ctx: PageContext,
    *,
    whole_tol: float = 0.05,
) -> List[float]:
    """
    One region's feature vector, in `FEATURE_NAMES` order.

    `whole_tol` is passed rather than imported so that the features describe the
    same notion of "cuts" the scorer uses, without this module reaching into the
    detector's live constants -- which a fitted profile moves, and which would
    then make a trained model's inputs mean something different at bake time
    than they did at training time.
    """
    rect: Rect = tuple(activity["rect"])  # type: ignore[assignment]
    w = max(0.0, rect[2] - rect[0])
    h = max(0.0, rect[3] - rect[1])
    area = w * h
    body = ctx.body_size or 10.0

    inside_spans = [s for s in ctx.spans if _overlap(rect, s.bbox) > 0.5 * _area(s.bbox)]
    chars = sum(len(s.text) for s in inside_spans)
    glyph_area = sum(_area(s.bbox) for s in inside_spans)
    sizes = [getattr(s, "size", 0.0) for s in inside_spans if getattr(s, "size", 0.0)]

    panels_inside = sum(1 for p in ctx.panels if _overlap(rect, p) >= (1 - whole_tol) * _area(p))
    panels_cut = sum(1 for p in ctx.panels if _cuts(rect, p, whole_tol))
    sols_inside = sum(1 for b in ctx.solutions if _overlap(rect, b) >= (1 - whole_tol) * _area(b))
    sols_cut = sum(1 for b in ctx.solutions if _cuts(rect, b, whole_tol))
    drawn = sum(_overlap(rect, b) for b in list(ctx.panels) + list(ctx.solutions))

    above = [r for r in ctx.region_rects if r[1] >= rect[3] - 1.0]
    below = [r for r in ctx.region_rects if r[3] <= rect[1] + 1.0]
    gap_above = min((r[1] - rect[3] for r in above), default=ctx.height)
    gap_below = min((rect[1] - r[3] for r in below), default=ctx.height)

    label = activity.get("label")
    label_str = str(label) if label is not None else ""

    return [
        1.0,
        w / ctx.width,
        h / ctx.height,
        area / (ctx.width * ctx.height),
        _squash(w / h if h > 0 else 0.0, 2.0),
        ((rect[0] + rect[2]) / 2) / ctx.width,
        ((rect[1] + rect[3]) / 2) / ctx.height,
        rect[0] / ctx.width,
        _squash(float(len(activity.get("parts") or [])), 2.0),
        1.0 if label_str else 0.0,
        1.0 if label_str[:1].isalpha() else 0.0,
        1.0 if label_str[:1].isdigit() else 0.0,
        _squash(float(len(activity.get("items") or [])), 4.0),
        _squash(float(len(inside_spans)), 20.0),
        _squash(float(chars), 200.0),
        min(1.0, glyph_area / area) if area > 0 else 0.0,
        min(3.0, (sum(sizes) / len(sizes) / body)) / 3.0 if sizes and body else 0.0,
        min(3.0, (max(sizes) / body)) / 3.0 if sizes and body else 0.0,
        _squash(float(panels_inside), 2.0),
        _squash(float(panels_cut), 2.0),
        _squash(float(sols_inside), 4.0),
        _squash(float(sols_cut), 4.0),
        min(1.0, drawn / area) if area > 0 else 0.0,
        _squash(max(0.0, gap_above) / body, 4.0) if body else 0.0,
        _squash(max(0.0, gap_below) / body, 4.0) if body else 0.0,
        _squash(float(len(ctx.region_rects)), 6.0),
        1.0 if activity.get("anchored") else 0.0,
        _squash(float(activity.get("column") or 0), 2.0),
    ]
