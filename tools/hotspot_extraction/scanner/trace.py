"""
What the detector decided, and what it refused, on one sheet.

The bake records the hotspots that survived. It does not record the ones that
did not, and that is the larger half of the story: 44.3% of sheets in the
catalogue produce no region at all, and a bake file cannot say whether that is
because the sheet asks nothing, because no span parsed as a label, because the
run failed monotonicity, or because a grown rect came out thinner than
`MIN_HOTSPOT` and was dropped on the floor. Those four have nothing in common
and want four different fixes.

So every point in detection that says `continue` or `return []` is asked to
leave a record behind. Two things are then possible that were not:

  * **A ranked work queue.** Group the empty sheets by the rule that emptied
    them and the biggest bucket is the next thing to fix -- the same reasoning
    the scorecard's `LINK_REASONS` histogram already applies to the publisher's
    manifest, but reaching the sheets the manifest never mentions.
  * **Supervision that costs nothing.** A refusal is a label: this candidate was
    considered and rejected under this rule. Cross-book agreement between the
    rules, and disagreement between a rule and the publisher's own anchor, are
    both derived from the corpus rather than drawn by hand -- which is the
    standing condition on any learned component here.

The trace is written to its own `trace.json.gz` sidecar and never to
`regions.json` or `diagnostics.json.gz`. The reader loads those two on every
book open; it must not pay for a diagnostic it never reads, and the shipped
bake must not grow. Tracing is off unless `scan.py --trace` asks for it.

Nothing in this module imports the rest of the package, so every stage --
markers, prompts, growth, anchors -- can record into the same object without a
circular import.
"""

from dataclasses import dataclass, field, asdict
import gzip
import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

TRACE_VERSION = 1

# How much of a span's text is worth keeping. A drop record is read to tell one
# kind of refusal from another, which the opening words settle; keeping the
# whole paragraph would make the sidecar large without making it clearer.
TEXT_CLIP = 60


@dataclass
class Drop:
    """
    One thing detection considered and refused, and the rule that refused it.

    `stage` names where in the pipeline it happened, `rule` names the specific
    test that failed, and both are meant to be grouped by. `detail` carries
    whatever numbers make that particular refusal legible -- the measured gap
    against the threshold it missed, say -- and is free-form on purpose: it is
    read by a person triaging a bucket, not by the detector.
    """
    stage: str
    rule: str
    # A list, not a tuple: the sidecar is JSON, and a tuple that survives in
    # memory but comes back as a list would make a round-trip comparison -- the
    # test that the sidecar is lossless -- fail for no real reason.
    rect: Optional[List[float]] = None
    text: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GrowthTrace:
    """
    How the detector read one sheet, for callers that ask afterwards.

    A band is the vertical scope an activity was read in, and it is a stabler
    thing to ask "does this point belong to that activity" of than the grown
    rect: the rect stops where the text and the drawn matter stop, while the
    band runs to the next marker. Anchor reconciliation binds against the bands
    for exactly that reason, and separates what it synthesises against the same
    geometry growth used, so both passes cut the sheet the same way.

    `drops` and the counters are the Stage 3 addition. They are only filled
    when the caller asks for them (`record=True`): every stage checks
    `trace.record` before building a `Drop`, so an untraced bake pays for one
    attribute lookup per refusal and allocates nothing. `bands` and `geometry`
    are filled either way, because reconciliation needs them.
    """
    bands: Dict[str, List[dict]] = field(default_factory=dict)
    geometry: Optional[Any] = None          # PageGeometry; untyped to stay import-free

    record: bool = False
    drops: List[Drop] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)

    def drop(
        self,
        stage: str,
        rule: str,
        rect: Optional[Sequence[float]] = None,
        text: str = "",
        **detail: Any,
    ) -> None:
        """
        Record a refusal, if anyone asked for one.

        Called from inside the detector's hot loops, so the disabled path has to
        be cheap: the `record` test comes first and nothing is built before it.
        """
        if not self.record:
            return
        self.counts[f"{stage}.{rule}"] = self.counts.get(f"{stage}.{rule}", 0) + 1
        self.drops.append(Drop(
            stage=stage,
            rule=rule,
            rect=[round(float(v), 2) for v in rect] if rect else None,
            text=(text or "").strip()[:TEXT_CLIP],
            detail={k: (round(v, 3) if isinstance(v, float) else v)
                    for k, v in detail.items()},
        ))

    def count(self, name: str, n: int = 1) -> None:
        """Tally something that is not a refusal -- candidates seen, markers kept."""
        if not self.record:
            return
        self.counts[name] = self.counts.get(name, 0) + n


