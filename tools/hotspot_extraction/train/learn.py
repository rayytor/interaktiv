"""
Stage 4.4: does a learned scorer beat the fitted rules? -- measured, not assumed.

The question the plan asks is not "can a model be trained here" but "is one
worth shipping", and those have different answers. This module builds the
dataset, trains the model, and reports the comparison on **held-out books**,
with the honest outcome allowed to be no.

**Where the labels come from, and what is wrong with them.** The publisher's
manifest says which activities are interactive, and the scorer's join already
decides which detected region answers each entry. A region that answered one is
a positive; a region on a sheet that *has* entries and answered none is a
negative. Nobody draws a box, which was the condition on doing this at all.

The weakness is in the negatives, and it has to be said plainly: a page may
carry five activities of which the publisher marked one interactive, so four
perfectly good regions are labelled 0. The label is therefore "is this the kind
of activity a publisher marks", not "is this a real activity". A model trained
on it can only ever be used to *rank* candidates on a page against each other,
never to decide in absolute terms that a region is wrong -- and the evaluation
below reflects that: it asks whether the ranking is any good, and then whether
acting on it improves the objective.

**Why logistic regression and not a gradient-boosted tree.** The plan allowed
either. A tree would need scikit-learn at training time, and the comparison it
would win or lose is against a rules detector that ships with two dependencies.
Fitting a linear model on 28 features by gradient descent is forty lines and no
dependency at all, so the "is it worth a dependency" question can be answered
after seeing whether *any* learned signal helps, rather than before. If the
linear model shows a real margin, the tree becomes worth trying; if it shows
none, the tree was never the thing standing in the way.
"""

from dataclasses import dataclass, field
import json
import math
import os
import random
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from interaktiv_core.linking import link_oges, oge_view, region_view
from tools.hotspot_extraction.scanner.profile import RULER, apply_profile, from_dict, load_profile
from tools.hotspot_extraction.scanner.regions import clean_page_activities
from tools.hotspot_extraction.scanner.score import interactive_oges, _group_by_printed_page
from tools.hotspot_extraction.scanner.serializer import serialize_activity
from tools.hotspot_extraction.train import cache as cache_mod
from tools.hotspot_extraction.train.features import (
    FEATURE_NAMES,
    N_FEATURES,
    features_for,
    page_context,
)

MODEL_VERSION = 1


# ------------------------------------------------------------- the dataset


@dataclass
class Dataset:
    x: List[List[float]] = field(default_factory=list)
    y: List[int] = field(default_factory=list)
    book: List[str] = field(default_factory=list)
    page: List[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.y)

    def positives(self) -> int:
        return sum(self.y)

    def extend(self, other: "Dataset") -> None:
        self.x.extend(other.x)
        self.y.extend(other.y)
        self.book.extend(other.book)
        self.page.extend(other.page)


