"""
One sheet, from primitives to regions -- written once, run from three places.

`scan.py` runs it over a PDF to bake a book, `train/cache.py` replays it over a
cached parse, and `train/objective.py` scores what it returns. Those three used
to be three copies of the same sequence, and the copies had already begun to
differ: the replay stopped after growth and never built the panel and solution
geometry, so a fitted profile would have been judged on regions the bake writes
against blocks the bake never saw.

That matters more here than duplication usually does. The fitting loop's whole
premise is that replaying a cached page is the same as baking the PDF; if the
two sequences drift, the loop optimises a detector that does not ship.

Nothing here decides anything. Every judgement still lives in `layout`,
`markers`, `prompts`, `figures`, `regions` and `anchors` -- this is the order
they run in, and the one place it is written down.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .anchors import PublisherOge, find_unplaced_anchors, reconcile_anchors
from .layout import PageLayout, detect_layout
from .primitives import PagePrimitives
from .prompts import detect_activity_markers
from .regions import (
    ActivityRegion,
    GrowthTrace,
    detect_panels,
    detect_solution_spaces,
    grow_activity_regions,
)


@dataclass
class PageResult:
    """
    Everything one sheet yields: the regions, and the geometry they are judged against.

    The diagnostic rects are not a debugging aid -- they are the drawn blocks
    the violation rules are stated over, so a page scored without them scores as
    clean. They are built here, beside the regions, for that reason.
    """
    layout: PageLayout
    markers: List[Any]
    activities: List[ActivityRegion]
    panels: List[Tuple[float, float, float, float]]
    solutions: List[Tuple[float, float, float, float]]
    anchor_ids: List[str] = field(default_factory=list)

    def diagnostics(self, width: float, height: float, *, as_lists: bool = False) -> Dict[str, Any]:
        """The sidecar entry for this sheet, in the shape `serialize_diagnostics` takes."""
        conv = list if as_lists else tuple
        return {
            "pageWidth": width,
            "pageHeight": height,
            "contentTop": self.layout.content_box[3],
            "contentBottom": self.layout.content_box[1],
            "panels": [conv(r) for r in self.panels],
            "solutions": [conv(r) for r in self.solutions],
            "markers": [conv(m.span.bbox) for m in self.markers],
        }


def body_spans_of(prim: PagePrimitives, layout: PageLayout) -> List[Any]:
    """
    The spans inside the content box, which is what the block detectors read.

    The two point tolerance is the original's: a line whose baseline sits a
    hair outside the decided content box is still body text, and dropping it
    would take its panel with it.
    """
    return [
        s for s in prim.spans
        if s.bbox[1] >= layout.content_box[1] - 2.0 and s.bbox[3] <= layout.content_box[3] + 2.0
    ]


def detect_page(
    prim: PagePrimitives,
    *,
    page_num: int,
    printed_page: Optional[int] = None,
    page_oges: Sequence[PublisherOge] = (),
    trace: Optional[GrowthTrace] = None,
    reconcile: bool = True,
) -> PageResult:
    """
    Run the detector over one already-parsed sheet.

    `page_oges` are the publisher's entries for this sheet only; passing the
    whole book's would silently anchor regions to icons printed elsewhere.
    `reconcile=False` runs the detector without the publisher's help at all,
    which is how the anchored and unanchored halves of a result are told apart.
    """
    if trace is None:
        trace = GrowthTrace()

    layout = detect_layout(prim)
    markers = detect_activity_markers(prim, layout=layout, trace=trace)
    activities = grow_activity_regions(prim, layout, markers, trace=trace)

    anchor_ids: List[str] = []
    if reconcile and page_oges and printed_page is not None:
        unplaced = find_unplaced_anchors(
            activities=activities,
            oges=page_oges,
            page_height=prim.height,
            printed_page=printed_page,
            page_width=prim.width,
        )
        if unplaced:
            anchor_ids = [o.id for o in unplaced]
            activities = reconcile_anchors(
                activities=activities,
                oges=page_oges,
                page_height=prim.height,
                printed_page=printed_page,
                page_width=prim.width,
                page_num=page_num,
                primitives=prim,
                layout=layout,
                markers=markers,
                trace=trace,
            )

    body = body_spans_of(prim, layout)
    panels = detect_panels(prim.drawings, body, page_h=prim.height, markers=markers)
    raw_solutions = detect_solution_spaces(prim.drawings, body, page_h=prim.height)
    solutions = [
        tuple(s["rect"]) if isinstance(s, dict) else tuple(s)
        for s in raw_solutions
    ]

    return PageResult(
        layout=layout,
        markers=markers,
        activities=activities,
        panels=[tuple(p) for p in panels],
        solutions=solutions,
        anchor_ids=anchor_ids,
    )
