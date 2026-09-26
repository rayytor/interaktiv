"""
Every tunable constant in the detector, in one place, as data.

The detector's behaviour is decided by about seventy numbers -- how far left of
a picture its label may stand, how much of a drawn block a region must cover
before it takes it whole, how wide a gutter has to be before it is a column.
They were written as module globals and hand-tuned against the ten books that
happened to be on disk, which is exactly how the checked-in benchmark came to
overstate quality by twelve points.

This module turns them into a `Profile`: a frozen record with a declared type
and range per field, whose defaults are the literals the modules ship with.
`apply_profile` writes one into the six detector modules; nothing else in the
codebase ever assigns to them.

Three things are deliberate.

**The names do not change.** `regions.PAD` is still `PAD` at every one of its
twenty-three call sites, and the profile field is `PAD` too, so a knob can be
grepped from either end. Threading a config object through 2,500 lines would
have cost more than it bought.

**A knob is a name *in a module*, not a name.** `HEADING_RATIO` is 1.12 in
`prompts.py` and 1.15 in `regions.py` -- two different thresholds that happen
to share a word -- so a profile keyed by bare name would quietly force them to
one value. Where a name is defined twice its field is qualified
(`HEADING_RATIO__prompts`), and where a name is *imported* into another module
(`anchors.py` does `from .regions import PAD`) both bindings are written, since
a profile that set only one of them would be a detector running two values for
one knob. `binds_in` records that, and `test_profile.py` recomputes it from the
imports, so adding an alias cannot silently escape.

**The scorer's ruler is frozen.** `MIN_HOTSPOT`, `TALL_REGION` and `WHOLE_TOL`
are both detector constants and the thresholds the violation rules are written
in. If a fitting loop could move them it would learn to widen the ruler rather
than fix the regions -- a tenth of a point of `WHOLE_TOL` erases cut violations
without moving a single edge. `RULER` holds those three at their defaults, for
`score.py` alone, and `apply_profile` never touches it.
"""

from dataclasses import dataclass, field, fields, make_dataclass
import hashlib
import importlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

PROFILE_VERSION = 1

# The shipped profile, if there is one. A missing file means "the defaults",
# which is the same detector the modules describe on their own -- so a fresh
# checkout with no profile behaves exactly as the source reads.
DEFAULT_PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile.json")

# Modules whose globals a profile is written into. `score.py` is absent on
# purpose: it reads the frozen `RULER` instead, so a fitted profile cannot move
# the rules it is being judged by.
TARGET_MODULES: Tuple[str, ...] = (
    "layout", "markers", "prompts", "figures", "regions", "anchors", "serializer",
)


@dataclass(frozen=True)
class Knob:
    """
    One tunable constant: where it lives, what it means, and how far it may move.

    `lo`/`hi` are the search box, not an assertion about the detector -- a value
    outside them is refused because an optimizer that wandered there has lost
    the plot, not because the code would crash.

    `binds_in` is every module whose global has to be written for the knob to
    take effect: its own, plus any module that imported the name. Leave it empty
    and it defaults to the home module alone.
    """
    name: str
    module: str
    default: float
    kind: str          # "float" or "int"
    lo: float
    hi: float
    note: str = ""
    tunable: bool = True
    binds_in: Tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """
        The profile field name: the constant's name, qualified only when it has to be.

        Bare `PAD` reads the same in the profile, the JSON and the twenty-three
        call sites; `HEADING_RATIO__regions` is ugly, and is used only where the
        alternative would be two knobs pretending to be one.
        """
        return self.name if self.name not in _AMBIGUOUS else f"{self.name}__{self.module}"

    @property
    def targets(self) -> Tuple[str, ...]:
        return self.binds_in or (self.module,)

    def coerce(self, value: Any) -> float:
        """
        The knob's canonical form of a number.

        Floats are rounded to six decimals, which is finer than any threshold
        here is meaningful to and coarse enough to absorb the binary noise of
        the optimizer's own arithmetic: without it, encoding `HEADING_MEASURE`
        to the unit interval and back returned 0.6000000000000001, which is a
        different profile with a different hash and shows up in a diff as a
        knob somebody moved.
        """
        if self.kind == "int":
            return int(round(float(value)))
        return round(float(value), 6)

    def clamp(self, value: float) -> float:
        return self.coerce(min(self.hi, max(self.lo, float(value))))


