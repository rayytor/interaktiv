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

**What is left out, and why.** A region grown from a manifest entry's icon, or
bound to one, is matched to that entry by construction: its positive label is
the manifest restated, and a model given it learns to read the flag rather than
the page (the first verdict put +2.20 on `is_anchored` for exactly that
reason). So the candidates are what the detector finds *without* the
publisher's help -- `detect_page(reconcile=False)` -- which is also the set a
ranker would have to choose among if it were ever used, and any anchored region
that still reaches the dataset is dropped and counted.

**What the model has to beat.** Not "the topmost region": the detector's own
choice. For every candidate the dataset carries its `link_oges` geometric cost
(`pair_cost`: drift plus weighted reach) to the nearest icon on the sheet, and
the baseline picks the cheapest. The label gate is left out of that baseline on
purpose -- with it the baseline *is* the join the labels come from and scores
100% by definition -- so it answers the question a ranker would actually face:
given where the icons hang, and nothing else, which region is it? It is also
reported on the *contested* sheets, where the two cheapest candidates are
within `CONTESTED` of each other and drift alone cannot tell them apart.

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

from interaktiv_core.linking import (
    UNLABELLED_DRIFT,
    apply_anchored_ids,
    link_oges,
    oge_view,
    pair_cost,
    region_view,
)
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

# 2: `is_anchored` removed, anchored regions out of the dataset.
MODEL_VERSION = 2

# Two candidates whose geometric costs are this close (in % of the sheet) are
# ones drift cannot separate: half the window an unlabelled entry is allowed.
CONTESTED = UNLABELLED_DRIFT / 2


# ------------------------------------------------------------- the dataset


@dataclass
class Dataset:
    x: List[List[float]] = field(default_factory=list)
    y: List[int] = field(default_factory=list)
    book: List[str] = field(default_factory=list)
    page: List[int] = field(default_factory=list)
    # `pair_cost` to the cheapest icon on the sheet; inf when none is placed.
    cost: List[float] = field(default_factory=list)
    # Anchored regions that reached the dataset and were dropped. Should be 0:
    # the replay runs without the publisher's anchors.
    anchored_dropped: int = 0

    def __len__(self) -> int:
        return len(self.y)

    def positives(self) -> int:
        return sum(self.y)

    def extend(self, other: "Dataset") -> None:
        self.x.extend(other.x)
        self.y.extend(other.y)
        self.book.extend(other.book)
        self.page.extend(other.page)
        self.cost.extend(other.cost)
        self.anchored_dropped += other.anchored_dropped


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
        # Without the publisher's anchors: the regions the detector finds on
        # its own, which are the only ones whose label is not a tautology.
        result = cache_mod.replay_page(sp, reconcile=False)
        if not result.activities:
            continue
        acts = [serialize_activity(a) for a in clean_page_activities(result.activities)]
        kept = [a for a in acts if not _is_anchored(a, sp.page_num)]
        ds.anchored_dropped += len(acts) - len(kept)
        if not kept:
            continue

        prim = sp.primitives
        ctx = page_context(prim, result.layout, result.panels, result.solutions, kept)

        matched, costs = _labels_and_costs(kept, page_oges, prim.width, prim.height)
        for i, act in enumerate(kept):
            ds.x.append(features_for(act, ctx, whole_tol=RULER.WHOLE_TOL))
            ds.y.append(1 if i in matched else 0)
            ds.book.append(book_id)
            ds.page.append(sp.page_num)
            ds.cost.append(costs[i])
        del result, ctx
    return {
        "book_id": book_id,
        "x": ds.x, "y": ds.y, "book": ds.book, "page": ds.page, "cost": ds.cost,
        "anchored_dropped": ds.anchored_dropped,
    }


def _is_anchored(activity: Dict[str, Any], page_num: int) -> bool:
    """Grown from an entry's icon, or bound to one: matched by construction."""
    return bool(
        activity.get("anchored")
        or activity.get("ogeId")
        or activity["id"].startswith(f"p{page_num}-oge-")
    )


def _labels_and_costs(
    activities: Sequence[Dict[str, Any]],
    page_oges: Sequence[Dict[str, Any]],
    page_w: float,
    page_h: float,
) -> Tuple[set, List[float]]:
    """
    Which regions answered a publisher entry, and each one's cost to the icons.

    The label is the reader's own join -- `link_oges`, then the construction
    claims of `apply_anchored_ids` -- so a positive here means what a matched
    entry means in the reader. The cost is `pair_cost` to the cheapest icon on
    the sheet that no region claims by construction, with no gate applied:
    what position alone says, which is the baseline a model must beat.
    """
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
    links = apply_anchored_ids(
        link_oges(region_views, oge_views, confidence=None), region_views, oge_views,
    )
    costs: List[float] = []
    for rv in region_views:
        c = [pair_cost(rv, ov) for ov in oge_views]
        c = [v for v in c if v is not None]
        costs.append(min(c) if c else math.inf)
    return set(links.keys()), costs


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
            part = Dataset(
                x=res["x"], y=res["y"], book=res["book"], page=res["page"],
                cost=res["cost"], anchored_dropped=res["anchored_dropped"],
            )
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


