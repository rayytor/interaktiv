"""
Tests for the decision trace (`scanner/trace.py`) and the instrumentation it feeds.

The trace exists to answer "why did this sheet produce nothing?", and the value
of that answer rests on three properties, each asserted here:

1. **Recording is genuinely optional and genuinely free when off.** The shipped
   bake runs untraced; if an untraced run still built `Drop` objects, every book
   in the catalogue would pay for a diagnostic nobody asked for.
2. **Tracing does not change what is detected.** A sidecar that alters the bake
   it describes is not a diagnostic. The same page traced and untraced must
   yield the same regions.
3. **An empty sheet is attributed to the rule that actually emptied it**, not to
   a counter, and not to a symptom rule that fires whenever anything else has
   already failed. Getting this wrong makes the work queue point at the wrong
   bucket, which is the one job the trace has.
"""

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from tools.hotspot_extraction.scanner.layout import Column, PageLayout
from tools.hotspot_extraction.scanner.primitives import PagePrimitives, TextSpan
from tools.hotspot_extraction.scanner.prompts import detect_activity_markers
from tools.hotspot_extraction.scanner.regions import grow_activity_regions
from tools.hotspot_extraction.scanner.serializer import serialize_activity
from tools.hotspot_extraction.scanner.trace import (
    SYMPTOM_RULES,
    TRACE_VERSION,
    Drop,
    GrowthTrace,
    attribute_empty_page,
    load_trace_gz,
    save_trace_gz,
    serialize_page_trace,
    serialize_trace,
)

PAGE_W, PAGE_H = 595.0, 842.0


def span(text, x0, y0, x1, y1, size=10.0, font="MyriadPro-Regular", flags=0):
    return TextSpan(text=text, bbox=(x0, y0, x1, y1), font=font, size=size,
                    flags=flags, color=0)


def lettered_page():
    """A sheet that reads as two lettered questions in one column."""
    spans = [
        span("a.", 60.0, 700.0, 72.0, 712.0),
        span("Asagidaki sorulari cevaplayiniz.", 76.0, 700.0, 320.0, 712.0),
        span("b.", 60.0, 640.0, 72.0, 652.0),
        span("Bosluklari doldurunuz.", 76.0, 640.0, 300.0, 652.0),
    ]
    prim = PagePrimitives(page_num=7, width=PAGE_W, height=PAGE_H,
                          spans=spans, drawings=[], images=[])
    layout = PageLayout(
        columns=[Column(index=0, x0=50.0, x1=545.0, opens_at=802.0, closes_at=44.0)],
        content_box=(50.0, 44.0, 545.0, 802.0),
        body_font_size=10.0,
    )
    return prim, layout


def prose_page():
    """A sheet of ordinary prose: nothing here is a question."""
    spans = [
        span("Bu bolumde hucre zarinin yapisi ele alinmaktadir ve gecirgenlik", 60.0, 700.0, 500.0, 712.0),
        span("ozellikleri ayrintili bicimde incelenmektedir.", 60.0, 686.0, 430.0, 698.0),
    ]
    prim = PagePrimitives(page_num=11, width=PAGE_W, height=PAGE_H,
                          spans=spans, drawings=[], images=[])
    layout = PageLayout(
        columns=[Column(index=0, x0=50.0, x1=545.0, opens_at=802.0, closes_at=44.0)],
        content_box=(50.0, 44.0, 545.0, 802.0),
        body_font_size=10.0,
    )
    return prim, layout


class TestRecordingIsOptional:
    def test_untraced_records_nothing(self):
        trace = GrowthTrace()                      # record defaults to False
        trace.drop("marker", "typography", rect=(0, 0, 1, 1), text="x")
        trace.count("marker.kept", 3)
        assert trace.drops == []
        assert trace.counts == {}

    def test_traced_records_rule_and_detail(self):
        trace = GrowthTrace(record=True)
        trace.drop("marker", "typography", rect=(1.0, 2.0, 3.0, 4.0),
                   text="  3.  ", size=7.25123, font="X")
        assert trace.counts == {"marker.typography": 1}
        d = trace.drops[0]
        assert (d.stage, d.rule) == ("marker", "typography")
        assert d.text == "3."                      # stripped
        assert d.detail["size"] == 7.251           # floats rounded for the sidecar
        assert d.rect == [1.0, 2.0, 3.0, 4.0]

    def test_the_pipeline_leaves_no_record_when_untraced(self):
        prim, layout = prose_page()
        trace = GrowthTrace()
        markers = detect_activity_markers(prim, layout=layout, trace=trace)
        grow_activity_regions(prim, layout, markers, trace=trace)
        assert trace.drops == []
        assert trace.counts == {}