# --------------------------------------------------------------- the knobs
#
# `default` must equal the literal the module ships with; `test_profile.py`
# reads both and fails if they ever part company, so this table cannot drift
# into being a second, wrong copy of the detector's settings.

SPEC: Tuple[Knob, ...] = (
    # ---- layout.py: where the page's furniture ends and its content begins
    Knob("HEADER_BAND", "layout", 40.0, "float", 0.0, 96.0, "pt from top of page"),
    Knob("FOOTER_BAND", "layout", 44.0, "float", 0.0, 96.0, "pt from bottom of page"),
    Knob("COLUMN_TOLERANCE", "layout", 12.0, "float", 2.0, 40.0, "pt spread within one column",
         binds_in=("layout", "markers", "prompts")),
    Knob("COLUMN_CHANNEL", "layout", 10.0, "float", 3.0, 36.0, "pt minimum gutter width",
         binds_in=("layout", "regions")),
    Knob("COLUMN_CLEAR", "layout", 0.5, "float", 0.2, 0.95, "share of height a gutter stays open",
         binds_in=("layout", "regions")),
    Knob("COLUMN_PAIRED", "layout", 0.35, "float", 0.1, 0.9, "share of height columns run side by side"),
    Knob("MIN_COLUMN_LINES", "layout", 4, "int", 2, 12, "line starts that make a cluster a column"),
    Knob("COLUMN_MIN_W", "layout", 54.0, "float", 24.0, 160.0, "pt: narrowest real column"),
    Knob("BODY_LINE_CHARS", "layout", 20, "int", 6, 60, "characters that make a line body text"),
    Knob("BODY_LINE_WIDTH", "layout", 60.0, "float", 20.0, 200.0, "pt a body line must run"),
    # A page number has as many digits as it has; this is a parser bound, not a
    # threshold, and fitting it could only ever break the folio.
    Knob("FOLIO_MAX", "layout", 4, "int", 4, 4, "max digits in a printed page number", tunable=False),

    # ---- markers.py: reading the printed label off a question
    Knob("GUTTER_RATIO", "markers", 0.5, "float", 0.1, 1.5, "clear space a label needs on its left"),
    Knob("HANGING_INDENT", "markers", 26.0, "float", 8.0, 72.0, "pt the instruction sits to the right"),
    Knob("MAX_SUBQUESTION_GAP", "markers", 4.0, "float", 0.5, 20.0, "pt alignment slack for sub-questions"),

    # ---- prompts.py: where one instruction stops and the next begins
    Knob("PARA_LEADING", "prompts", 1.55, "float", 1.05, 2.6, "leading multiple that opens a paragraph"),
    Knob("PARA_INDENT", "prompts", 18.0, "float", 4.0, 54.0, "pt left-edge shift that opens one"),
    Knob("SIZE_SHIFT", "prompts", 1.12, "float", 1.02, 1.6, "type-size ratio that opens one"),
    Knob("SHORT_LINE", "prompts", 12.0, "float", 2.0, 60.0, "pt short of measure that ends one"),
    Knob("PROMPT_MIN_CHARS", "prompts", 12, "int", 3, 40, "characters that make a question"),
    Knob("PROMPT_MAX_LINES", "prompts", 8, "int", 2, 20, "lines of an opening paragraph read for cues"),
    Knob("HEADING_RATIO", "prompts", 1.12, "float", 1.02, 1.6, "size over body that makes a title"),
    Knob("RUBRIC_MAX_CHARS", "prompts", 48, "int", 16, 120, "longest rubric heading"),
    Knob("QUESTION_MAX_CHARS", "prompts", 140, "int", 40, 400, "longest paragraph a mid-text ? can own"),
    Knob("RUBRIC_REACH", "prompts", 90.0, "float", 20.0, 240.0, "pt a rubric gathers its instruction across"),
    Knob("SCOPE_MIN", "prompts", 20.0, "float", 4.0, 80.0, "pt: shortest scope a prompt can own"),
    Knob("NEAR_MARKER", "prompts", 6.0, "float", 1.0, 24.0, "pt slack for a line being a marker's own"),

    # ---- figures.py: holding a picture together, and finding its caption
    Knob("FIGURE_WELD", "figures", 6.0, "float", 1.0, 24.0, "pt gap that still reads as one picture"),
    Knob("FIGURE_REACH", "figures", 0.6, "float", 0.1, 1.5, "weld reach as a share of the tile's short side"),
    Knob("FIGURE_SPAN", "figures", 30.0, "float", 6.0, 96.0, "pt: widest gap a weld may bridge"),
    Knob("FIGURE_MIN", "figures", 24.0, "float", 8.0, 72.0, "pt: figure, not icon"),
    Knob("FIGURE_AREA", "figures", 600.0, "float", 100.0, 4000.0, "pt^2 of ink worth holding together"),
    Knob("FIGURE_TILE", "figures", 4.0, "float", 1.0, 16.0, "pt: smallest side that can join a figure"),
    Knob("FIGURE_SHARE", "figures", 0.6, "float", 0.3, 0.95, "share of the content box that is the ground"),
    Knob("CAPTION_GAP", "figures", 14.0, "float", 2.0, 48.0, "pt a caption may sit under its figure"),
    Knob("CAPTION_SIDE", "figures", 24.0, "float", 4.0, 72.0, "pt a margin caption may sit beside it"),
    Knob("CAPTION_LINES", "figures", 4, "int", 0, 12, "lines of caption after the first"),
    Knob("ART_COVER", "figures", 0.5, "float", 0.1, 0.95, "share of a stroke that must lie over a figure"),
    Knob("MARKER_REACH", "figures", 44.0, "float", 8.0, 120.0, "pt left of a picture its label may stand"),

    # ---- regions.py: growing the hotspot, and where its edge may come to rest
    Knob("PANEL_MIN_W", "regions", 60.0, "float", 20.0, 180.0, "pt: minimum width of a text panel"),
    Knob("PANEL_MIN_H", "regions", 30.0, "float", 10.0, 100.0, "pt: minimum height of one"),
    Knob("PANEL_LINES", "regions", 2, "int", 1, 6, "lines of prose inside a panel"),
    Knob("RULE_THICK", "regions", 2.5, "float", 0.5, 8.0, "pt: thickest stroke still a rule"),
    Knob("RULE_MIN", "regions", 20.0, "float", 6.0, 72.0, "pt: shortest rule"),
    Knob("RULE_CLUSTER", "regions", 6.0, "float", 1.0, 24.0, "pt: cluster distance for rules"),
    Knob("RULE_ROW", "regions", 30.0, "float", 8.0, 90.0, "pt: tallest row in a ruled grid"),
    Knob("MIN_CELL", "regions", 8.0, "float", 2.0, 30.0, "pt: smallest cell dimension"),
    Knob("HOTSPOT_GAP", "regions", 3.0, "float", 0.0, 16.0, "pt separation between hotspots"),
    Knob("HAIRLINE", "regions", 0.25, "float", 0.0, 1.0, "pt overlap that is a seam, not a dispute"),
    Knob("PAD", "regions", 8.0, "float", 0.0, 32.0, "pt breathing room around text",
         binds_in=("regions", "anchors")),
    Knob("MIN_HOTSPOT", "regions", 6.0, "float", 6.0, 6.0,
         "pt: minimum hotspot side -- also the scorer's sliver rule", tunable=False),
    Knob("FLOW_LEADING", "regions", 1.8, "float", 1.05, 3.0, "leading multiple that still reads as next line"),
    Knob("FLOW_BREAK", "regions", 90.0, "float", 20.0, 240.0, "pt gap that ends flow before a list"),
    Knob("HEADING_RATIO", "regions", 1.15, "float", 1.02, 1.6, "size ratio that indicates a heading"),
    Knob("BODY_EVIDENCE", "regions", 100.0, "float", 20.0, 400.0, "pt of character width that settles body size"),
    Knob("HEADING_MEASURE", "regions", 0.6, "float", 0.2, 0.95, "fraction of measure for a heading"),
    Knob("MIN_STRIP", "regions", 24.0, "float", 6.0, 72.0, "pt: minimum height of an independent strip"),
    Knob("BRIDGE", "regions", 12.0, "float", 2.0, 48.0, "pt gap that does not interrupt a full-width run"),
    Knob("STACK_GAP", "regions", 14.0, "float", 2.0, 60.0, "pt two pieces of one region may bridge"),
    Knob("STACK_OVERLAP", "regions", 0.6, "float", 0.2, 0.95, "share of the narrower piece that must overlap"),
    Knob("COLUMN_EDGE", "regions", 6.0, "float", 0.0, 24.0, "pt overhang allowed for a block in a column"),
    Knob("WHOLE_TOL", "regions", 0.05, "float", 0.05, 0.05,
         "share tolerance for taking a block whole -- also the scorer's cut rule", tunable=False),
    Knob("TALL_REGION", "regions", 0.7, "float", 0.7, 0.7,
         "share of the sheet past which a hotspot is not one -- also the scorer's tall rule", tunable=False),
    Knob("RETREAT_SHARE", "regions", 0.75, "float", 0.3, 0.95, "share of a block a hotspot keeps rather than retreats"),
    Knob("RETREAT_COST", "regions", 0.5, "float", 0.05, 0.9, "share of its own area a hotspot may give up"),
    Knob("GRAPHIC_REACH", "regions", 0.55, "float", 0.2, 0.95, "share of a graphic a band must hold to own it"),
    Knob("GRAPHIC_DROP", "regions", 72.0, "float", 12.0, 200.0, "pt below the text past which a graphic is not its own"),
    Knob("GRAPHIC_SHARE", "regions", 0.5, "float", 0.2, 0.95, "share of the band past which a shape is furniture"),
    Knob("GRAPHIC_LINK", "regions", 24.0, "float", 4.0, 90.0, "pt to the next shape that reads as one diagram"),
    Knob("GRAPHIC_PASSES", "regions", 8, "int", 1, 20, "rings out from the text a diagram may be followed"),
    Knob("GRAPHIC_LABEL", "regions", 12.0, "float", 2.0, 48.0, "pt outside a diagram its labels may sit"),
    Knob("GRAPHIC_HELD", "regions", 0.7, "float", 0.3, 0.95, "share of a shape a block must hold to own it"),

    # ---- anchors.py: binding a region to the publisher's icon
    Knob("ANCHOR_ABOVE", "anchors", 24.0, "float", 4.0, 72.0, "pt an anchor icon may sit above its line"),
    Knob("BAND_REACH_ABOVE", "anchors", 24.0, "float", 2.0, 72.0, "pt"),
    Knob("BAND_REACH_BELOW", "anchors", 6.0, "float", 0.0, 48.0, "pt"),
    Knob("BAND_REACH_ACROSS", "anchors", 24.0, "float", 2.0, 96.0, "pt"),
)