def _single_positive_pages(ds: Dataset) -> List[List[int]]:
    """Example indices per sheet, for sheets with one marked region among several."""
    pages: Dict[Tuple[str, int], List[int]] = {}
    for i, (b, p) in enumerate(zip(ds.book, ds.page)):
        pages.setdefault((b, p), []).append(i)
    return [
        rows for rows in pages.values()
        if len(rows) > 1 and sum(ds.y[i] for i in rows) == 1
    ]


def contested(ds: Dataset, rows: Sequence[int]) -> bool:
    """Drift cannot separate this sheet: its two cheapest candidates are within `CONTESTED`."""
    costs = sorted(ds.cost[i] for i in rows)
    return len(costs) > 1 and costs[1] - costs[0] <= CONTESTED


def _top1(ds: Dataset, pick, only_contested: bool = False) -> Optional[float]:
    usable = _single_positive_pages(ds)
    if only_contested:
        usable = [rows for rows in usable if contested(ds, rows)]
    if not usable:
        return None
    return sum(1 for rows in usable if ds.y[pick(rows)] == 1) / len(usable)


def top1_accuracy(model: LogisticModel, ds: Dataset, *, only_contested: bool = False) -> Optional[float]:
    """
    On each sheet with exactly one marked entry, does the model rank it first?

    The use a model with these labels could honestly have is choosing between
    the candidates on one page, so this is the metric that corresponds to a
    decision the detector could actually take. It is reported beside AUC because
    a model can have a respectable AUC over the whole corpus and still lose the
    per-page contest that matters.
    """
    return _top1(ds, lambda rows: max(rows, key=lambda i: model.score(ds.x[i])), only_contested)


def baseline_top1(ds: Dataset, *, only_contested: bool = False) -> Optional[float]:
    """
    The detector's own answer to the same question, so the model has something to beat.

    The candidate with the cheapest `pair_cost` to an icon on the sheet -- the
    geometry `link_oges` settles pairs by. A learned scorer that cannot beat
    *that* has no claim on anybody's dependency budget.
    """
    return _top1(ds, lambda rows: min(rows, key=lambda i: ds.cost[i]), only_contested)


def usable_pages(ds: Dataset, *, only_contested: bool = False) -> int:
    usable = _single_positive_pages(ds)
    if only_contested:
        usable = [rows for rows in usable if contested(ds, rows)]
    return len(usable)


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
    for name, d in (("train", train_ds), ("held-out", held_ds)):
        print(f"  {name:<8} {len(d):6d} regions on manifest pages, {d.positives()} marked "
              f"({d.positives() / max(1, len(d)):.1%}); {d.anchored_dropped} anchored dropped")

    model = train_logistic(train_ds, epochs=args.epochs, l2=args.l2)

    def judged(d: Dataset) -> Dict[str, Any]:
        return {
            "examples": len(d), "positives": d.positives(),
            "anchoredDropped": d.anchored_dropped,
            "auc": auc(model, d),
            "pages": usable_pages(d),
            "top1": top1_accuracy(model, d),
            "baselineTop1": baseline_top1(d),
            "contestedPages": usable_pages(d, only_contested=True),
            "contestedTop1": top1_accuracy(model, d, only_contested=True),
            "contestedBaselineTop1": baseline_top1(d, only_contested=True),
        }

    verdict = {
        "profileHash": __import__(
            "tools.hotspot_extraction.scanner.profile", fromlist=["profile_hash"]
        ).profile_hash(profile),
        "modelVersion": MODEL_VERSION,
        "baseline": "link_oges pair_cost (drift + reach) to the cheapest icon on the sheet, no label gate",
        "contestedWithin": CONTESTED,
        "train": judged(train_ds),
        "heldOut": judged(held_ds),
        "model": model.to_dict(),
    }

    fmt = lambda v: "   n/a" if v is None else f"{v:6.3f}"
    print("\n                      AUC     top-1   drift top-1  pages | contested: top-1  drift  pages")
    for name in ("train", "heldOut"):
        r = verdict[name]
        print(f"  {name:<10} {fmt(r['auc'])}  {fmt(r['top1'])}   {fmt(r['baselineTop1'])}   {r['pages']:5d} |"
              f"            {fmt(r['contestedTop1'])} {fmt(r['contestedBaselineTop1'])}  {r['contestedPages']:5d}")

    ho = verdict["heldOut"]
    margin = None
    if ho["top1"] is not None and ho["baselineTop1"] is not None:
        margin = ho["top1"] - ho["baselineTop1"]
        verdict["heldOutMargin"] = margin
    if ho["contestedTop1"] is not None and ho["contestedBaselineTop1"] is not None:
        verdict["heldOutContestedMargin"] = ho["contestedTop1"] - ho["contestedBaselineTop1"]

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
    cm = verdict.get("heldOutContestedMargin")
    if cm is not None:
        print(f"  On the {ho['contestedPages']} held-out sheets drift cannot separate "
              f"(within {CONTESTED:g}%): model {cm:+.1%} against drift.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(verdict, f, indent=2)
            f.write("\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