def examples_for_book(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Child entry point: every labelled region of one book.

    Only sheets the publisher put at least one entry on contribute. Everywhere
    else there is nothing to be right or wrong about, and including those pages
    would teach the model that most regions are negatives -- which is a fact
    about the manifest's coverage, not about the regions.
    """
    import pymupdf
    pymupdf.TOOLS.set_low_memory(True)

    apply_profile(from_dict(task["profile"]))
    book_id = task["book_id"]
    shard = cache_mod.load_shard(cache_mod.shard_path(book_id, task["cache_dir"]))
    oges_by_printed = _group_by_printed_page(interactive_oges(book_id))

    ds = Dataset()
    for sp in shard.pages:
        printed = shard.folio_map.get(sp.page_num)
        page_oges = oges_by_printed.get(printed, []) if printed is not None else []
        if not page_oges:
            continue
        result = cache_mod.replay_page(sp)
        if not result.activities:
            continue
        acts = [serialize_activity(a) for a in clean_page_activities(result.activities)]
        if not acts:
            continue

        prim = sp.primitives
        ctx = page_context(prim, result.layout, result.panels, result.solutions, acts)

        matched = _matched_region_ids(acts, page_oges, sp.page_num, prim.width, prim.height)
        for act in acts:
            ds.x.append(features_for(act, ctx, whole_tol=RULER.WHOLE_TOL))
            ds.y.append(1 if act["id"] in matched else 0)
            ds.book.append(book_id)
            ds.page.append(sp.page_num)
        del result, ctx
    return {
        "book_id": book_id,
        "x": ds.x, "y": ds.y, "book": ds.book, "page": ds.page,
    }


def _matched_region_ids(
    activities: Sequence[Dict[str, Any]],
    page_oges: Sequence[Dict[str, Any]],
    page_num: int,
    page_w: float,
    page_h: float,
) -> set:
    """
    Which regions on this sheet answered a publisher entry.

    The same join the scorer uses -- `link_oges` plus the two shapes of anchor
    binding -- so a positive here means exactly what an `ok` bucket means there.
    Re-deriving it with a looser rule would train the model on a target the
    score does not measure.
    """
    out = set()
    prefix = f"p{page_num}-oge-"
    for a in activities:
        if a.get("ogeId"):
            out.add(a["id"])
        elif a["id"].startswith(prefix):
            out.add(a["id"])

    region_views = [
        region_view(
            region_id=a["id"], label=a.get("label"), rect=tuple(a["rect"]),
            page_width=page_w, page_height=page_h,
            anchored=bool(a.get("anchored")), oge_id=a.get("ogeId"),
        )
        for a in activities
    ]
    oge_views = [
        oge_view(oge_id=o["id"], title=o.get("baslik") or "",
                 printed_page=o.get("sayfano"), posx=o.get("posx"), posy=o.get("posy"))
        for o in page_oges
    ]
    for ri in link_oges(region_views, oge_views, confidence=None).keys():
        out.add(activities[ri]["id"])
    return out


def build_dataset(
    book_ids: Sequence[str],
    *,
    cache_dir: str = cache_mod.DEFAULT_CACHE_DIR,
    profile=None,
    workers: int = 8,
) -> Dataset:
    """One short-lived child per book, as everything else here does."""
    from concurrent.futures import ProcessPoolExecutor

    profile = profile or load_profile()
    tasks = [
        {"book_id": b, "cache_dir": cache_dir, "profile": profile.to_dict()}
        for b in book_ids
    ]
    ds = Dataset()
    with ProcessPoolExecutor(max_workers=workers, max_tasks_per_child=1) as ex:
        for res in ex.map(examples_for_book, tasks):
            part = Dataset(x=res["x"], y=res["y"], book=res["book"], page=res["page"])
            ds.extend(part)
    return ds


# --------------------------------------------------------------- the model


@dataclass
class LogisticModel:
    """
    Weights over `FEATURE_NAMES`, and the threshold they are read at.

    Kept as a plain list so the whole model is a line of JSON: if this ever
    ships it ships beside the profile, and the detector gains numbers rather
    than a dependency.
    """
    weights: List[float]
    version: int = MODEL_VERSION
    feature_names: Tuple[str, ...] = FEATURE_NAMES

    def score(self, x: Sequence[float]) -> float:
        z = sum(w * v for w, v in zip(self.weights, x))
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        e = math.exp(z)
        return e / (1.0 + e)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "features": list(self.feature_names),
            "weights": [round(w, 6) for w in self.weights],
        }


def train_logistic(
    ds: Dataset,
    *,
    epochs: int = 400,
    lr: float = 0.5,
    l2: float = 1e-3,
    seed: int = 20260922,
    class_balance: bool = True,
) -> LogisticModel:
    """
    Plain batch gradient descent with L2, and positives reweighted to parity.

    Balancing matters here: on a sheet with one marked entry and six regions the
    positives are a sixth of the data, and an unbalanced fit would find that
    predicting "not interactive" for everything scores 86% and stop. The
    reweighting makes the model rank rather than abstain, which is the only use
    the labels support anyway.
    """
    n = len(ds)
    if n == 0:
        return LogisticModel(weights=[0.0] * N_FEATURES)
    rng = random.Random(seed)
    w = [rng.uniform(-0.01, 0.01) for _ in range(N_FEATURES)]

    pos = ds.positives()
    neg = n - pos
    w_pos = (n / (2 * pos)) if (class_balance and pos) else 1.0
    w_neg = (n / (2 * neg)) if (class_balance and neg) else 1.0

    for _ in range(epochs):
        grad = [0.0] * N_FEATURES
        for xi, yi in zip(ds.x, ds.y):
            z = sum(wj * v for wj, v in zip(w, xi))
            p = 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))
            weight = w_pos if yi else w_neg
            err = weight * (p - yi)
            for j, v in enumerate(xi):
                grad[j] += err * v
        for j in range(N_FEATURES):
            # The bias is not regularised: shrinking it would bias the whole
            # ranking toward the majority class, which is what the reweighting
            # above exists to undo.
            reg = 0.0 if j == 0 else l2 * w[j]
            w[j] -= lr * (grad[j] / n + reg)
    return LogisticModel(weights=w)


# ----------------------------------------------------------- the judgement


def auc(model: LogisticModel, ds: Dataset) -> Optional[float]:
    """
    Probability that a marked region outranks an unmarked one on the same corpus.

    Rank-based, so it says whether the ordering is informative without any
    threshold having been chosen -- which is the first thing to know, and often
    the last: a model at 0.5 has learned nothing and no threshold will rescue it.
    """
    pairs = sorted(zip((model.score(x) for x in ds.x), ds.y))
    pos = ds.positives()
    neg = len(ds) - pos
    if not pos or not neg:
        return None
    rank_sum = 0.0
    i = 0
    rank = 1
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg_rank = (rank + (rank + (j - i))) / 2
        for k in range(i, j + 1):
            if pairs[k][1] == 1:
                rank_sum += avg_rank
        rank += (j - i + 1)
        i = j + 1
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


def top1_accuracy(model: LogisticModel, ds: Dataset) -> Optional[float]:
    """
    On each sheet with exactly one marked entry, does the model rank it first?

    The use a model with these labels could honestly have is choosing between
    the candidates on one page, so this is the metric that corresponds to a
    decision the detector could actually take. It is reported beside AUC because
    a model can have a respectable AUC over the whole corpus and still lose the
    per-page contest that matters.
    """
    pages: Dict[Tuple[str, int], List[Tuple[float, int]]] = {}
    for x, y, b, p in zip(ds.x, ds.y, ds.book, ds.page):
        pages.setdefault((b, p), []).append((model.score(x), y))
    usable = [rows for rows in pages.values() if sum(y for _, y in rows) == 1 and len(rows) > 1]
    if not usable:
        return None
    hits = sum(1 for rows in usable if max(rows, key=lambda r: r[0])[1] == 1)
    return hits / len(usable)


def baseline_top1(ds: Dataset) -> Optional[float]:
    """
    The rules' own answer to the same question, so the model has something to beat.

    "The topmost region on the sheet" is the closest thing the detector has to a
    guess when it must choose one, and a learned scorer that cannot beat *that*
    has no claim on anybody's dependency budget.
    """
    y_centre = FEATURE_NAMES.index("y_centre")
    pages: Dict[Tuple[str, int], List[Tuple[float, int]]] = {}
    for x, y, b, p in zip(ds.x, ds.y, ds.book, ds.page):
        pages.setdefault((b, p), []).append((x[y_centre], y))
    usable = [rows for rows in pages.values() if sum(y for _, y in rows) == 1 and len(rows) > 1]
    if not usable:
        return None
    hits = sum(1 for rows in usable if max(rows, key=lambda r: r[0])[1] == 1)
    return hits / len(usable)


# -------------------------------------------------------------------- CLI


def main() -> int:
    import argparse
    from tools.hotspot_extraction.train import splits as splits_mod

    ap = argparse.ArgumentParser(
        description="Stage 4.4: train a candidate scorer and report whether it is worth shipping."
    )
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--profile", type=str, default=None,
                    help="Profile the candidates are generated under (default: the shipped one)")
    ap.add_argument("--epochs", type=int, default=400)
    ap.add_argument("--l2", type=float, default=1e-3)
    ap.add_argument("--json", type=str, help="Write the verdict here")
    args = ap.parse_args()

    profile = load_profile(args.profile) if args.profile else load_profile()
    train_ids = splits_mod.train_ids()
    held_ids = splits_mod.held_out_ids()

    print(f"Building the dataset under profile {profile.to_dict() and ''}"
          f"{__import__('tools.hotspot_extraction.scanner.profile', fromlist=['profile_hash']).profile_hash(profile)}")
    train_ds = build_dataset(train_ids, profile=profile, workers=args.workers)
    held_ds = build_dataset(held_ids, profile=profile, workers=args.workers)
    print(f"  train    {len(train_ds):6d} regions on manifest pages, {train_ds.positives()} marked "
          f"({train_ds.positives() / max(1, len(train_ds)):.1%})")
    print(f"  held-out {len(held_ds):6d} regions on manifest pages, {held_ds.positives()} marked "
          f"({held_ds.positives() / max(1, len(held_ds)):.1%})")

    model = train_logistic(train_ds, epochs=args.epochs, l2=args.l2)

    verdict = {
        "profileHash": __import__(
            "tools.hotspot_extraction.scanner.profile", fromlist=["profile_hash"]
        ).profile_hash(profile),
        "train": {
            "examples": len(train_ds), "positives": train_ds.positives(),
            "auc": auc(model, train_ds), "top1": top1_accuracy(model, train_ds),
            "baselineTop1": baseline_top1(train_ds),
        },
        "heldOut": {
            "examples": len(held_ds), "positives": held_ds.positives(),
            "auc": auc(model, held_ds), "top1": top1_accuracy(model, held_ds),
            "baselineTop1": baseline_top1(held_ds),
        },
        "model": model.to_dict(),
    }

    print("\n                      AUC     top-1    rules' top-1")
    for name in ("train", "heldOut"):
        r = verdict[name]
        fmt = lambda v: "   n/a" if v is None else f"{v:6.3f}"
        print(f"  {name:<10} {fmt(r['auc'])}  {fmt(r['top1'])}   {fmt(r['baselineTop1'])}")

    ho = verdict["heldOut"]
    margin = None
    if ho["top1"] is not None and ho["baselineTop1"] is not None:
        margin = ho["top1"] - ho["baselineTop1"]
        verdict["heldOutMargin"] = margin

    print("\n  strongest weights:")
    ranked = sorted(
        zip(FEATURE_NAMES, model.weights), key=lambda kv: -abs(kv[1])
    )[:10]
    for name, w in ranked:
        print(f"    {name:<20} {w:+.3f}")

    # The decision the plan asked for, stated rather than implied.
    print()
    if margin is None:
        print("  VERDICT: not measurable -- too few single-entry pages to compare on.")
    elif margin > 0.05:
        print(f"  VERDICT: the model beats the rules by {margin:+.1%} on held-out books. "
              f"Worth carrying -- as weights in JSON, so it still costs no dependency.")
    else:
        print(f"  VERDICT: the model does not beat the rules on held-out books "
              f"({margin:+.1%}). Keep the fitted rules; a learned scorer is not "
              f"earning its place on this corpus.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(verdict, f, indent=2)
            f.write("\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