# A name defined in more than one module is two knobs, not one, and its field
# has to say which. Computed rather than listed so that a future collision is
# handled by the same rule instead of by whoever notices it.
_AMBIGUOUS = frozenset(
    name for name in {k.name for k in SPEC}
    if len({k.module for k in SPEC if k.name == name}) > 1
)

BY_KEY: Dict[str, Knob] = {k.key: k for k in SPEC}
TUNABLE: Tuple[Knob, ...] = tuple(k for k in SPEC if k.tunable and k.hi > k.lo)


def _check_spec() -> None:
    seen: Dict[Tuple[str, str], bool] = {}
    for k in SPEC:
        ident = (k.module, k.name)
        if ident in seen:
            raise ValueError(f"knob {k.module}.{k.name} declared twice")
        seen[ident] = True
        if not (k.lo <= k.default <= k.hi):
            raise ValueError(f"knob {k.key}: default {k.default} outside [{k.lo}, {k.hi}]")
        if k.kind not in ("float", "int"):
            raise ValueError(f"knob {k.key}: unknown kind {k.kind!r}")
        for target in k.targets:
            if target not in TARGET_MODULES:
                raise ValueError(f"knob {k.key}: binds in unknown module {target!r}")
        if k.module not in k.targets:
            raise ValueError(f"knob {k.key}: binds_in must include its own module")