def serialize_page_trace(
    page_num: int,
    trace: GrowthTrace,
    region_ids: Sequence[str],
    printed_page: Optional[int] = None,
    anchors: Sequence[str] = (),
) -> Dict[str, Any]:
    """
    One sheet's trace, as the shape that goes into the sidecar.

    The kept region ids are carried beside the refusals deliberately: a sheet
    that produced six hotspots and refused two candidates is a different thing
    from a sheet that produced none and refused two, and only the pair says
    which.
    """
    return {
        "page": page_num,
        "printedPage": printed_page,
        "regions": list(region_ids),
        "anchors": list(anchors),
        "counts": dict(sorted(trace.counts.items())),
        "drops": [asdict(d) for d in trace.drops],
    }


# A rule that only reports that nothing survived. Attributing an empty sheet to
# one of these says no more than "it is empty": growth raises `no-marker`
# whenever marker detection came back with nothing, whatever emptied it. The
# refusal worth reading is the gate upstream that did the emptying, so a
# symptom is named only when no other rule refused anything on the sheet.
SYMPTOM_RULES = frozenset({
    "growth.no-marker",
    "marker.no-run",
    "prompt.no-opening",
})


def attribute_empty_page(page: Dict[str, Any]) -> str:
    """
    The one rule to read first on a sheet that produced no hotspot.

    Counted from the sheet's own refusals rather than from its counters, so a
    tally like `prompt.paragraphs` -- which says how much type the sheet holds,
    not that anything was refused -- can never be named as the cause.
    """
    tally: Dict[str, int] = {}
    for d in page.get("drops", ()):
        key = f"{d['stage']}.{d['rule']}"
        tally[key] = tally.get(key, 0) + 1
    if not tally:
        return "none.nothing-considered"
    upstream = {k: n for k, n in tally.items() if k not in SYMPTOM_RULES}
    pool = upstream or tally
    # Ties go to the rule that reads first alphabetically, so the histogram is
    # the same whichever order the drops happened to be recorded in.
    return min(sorted(pool), key=lambda k: -pool[k])


def serialize_trace(
    book_id: str,
    pdf_path: str,
    fingerprint: str,
    pages: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Assemble the book's trace sidecar, with the histogram already summed.

    The per-book `counts` total is what makes this a work queue rather than a
    log: it says which rule emptied the most sheets in this book, and the same
    key summed across books says which rule to fix first in the catalogue.
    """
    totals: Dict[str, int] = {}
    empty_by_rule: Dict[str, int] = {}
    empty_pages = 0

    for p in pages:
        for k, n in p.get("counts", {}).items():
            totals[k] = totals.get(k, 0) + n
        if p.get("regions"):
            continue
        empty_pages += 1
        cause = attribute_empty_page(p)
        empty_by_rule[cause] = empty_by_rule.get(cause, 0) + 1

    return {
        "traceVersion": TRACE_VERSION,
        "bookId": book_id,
        "pdf": os.path.basename(pdf_path),
        "fingerprint": fingerprint,
        "pageCount": len(pages),
        "emptyPages": empty_pages,
        "counts": dict(sorted(totals.items(), key=lambda kv: -kv[1])),
        "emptyPagesByRule": dict(sorted(empty_by_rule.items(), key=lambda kv: -kv[1])),
        "pages": list(pages),
    }


def save_trace_gz(path: str, data: Dict[str, Any]) -> int:
    """Write `trace.json.gz`, creating its directory. Returns bytes written."""
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with gzip.open(path, "wb", compresslevel=6) as f:
        f.write(raw)
    return os.path.getsize(path)


def load_trace_gz(path: str) -> Dict[str, Any]:
    """Read a trace sidecar back."""
    with gzip.open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))
