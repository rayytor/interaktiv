"""
Drawings that belong to a book.

A teacher draws with Rayyanpen, the overlay pen, and a stroke that ends over a
page is handed to the reader, which keeps it here. Strokes are stored in PDF
user space (see `geometry.py`), so they stay on the printed line they were
drawn over whatever the zoom, scroll, view mode or rotation.

Each book keeps at most `BUDGET_BYTES` of drawings, measured as the size of its
file on disk. When a new stroke would go over, the oldest strokes are deleted
until it fits; the newest stroke is never the one dropped.

The module is pure Python so that it can be tested without GTK. Saving is
atomic; when to save is the caller's business (the reader coalesces writes).
"""

from __future__ import annotations

import json
import math
import os
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import appdirs

BUDGET_BYTES = 5 * 1024 * 1024
FORMAT_VERSION = 1

FREEHAND = ("pen", "marker")
SHAPES = ("line", "arrow", "rect", "ellipse")
KINDS = FREEHAND + SHAPES

# Points are kept to a tenth of a PDF point (0.035 mm on paper), which is well
# under a board pixel at any zoom the reader offers.
PRECISION = 1
ELLIPSE_SEGMENTS = 48


def drawings_dir() -> str:
    """Where every book's drawings file lives. Survives app updates."""
    return os.path.join(appdirs.data_dir(), "drawings")


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return secrets.token_hex(6)


def _round(values: Iterable[float]) -> List[float]:
    out = []
    for v in values:
        r = round(float(v), PRECISION)
        out.append(int(r) if r == int(r) else r)
    return out


@dataclass(eq=False)
class InkStroke:
    """
    One stroke on one page.

    `points` is a flat `[x0, y0, x1, y1, ...]` list in PDF user space (y up).
    Shapes keep only their two defining corners. `width` is in PDF points, so
    ink thickens as the page is zoomed, like ink on paper would.
    """

    page: int
    kind: str
    rgba: str
    width: float
    points: List[float]
    id: str = field(default_factory=new_id)
    created_ms: int = field(default_factory=now_ms)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown stroke kind: {self.kind!r}")
        if len(self.points) < 2 or len(self.points) % 2:
            raise ValueError("a stroke needs at least one (x, y) point")
        self.points = _round(self.points)
        self.width = round(float(self.width), 2)

    # -- serialisation ---------------------------------------------------

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "page": self.page,
            "t": self.created_ms,
            "kind": self.kind,
            "rgba": self.rgba,
            "w": self.width,
            "pts": self.points,
        }

    @classmethod
    def from_json(cls, data: dict) -> "InkStroke":
        return cls(
            page=int(data["page"]),
            kind=str(data["kind"]),
            rgba=str(data["rgba"]),
            width=float(data["w"]),
            points=list(data["pts"]),
            id=str(data["id"]),
            created_ms=int(data["t"]),
        )

    def encoded_size(self) -> int:
        return len(_dumps(self.to_json()))

    # -- geometry --------------------------------------------------------

    def pairs(self) -> List[Tuple[float, float]]:
        p = self.points
        return [(p[i], p[i + 1]) for i in range(0, len(p), 2)]

    def outline(self) -> List[Tuple[float, float]]:
        """The line the stroke's ink follows, for hit testing and drawing."""
        pts = self.pairs()
        if self.kind in FREEHAND or len(pts) < 2:
            return pts
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        if self.kind in ("line", "arrow"):
            return [(x0, y0), (x1, y1)]
        if self.kind == "rect":
            return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = abs(x1 - x0) / 2, abs(y1 - y0) / 2
        return [
            (
                cx + rx * math.cos(2 * math.pi * i / ELLIPSE_SEGMENTS),
                cy + ry * math.sin(2 * math.pi * i / ELLIPSE_SEGMENTS),
            )
            for i in range(ELLIPSE_SEGMENTS + 1)
        ]

    def bbox(self) -> Tuple[float, float, float, float]:
        """`(x0, y0, x1, y1)` including half the pen width."""
        pts = self.outline()
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        h = self.width / 2
        return (min(xs) - h, min(ys) - h, max(xs) + h, max(ys) + h)


def _dumps(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# -- distance helpers ----------------------------------------------------


def simplify(points: Sequence[Tuple[float, float]], tolerance: float) -> List[Tuple[float, float]]:
    """
    Drop points that lie within `tolerance` of the line through their
    neighbours (Ramer-Douglas-Peucker). A board samples a finger 125 times a
    second, and most of those samples add bytes to the 5 MB budget but nothing
    to the line.
    """
    pts = list(points)
    if len(pts) < 3 or tolerance <= 0:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        first, last = stack.pop()
        ax, ay = pts[first]
        bx, by = pts[last]
        worst, index = -1.0, -1
        for i in range(first + 1, last):
            d = _seg_point_dist(ax, ay, bx, by, *pts[i])
            if d > worst:
                worst, index = d, i
        if index >= 0 and worst > tolerance:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, k in zip(pts, keep) if k]