_check_spec()


# ------------------------------------------------------------- the profile
#
# Built from `SPEC` rather than written out, because a hand-written dataclass
# beside a hand-written range table is two lists that have to agree, and they
# would not stay agreeing. Field names are the constant names, upper case, so
# the JSON, the profile and the detector all spell a knob the same way.

def _profile_repr(self) -> str:
    return f"Profile(hash={profile_hash(self)}, changed={len(changed_from_default(self))})"


def _profile_to_dict(self) -> Dict[str, float]:
    return {k.key: getattr(self, k.key) for k in SPEC}


def _profile_replace(self, **changes: float) -> "Profile":
    values = _profile_to_dict(self)
    for key, value in changes.items():
        knob = BY_KEY.get(key)
        if knob is None:
            raise KeyError(f"no such knob: {key}")
        values[key] = knob.coerce(value)
    return Profile(**values)


Profile = make_dataclass(
    "Profile",
    [(k.key, int if k.kind == "int" else float, field(default=k.default)) for k in SPEC],
    frozen=True,
    repr=False,
    namespace={
        "__doc__": (
            "One complete setting of the detector.\n\n"
            "Frozen, because a profile is handed to a child process and compared "
            "by hash; a mutable one could be edited after the hash was taken and "
            "a bake would then record a profile that never ran."
        ),
        "__repr__": _profile_repr,
        "to_dict": _profile_to_dict,
        "replace": _profile_replace,
    },
)