class TestTracingChangesNothing:
    @pytest.mark.parametrize("page", ["lettered", "prose"])
    def test_same_regions_traced_and_untraced(self, page):
        make = lettered_page if page == "lettered" else prose_page

        prim, layout = make()
        off = GrowthTrace()
        plain = [serialize_activity(a) for a in grow_activity_regions(
            prim, layout, detect_activity_markers(prim, layout=layout, trace=off), trace=off)]

        prim, layout = make()
        on = GrowthTrace(record=True)
        traced = [serialize_activity(a) for a in grow_activity_regions(
            prim, layout, detect_activity_markers(prim, layout=layout, trace=on), trace=on)]

        assert plain == traced

    def test_a_page_with_no_marker_says_so(self):
        prim, layout = prose_page()
        trace = GrowthTrace(record=True)
        markers = detect_activity_markers(prim, layout=layout, trace=trace)
        acts = grow_activity_regions(prim, layout, markers, trace=trace)
        assert acts == []
        assert trace.counts.get("growth.no-marker") == 1


class TestAttribution:
    def test_a_counter_is_never_named_as_the_cause(self):
        # `prompt.paragraphs` counts how much type the sheet holds. It is by far
        # the largest number on most sheets and it refuses nothing, so naming it
        # would point the work queue at a tally rather than at a rule.
        page = {
            "regions": [],
            "counts": {"prompt.paragraphs": 40, "marker.typography": 2},
            "drops": [
                {"stage": "marker", "rule": "typography", "rect": None, "text": "", "detail": {}},
                {"stage": "marker", "rule": "typography", "rect": None, "text": "", "detail": {}},
            ],
        }
        assert attribute_empty_page(page) == "marker.typography"

    def test_a_symptom_yields_to_the_rule_upstream_of_it(self):
        page = {
            "regions": [],
            "counts": {},
            "drops": [
                {"stage": "growth", "rule": "no-marker", "rect": None, "text": "", "detail": {}},
                {"stage": "growth", "rule": "no-marker", "rect": None, "text": "", "detail": {}},
                {"stage": "marker", "rule": "no-hanging-indent", "rect": None, "text": "", "detail": {}},
            ],
        }
        assert "growth.no-marker" in SYMPTOM_RULES
        assert attribute_empty_page(page) == "marker.no-hanging-indent"

    def test_a_symptom_stands_when_it_is_all_there_is(self):
        page = {"regions": [], "counts": {}, "drops": [
            {"stage": "growth", "rule": "no-marker", "rect": None, "text": "", "detail": {}},
        ]}
        assert attribute_empty_page(page) == "growth.no-marker"

    def test_a_sheet_that_considered_nothing_is_named_as_such(self):
        assert attribute_empty_page({"regions": [], "counts": {}, "drops": []}) \
            == "none.nothing-considered"

    def test_ties_are_broken_the_same_way_every_time(self):
        drops = [
            {"stage": "marker", "rule": "typography", "rect": None, "text": "", "detail": {}},
            {"stage": "marker", "rule": "out-of-run", "rect": None, "text": "", "detail": {}},
        ]
        first = attribute_empty_page({"regions": [], "counts": {}, "drops": drops})
        second = attribute_empty_page({"regions": [], "counts": {}, "drops": list(reversed(drops))})
        assert first == second


class TestSidecar:
    def test_round_trip_and_histogram(self, tmp_path):
        full = GrowthTrace(record=True)
        full.count("marker.kept", 2)
        empty = GrowthTrace(record=True)
        empty.drop("marker", "no-hanging-indent", rect=(1, 2, 3, 4), text="3.")

        pages = [
            serialize_page_trace(1, full, region_ids=["p1-a", "p1-b"], printed_page=9),
            serialize_page_trace(2, empty, region_ids=[], printed_page=10),
        ]
        data = serialize_trace("bk", "/books/bk.pdf", "123-abc", pages)

        assert data["traceVersion"] == TRACE_VERSION
        assert data["pageCount"] == 2
        assert data["emptyPages"] == 1
        assert data["emptyPagesByRule"] == {"marker.no-hanging-indent": 1}
        assert data["counts"]["marker.kept"] == 2

        path = os.path.join(tmp_path, "nested", "trace.json.gz")
        save_trace_gz(path, data)
        assert load_trace_gz(path) == data

    def test_a_page_with_regions_is_not_counted_as_empty(self):
        t = GrowthTrace(record=True)
        t.drop("growth", "sliver", rect=(0, 0, 1, 1))
        data = serialize_trace("bk", "bk.pdf", "fp", [
            serialize_page_trace(1, t, region_ids=["p1-a"]),
        ])
        assert data["emptyPages"] == 0
        assert data["emptyPagesByRule"] == {}
        # The refusal is still recorded -- a sheet can lose one activity and keep another.
        assert data["counts"]["growth.sliver"] == 1