def _seg_point_dist(ax, ay, bx, by, px, py) -> float:
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _segments_cross(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    return o1 * o2 < 0 and o3 * o4 < 0


def _seg_seg_dist(a, b, c, d) -> float:
    if _segments_cross(a, b, c, d):
        return 0.0
    return min(
        _seg_point_dist(*a, *b, *c),
        _seg_point_dist(*a, *b, *d),
        _seg_point_dist(*c, *d, *a),
        _seg_point_dist(*c, *d, *b),
    )


def _path_segments(path: Sequence[Tuple[float, float]]):
    if len(path) == 1:
        return [(path[0], path[0])]
    return list(zip(path, path[1:]))


def _near_path(p, path_segs, reach: float) -> bool:
    return any(_seg_point_dist(*a, *b, *p) <= reach for a, b in path_segs)


def _seg_near_path(a, b, path_segs, reach: float) -> bool:
    return any(_seg_seg_dist(a, b, c, d) <= reach for c, d in path_segs)


# -- one book ------------------------------------------------------------


class BookInk:
    """
    Every stroke drawn on one book, in the order they were added.

    `version` goes up on every change and `page_version(page)` on every change
    to that page, so a renderer can cache a page's ink until it changes.
    """

    def __init__(
        self,
        book_id: str,
        path: Optional[str] = None,
        budget: int = BUDGET_BYTES,
    ) -> None:
        self.book_id = book_id
        self.path = path or os.path.join(drawings_dir(), f"{book_id}.json")
        self.budget = budget
        self._strokes: Dict[str, InkStroke] = {}
        self._sizes: Dict[str, int] = {}
        self._payload = 0  # bytes of the encoded strokes, commas included
        self.version = 0
        self._page_versions: Dict[int, int] = {}
        self.dirty = False

    # -- queries -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._strokes)

    def get(self, stroke_id: str) -> Optional[InkStroke]:
        return self._strokes.get(stroke_id)

    def strokes_on(self, page: int) -> List[InkStroke]:
        return [s for s in self._strokes.values() if s.page == page]

    def page_version(self, page: int) -> int:
        return self._page_versions.get(page, 0)

    def size_bytes(self) -> int:
        """The size the file has when saved now."""
        return len(self._header()) + self._payload + 3  # "[" and "]}"

    def _header(self) -> bytes:
        # Everything before the strokes array; the file is header + array + "}".
        head = _dumps({"version": FORMAT_VERSION, "book": self.book_id})
        return head[:-1] + b',"strokes":'

    # -- changes -----------------------------------------------------------

    def _touch(self, page: int) -> None:
        self.version += 1
        self._page_versions[page] = self._page_versions.get(page, 0) + 1
        self.dirty = True

    def _insert(self, stroke: InkStroke) -> None:
        size = stroke.encoded_size()
        self._payload += size + (1 if self._strokes else 0)
        self._strokes[stroke.id] = stroke
        self._sizes[stroke.id] = size
        self._touch(stroke.page)

    def _drop(self, stroke_id: str) -> Optional[InkStroke]:
        stroke = self._strokes.pop(stroke_id, None)
        if stroke is None:
            return None
        self._payload -= self._sizes.pop(stroke_id) + (1 if self._strokes else 0)
        self._touch(stroke.page)
        return stroke

    def _enforce_budget(self, keep: Iterable[str] = ()) -> List[InkStroke]:
        """Delete the oldest strokes until the file fits. Returns what went."""
        keep = set(keep)
        evicted: List[InkStroke] = []
        if self.size_bytes() <= self.budget:
            return evicted
        oldest = sorted(
            (s for s in self._strokes.values() if s.id not in keep),
            key=lambda s: s.created_ms,
        )
        for stroke in oldest:
            if self.size_bytes() <= self.budget:
                break
            self._drop(stroke.id)
            evicted.append(stroke)
        return evicted

    def add(self, stroke: InkStroke) -> List[InkStroke]:
        """Keep a new stroke. Returns the old strokes deleted to make room."""
        self._insert(stroke)
        return self._enforce_budget(keep=(stroke.id,))

    def remove(self, ids: Iterable[str]) -> List[InkStroke]:
        """Take strokes away (undo of a handoff). Returns them for a redo."""
        return [s for s in (self._drop(i) for i in ids) if s is not None]

    def restore(self, strokes: Iterable[InkStroke]) -> List[InkStroke]:
        """Put strokes back (redo, or undo of an erase)."""
        restored = []
        for stroke in strokes:
            if stroke.id not in self._strokes:
                self._insert(stroke)
                restored.append(stroke.id)
        return self._enforce_budget(keep=restored)

    def clear_page(self, page: int) -> List[InkStroke]:
        return self.remove([s.id for s in self.strokes_on(page)])

    def erase(
        self, page: int, path: Sequence[Tuple[float, float]], radius: float
    ) -> Tuple[List[InkStroke], List[InkStroke]]:
        """
        Rub out ink under an eraser that went along `path` (PDF user space).

        A freehand stroke loses only the part that was touched and is split
        into the pieces left over, the way a real eraser leaves a line in two;
        a shape the eraser touches goes as a whole. Returns
        `(removed, added)` so that the caller can undo it.
        """
        if not path:
            return [], []
        segs = _path_segments(path)
        xs = [x for x, _ in path]
        ys = [y for _, y in path]
        removed: List[InkStroke] = []
        added: List[InkStroke] = []
        for stroke in list(self.strokes_on(page)):
            reach = radius + stroke.width / 2
            x0, y0, x1, y1 = stroke.bbox()
            if (
                x1 < min(xs) - radius
                or x0 > max(xs) + radius
                or y1 < min(ys) - radius
                or y0 > max(ys) + radius
            ):
                continue
            outline = stroke.outline()
            if stroke.kind in SHAPES:
                hit = any(
                    _seg_near_path(a, b, segs, reach) for a, b in _path_segments(outline)
                )
                if hit:
                    removed.append(self._drop(stroke.id))
                continue
            pieces = self._split(outline, segs, reach)
            if pieces is None:
                continue  # untouched
            removed.append(self._drop(stroke.id))
            for piece in pieces:
                flat = [c for p in piece for c in p]
                part = InkStroke(
                    page=stroke.page,
                    kind=stroke.kind,
                    rgba=stroke.rgba,
                    width=stroke.width,
                    points=flat,
                    created_ms=stroke.created_ms,
                )
                self._insert(part)
                added.append(part)
        return removed, added

    @staticmethod
    def _split(outline, segs, reach):
        """The runs of a polyline the eraser missed, or None if it missed all."""
        kept = [not _near_path(p, segs, reach) for p in outline]
        if len(outline) == 1:
            return None if kept[0] else []
        seg_kept = [
            kept[i] and kept[i + 1] and not _seg_near_path(outline[i], outline[i + 1], segs, reach)
            for i in range(len(outline) - 1)
        ]
        if all(kept) and all(seg_kept):
            return None
        pieces, run = [], []
        for i, ok in enumerate(seg_kept):
            if ok:
                if not run:
                    run = [outline[i]]
                run.append(outline[i + 1])
            elif run:
                pieces.append(run)
                run = []
        if run:
            pieces.append(run)
        return [p for p in pieces if len(p) >= 2]

    # -- persistence ---------------------------------------------------------

    def to_bytes(self) -> bytes:
        body = b",".join(_dumps(s.to_json()) for s in self._strokes.values())
        return self._header() + b"[" + body + b"]}"

    def save(self) -> bool:
        """Write the file atomically. Returns False if the disk said no."""
        directory = os.path.dirname(self.path)
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ink-", suffix=".json")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(self.to_bytes())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            return False
        self.dirty = False
        return True

    @classmethod
    def load(
        cls, book_id: str, path: Optional[str] = None, budget: int = BUDGET_BYTES
    ) -> "BookInk":
        """Read a book's drawings. A missing or damaged file is an empty book."""
        ink = cls(book_id, path, budget)
        try:
            with open(ink.path, "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
            strokes = data.get("strokes", []) if isinstance(data, dict) else []
        except (OSError, ValueError):
            return ink
        for raw in strokes:
            try:
                ink._insert(InkStroke.from_json(raw))
            except (KeyError, TypeError, ValueError):
                continue
        ink._enforce_budget()
        ink.dirty = False
        return ink


class EraseSession:
    """
    One eraser gesture, streamed in pieces, undone as one step.

    Rayyanpen sends the eraser's path a few times a second while the finger
    moves. A piece of stroke that this same gesture created and then erased
    again never existed as far as undo is concerned.
    """

    def __init__(self) -> None:
        self.removed: Dict[str, InkStroke] = {}
        self.added: Dict[str, InkStroke] = {}
        self.undone = False

    def record(self, removed: Iterable[InkStroke], added: Iterable[InkStroke]) -> None:
        for stroke in removed:
            if stroke.id in self.added:
                del self.added[stroke.id]
            else:
                self.removed[stroke.id] = stroke
        for stroke in added:
            self.added[stroke.id] = stroke

    def undo(self, ink: BookInk) -> None:
        if self.undone:
            return
        ink.remove(list(self.added))
        ink.restore(self.removed.values())
        self.undone = True

    def redo(self, ink: BookInk) -> None:
        if not self.undone:
            return
        ink.remove(list(self.removed))
        ink.restore(self.added.values())
        self.undone = False