DEFAULTS: "Profile" = Profile()


# The three thresholds the violation rules are written in, pinned to their
# defaults. `score.py` reads these and nothing else, so no profile -- fitted,
# hand-edited or loaded from disk -- can move the ruler it is measured with.
@dataclass(frozen=True)
class Ruler:
    MIN_HOTSPOT: float
    TALL_REGION: float
    WHOLE_TOL: float


RULER = Ruler(
    MIN_HOTSPOT=DEFAULTS.MIN_HOTSPOT,
    TALL_REGION=DEFAULTS.TALL_REGION,
    WHOLE_TOL=DEFAULTS.WHOLE_TOL,
)


# ----------------------------------------------------------- serialisation

def to_dict(p: "Profile") -> Dict[str, float]:
    return p.to_dict()


def from_dict(d: Dict[str, Any], *, strict: bool = True) -> "Profile":
    """
    Read a profile out of JSON, refusing anything it cannot honour.

    Strict is the default because the two ways this is called -- loading the
    shipped profile, and reading a candidate back from a child process -- are
    both cases where a silently ignored key means the detector is not running
    what the caller believes it is running.
    """
    values = to_dict(DEFAULTS)
    unknown = [k for k in d if k not in BY_KEY]
    if unknown and strict:
        raise KeyError(f"unknown knob(s): {', '.join(sorted(unknown))}")
    for key, raw in d.items():
        knob = BY_KEY.get(key)
        if knob is None:
            continue
        try:
            value = knob.coerce(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"knob {key}: {raw!r} is not a {knob.kind}") from exc
        if strict and not (knob.lo <= value <= knob.hi):
            raise ValueError(f"knob {key}: {value} outside [{knob.lo}, {knob.hi}]")
        values[key] = knob.clamp(value)
    return Profile(**values)


def profile_hash(p: "Profile") -> str:
    """
    A short, stable name for one setting of the detector.

    Stamped into `regions.json`, so a bake says which profile made it and a
    re-bake can be triggered by a retrained profile rather than only by a
    changed PDF. Floats are formatted with `repr`, which round-trips exactly,
    so the hash is a hash of the values and not of how they were printed.
    """
    payload = json.dumps(
        {"version": PROFILE_VERSION, "knobs": {k.key: repr(getattr(p, k.key)) for k in SPEC}},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def changed_from_default(p: "Profile") -> Dict[str, Tuple[float, float]]:
    """Which knobs this profile moves, and from what -- the useful half of a diff."""
    out: Dict[str, Tuple[float, float]] = {}
    for k in SPEC:
        now = getattr(p, k.key)
        if now != k.default:
            out[k.key] = (k.default, now)
    return out


def save_profile(p: "Profile", path: str = DEFAULT_PROFILE_PATH, *, note: str = "") -> str:
    """
    Write a profile as JSON, recording only what it changes.

    A file listing all seventy values would make every default look like a
    decision someone took; the point of the sparse form is that reviewing a
    fitted profile means reading the handful of lines that differ.
    """
    body: Dict[str, Any] = {
        "version": PROFILE_VERSION,
        "hash": profile_hash(p),
        "knobs": {key: getattr(p, key) for key in sorted(changed_from_default(p))},
    }
    if note:
        body["note"] = note
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f, indent=2, ensure_ascii=False, sort_keys=False)
        f.write("\n")
    return body["hash"]


def load_profile(path: Optional[str] = DEFAULT_PROFILE_PATH) -> "Profile":
    """
    The profile the detector should run, or the defaults if none is shipped.

    A missing file is not an error: it means the detector runs the constants
    its source shows, which is what a fresh checkout should do.
    """
    if not path or not os.path.isfile(path):
        return DEFAULTS
    with open(path, "r", encoding="utf-8") as f:
        body = json.load(f)
    if body.get("version") != PROFILE_VERSION:
        raise ValueError(
            f"{path}: profile version {body.get('version')!r}, expected {PROFILE_VERSION}"
        )
    p = from_dict(body.get("knobs") or {})
    stamped = body.get("hash")
    if stamped and stamped != profile_hash(p):
        raise ValueError(f"{path}: hash {stamped} does not match its own values")
    return p


# ------------------------------------------------------------- application

_ACTIVE: "Profile" = DEFAULTS


def _modules() -> List[Any]:
    return [importlib.import_module(f".{name}", __package__) for name in TARGET_MODULES]


def apply_profile(p: "Profile") -> "Profile":
    """
    Make `p` the detector's settings, everywhere the names are bound.

    Returns the profile that was active before, so a caller that needs the old
    one back -- a test, mostly -- does not have to have saved it. In the fitting
    loop nothing restores anything: each candidate is a fresh child process, so
    there is no reentrancy question and a crashed candidate cannot leave a
    half-applied profile behind.
    """
    global _ACTIVE
    previous = _ACTIVE
    mods = dict(zip(TARGET_MODULES, _modules()))
    for k in SPEC:
        value = getattr(p, k.key)
        for target in k.targets:
            mod = mods[target]
            if not hasattr(mod, k.name):
                raise AttributeError(
                    f"knob {k.key} claims to bind in {target}, which has no {k.name}"
                )
            setattr(mod, k.name, value)
    _ACTIVE = p
    return previous


def active_profile() -> "Profile":
    return _ACTIVE


def source_values() -> Dict[str, Dict[str, float]]:
    """
    What the modules currently hold for every knob, per module.

    Used by the tests two ways: to assert the defaults in `SPEC` really are the
    literals the source ships, and to assert `apply_profile` reaches every
    binding of a name rather than only the one in its home module.
    """
    out: Dict[str, Dict[str, float]] = {}
    for name, mod in zip(TARGET_MODULES, _modules()):
        held = {k.name: getattr(mod, k.name) for k in SPEC if hasattr(mod, k.name)}
        if held:
            out[name] = held
    return out
