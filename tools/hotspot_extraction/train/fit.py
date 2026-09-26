"""
Fitting the detector's constants, and saying which of them mattered.

The objective is deterministic -- the same profile over the same cache gives the
same loss every time -- but it is not smooth. Most of these constants are
thresholds inside an `if`, so the loss is a step function of each one: flat over
a range, then a jump when some page's block falls on the other side of a
comparison. Gradients do not exist and finite differences mostly return zero.

That shapes the whole approach:

1. **Screen.** Move each knob alone, a few steps either way, and record the
   largest change in loss it can produce. Most of the seventy turn out to be
   inert on this corpus, and knowing which is worth having on its own -- an
   inert constant is one nobody needs to argue about.
2. **Coordinate descent** over the knobs that moved, on a coarse grid, which is
   what a piecewise-constant objective actually admits. It cannot exploit
   interactions, but it cannot be fooled by a flat neighbourhood either.
3. **Separable CMA-ES** over the same subset, to pick up the interactions
   coordinate descent structurally cannot see.

The optimizer is a hundred lines of arithmetic over Python lists. The plan said
numpy, and numpy would be reasonable, but the vector here has at most a few
dozen entries against an objective that costs seconds -- the linear algebra is
free either way, and this keeps the training path at zero dependencies, which
matters more on a machine where the shipped detector already has only two.

**What this never does** is look at the held-out books. They are scored once, by
`--final`, after everything is decided. A number that improved only on the
training split is not an improvement, and the only way to keep that honest is
for the fitting loop to be unable to see the other half.
"""

from dataclasses import dataclass, field, asdict
import json
import math
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.hotspot_extraction.scanner.profile import (
    DEFAULTS,
    SPEC,
    TUNABLE,
    BY_KEY,
    Knob,
    Profile,
    changed_from_default,
    load_profile,
    profile_hash,
    save_profile,
)
from tools.hotspot_extraction.train import splits as splits_mod
from tools.hotspot_extraction.train.objective import (
    ChunkPool,
    DEFAULT_WEIGHTS,
    Evaluation,
    Weights,
)

# ------------------------------------------------------------- the encoding
#
# Every knob is searched on [0, 1] rather than in its own units, so one step
# size means the same thing to `PAD` (0-32 pt) as to `WHOLE_TOL`-scale ratios.
# Integers round on the way out, which makes the encoded space of an integer
# knob a staircase -- correct, and the reason the optimizers below are all
# rank-based rather than gradient-based.


def encode(profile: Profile, knobs: Sequence[Knob]) -> List[float]:
    return [
        (getattr(profile, k.key) - k.lo) / (k.hi - k.lo) if k.hi > k.lo else 0.0
        for k in knobs
    ]


def decode(base: Profile, knobs: Sequence[Knob], x: Sequence[float]) -> Profile:
    changes = {}
    for k, u in zip(knobs, x):
        u = min(1.0, max(0.0, u))
        changes[k.key] = k.clamp(k.lo + u * (k.hi - k.lo))
    return base.replace(**changes)


# ------------------------------------------------------------- bookkeeping


@dataclass
class Trial:
    """One evaluated candidate, kept so a run can be read afterwards."""
    n: int
    loss: float
    profile_hash: str
    changed: Dict[str, float]
    totals: Dict[str, Any]
    phase: str
    seconds: float


@dataclass
class Run:
    """
    The record of a fitting run: every candidate, and the best so far.

    Written out after every improvement, so a run that is interrupted -- or that
    the user stops, which on a four-hour job is likely -- leaves behind the best
    profile it had found rather than nothing.
    """
    started: float = field(default_factory=time.time)
    evaluations: int = 0
    best_loss: float = float("inf")
    best: Optional[Profile] = None
    baseline_loss: Optional[float] = None
    baseline_totals: Optional[Dict[str, Any]] = None
    best_totals: Optional[Dict[str, Any]] = None
    trials: List[Trial] = field(default_factory=list)
    screen: List[Dict[str, Any]] = field(default_factory=list)
    log_path: Optional[str] = None

    def note(self, phase: str, profile: Profile, ev: Evaluation, seconds: float) -> bool:
        self.evaluations += 1
        improved = ev.loss < self.best_loss - 1e-12
        self.trials.append(Trial(
            n=self.evaluations,
            loss=round(ev.loss, 6),
            profile_hash=ev.profile_hash,
            changed={k: v[1] for k, v in changed_from_default(profile).items()},
            totals=ev.totals(),
            phase=phase,
            seconds=round(seconds, 2),
        ))
        if improved:
            self.best_loss = ev.loss
            self.best = profile
            self.best_totals = ev.totals()
            self.save()
        return improved

    def save(self) -> None:
        if not self.log_path:
            return
        body = {
            "startedAt": self.started,
            "elapsed": round(time.time() - self.started, 1),
            "evaluations": self.evaluations,
            "baselineLoss": self.baseline_loss,
            "baselineTotals": self.baseline_totals,
            "bestLoss": self.best_loss if self.best_loss < float("inf") else None,
            "bestTotals": self.best_totals,
            "bestProfileHash": profile_hash(self.best) if self.best else None,
            "bestChanged": {k: v[1] for k, v in changed_from_default(self.best).items()} if self.best else {},
            "screen": self.screen,
            "trials": [asdict(t) for t in self.trials[-400:]],
        }
        tmp = self.log_path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f, indent=2)
            f.write("\n")
        os.replace(tmp, self.log_path)


class Budget:
    """A wall-clock and evaluation ceiling, checked between candidates."""

    def __init__(self, seconds: Optional[float] = None, evaluations: Optional[int] = None):
        self.deadline = time.time() + seconds if seconds else None
        self.max_evals = evaluations
        self.used = 0

    def spent(self) -> bool:
        if self.deadline and time.time() >= self.deadline:
            return True
        if self.max_evals and self.used >= self.max_evals:
            return True
        return False

    def charge(self) -> None:
        self.used += 1

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.time()) if self.deadline else float("inf")


# ---------------------------------------------------------------- phase 1


def screen(
    pool: ChunkPool,
    base: Profile,
    books: Sequence[str],
    run: Run,
    budget: Budget,
    *,
    weights: Weights = DEFAULT_WEIGHTS,
    probes: Sequence[float] = (0.25, 0.75),
    verbose: bool = True,
) -> List[Knob]:
    """
    Which knobs can move the loss at all, ranked by how much.

    Two probes per knob, at a quarter and three quarters of its declared range,
    which is coarse on purpose: the question here is "does this constant do
    anything on this corpus", not "what is its best value". A knob whose loss is
    identical at both ends of its range is inert, and spending the fitting
    budget on it would be spending it on nothing.

    The ranking is the interesting output even when the fit that follows fails.
    """
    movers: List[Tuple[float, Knob]] = []
    for i, knob in enumerate(TUNABLE, 1):
        if budget.spent():
            break
        deltas = []
        for u in probes:
            candidate = base.replace(**{knob.key: knob.lo + u * (knob.hi - knob.lo)})
            if getattr(candidate, knob.key) == getattr(base, knob.key):
                continue
            t0 = time.time()
            ev = pool.evaluate(candidate, books, weights=weights)
            budget.charge()
            run.note("screen", candidate, ev, time.time() - t0)
            deltas.append(ev.loss - (run.baseline_loss or 0.0))
        span = max((abs(d) for d in deltas), default=0.0)
        best_delta = min(deltas) if deltas else 0.0
        run.screen.append({
            "knob": knob.key,
            "module": knob.module,
            "span": round(span, 6),
            "bestDelta": round(best_delta, 6),
        })
        if span > 1e-9:
            movers.append((span, knob))
        if verbose:
            mark = "·" if span <= 1e-9 else ("↓" if best_delta < -1e-9 else "↕")
            print(f"  [{i:2d}/{len(TUNABLE)}] {mark} {knob.key:<26} span {span:+.4f}  best {best_delta:+.4f}")
    run.save()
    movers.sort(key=lambda sk: -sk[0])
    return [k for _, k in movers]


# ---------------------------------------------------------------- phase 2


def coordinate_descent(
    pool: ChunkPool,
    base: Profile,
    books: Sequence[str],
    knobs: Sequence[Knob],
    run: Run,
    budget: Budget,
    *,
    weights: Weights = DEFAULT_WEIGHTS,
    points: int = 7,
    passes: int = 3,
    verbose: bool = True,
) -> Profile:
    """
    Walk one knob at a time over a coarse grid, keeping whatever helps.

    Robust to a step-shaped objective in the way a gradient method is not: it
    asks only "is this value better than that one", which is the only question
    a piecewise-constant loss can answer. Knobs are visited in the order the
    screen ranked them, so if the budget runs out it runs out on the ones that
    matter least.
    """
    current = base
    for p in range(1, passes + 1):
        improved_any = False
        for knob in knobs:
            if budget.spent():
                return current
            now = getattr(current, knob.key)
            step = (knob.hi - knob.lo) / (points - 1)
            coarse = [knob.lo + step * i for i in range(points)]
            seen = {now}

            def try_values(values: Sequence[float], current_value: float) -> Tuple[Profile, float, bool]:
                """Evaluate a few settings of this one knob, keeping any that helps."""
                nonlocal current
                moved = False
                for raw in values:
                    value = knob.clamp(raw)
                    if value in seen or budget.spent():
                        continue
                    seen.add(value)
                    candidate = current.replace(**{knob.key: value})
                    t0 = time.time()
                    ev = pool.evaluate(candidate, books, weights=weights)
                    budget.charge()
                    if run.note("descent", candidate, ev, time.time() - t0):
                        if verbose:
                            print(f"  pass {p}  {knob.key} {current_value} -> {value}   loss {ev.loss:.5f}")
                        current = candidate
                        current_value = value
                        moved = True
                return current, current_value, moved

            current, now, moved_coarse = try_values(coarse, now)
            # Refine around wherever the coarse sweep left this knob, not around
            # where it started: on the second pass the interesting question is
            # local, and halving the step from a stale centre asks it of the
            # wrong place.
            current, now, moved_fine = try_values([now - step / 2, now + step / 2], now)
            improved_any = improved_any or moved_coarse or moved_fine
        if not improved_any:
            if verbose:
                print(f"  pass {p}: no knob improved; stopping early")
            break
    return current


# ---------------------------------------------------------------- phase 3


def sep_cmaes(
    pool: ChunkPool,
    base: Profile,
    books: Sequence[str],
    knobs: Sequence[Knob],
    run: Run,
    budget: Budget,
    *,
    weights: Weights = DEFAULT_WEIGHTS,
    sigma0: float = 0.2,
    seed: int = 20260922,
    verbose: bool = True,
) -> Profile:
    """
    Separable CMA-ES: the interactions coordinate descent cannot see, cheaply.

    The separable variant keeps a diagonal covariance instead of a full one, so
    there is no eigendecomposition and the whole update is a few loops over a
    list. That is not only a convenience: with a few dozen parameters and a
    handful of hundred evaluations there is not enough data to estimate a full
    covariance anyway, and pretending otherwise would fit noise in the step
    sizes rather than structure in the problem.

    Rank-based throughout -- it uses only the *order* of the candidates' losses,
    never their differences -- which is the property that makes it suited to an
    objective made of thresholds, where a difference can be zero across a wide
    plateau and then large in one step.
    """
    n = len(knobs)
    if n == 0:
        return base
    rng = random.Random(seed)

    m = encode(base, knobs)
    sigma = sigma0
    d = [1.0] * n           # per-coordinate standard deviation
    p_sigma = [0.0] * n
    p_c = [0.0] * n

    lam = 4 + int(3 * math.log(n))
    mu = lam // 2
    raw_w = [math.log(mu + 0.5) - math.log(i + 1) for i in range(mu)]
    total_w = sum(raw_w)
    w = [x / total_w for x in raw_w]
    mu_eff = 1.0 / sum(x * x for x in w)

    c_sigma = (mu_eff + 2) / (n + mu_eff + 5)
    d_sigma = 1 + 2 * max(0.0, math.sqrt((mu_eff - 1) / (n + 1)) - 1) + c_sigma
    c_c = (4 + mu_eff / n) / (n + 4 + 2 * mu_eff / n)
    # The separable variant's learning rates: the (n + 2) / 3 factor is what
    # makes a diagonal model learn faster than a full one would.
    c_1 = 2.0 / ((n + 1.3) ** 2 + mu_eff) * (n + 2) / 3
    c_mu = min(1 - c_1, 2 * (mu_eff - 2 + 1 / mu_eff) / ((n + 2) ** 2 + mu_eff)) * (n + 2) / 3
    chi_n = math.sqrt(n) * (1 - 1 / (4 * n) + 1 / (21 * n * n))

    best = base
    best_loss = run.best_loss
    generation = 0

    while not budget.spent():
        generation += 1
        population: List[Tuple[float, List[float], List[float]]] = []
        for _ in range(lam):
            if budget.spent():
                break
            z = [rng.gauss(0.0, 1.0) for _ in range(n)]
            x = [min(1.0, max(0.0, m[i] + sigma * d[i] * z[i])) for i in range(n)]
            candidate = decode(base, knobs, x)
            t0 = time.time()
            ev = pool.evaluate(candidate, books, weights=weights)
            budget.charge()
            run.note("cmaes", candidate, ev, time.time() - t0)
            population.append((ev.loss, x, z))
            if ev.loss < best_loss - 1e-12:
                best_loss, best = ev.loss, candidate
        if len(population) < mu:
            break

        population.sort(key=lambda t: t[0])
        old_m = list(m)
        m = [sum(w[k] * population[k][1][i] for k in range(mu)) for i in range(n)]
        # The realised step, in the sampling distribution's own units.
        y = [(m[i] - old_m[i]) / (sigma * d[i]) if d[i] > 0 else 0.0 for i in range(n)]

        p_sigma = [
            (1 - c_sigma) * p_sigma[i] + math.sqrt(c_sigma * (2 - c_sigma) * mu_eff) * y[i]
            for i in range(n)
        ]
        norm_ps = math.sqrt(sum(v * v for v in p_sigma))
        h_sigma = 1.0 if norm_ps / math.sqrt(1 - (1 - c_sigma) ** (2 * generation)) < (1.4 + 2 / (n + 1)) * chi_n else 0.0
        p_c = [
            (1 - c_c) * p_c[i] + h_sigma * math.sqrt(c_c * (2 - c_c) * mu_eff) * y[i] * d[i]
            for i in range(n)
        ]

        for i in range(n):
            var = d[i] * d[i]
            rank_one = p_c[i] * p_c[i]
            rank_mu = sum(
                w[k] * ((population[k][1][i] - old_m[i]) / sigma) ** 2
                for k in range(mu)
            )
            var = (1 - c_1 - c_mu) * var + c_1 * rank_one + c_mu * rank_mu
            d[i] = math.sqrt(max(var, 1e-12))
        sigma *= math.exp((c_sigma / d_sigma) * (norm_ps / chi_n - 1))
        sigma = min(1.0, max(1e-4, sigma))

        if verbose:
            print(f"  gen {generation:3d}  best {best_loss:.5f}  sigma {sigma:.4f}  evals {run.evaluations}")
        if sigma < 1e-3:
            if verbose:
                print("  step size collapsed; converged")
            break

    return best


# -------------------------------------------------------------------- CLI


def _report(title: str, ev_totals: Dict[str, Any]) -> str:
    t = ev_totals
    return (
        f"{title:<10} loss {t['loss']:+.5f} | cuts {t['cuts']} ({t['cutPerRegion']:.4f}/region) "
        f"| overlaps {t['overlaps']} | tall {t['tall']} | slivers {t['slivers']} "
        f"| match {t['matchRate']} | coverage {t['coverage']} | {t['regions']} regions"
    )


def _compare(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    def delta(key: str, fmt: str = "d") -> str:
        a, b = before.get(key), after.get(key)
        if a is None or b is None:
            return "n/a"
        return f"{a} -> {b}" + (f" ({b - a:+{fmt}})" if isinstance(a, (int, float)) else "")
    return (
        f"    cuts      {delta('cuts')}\n"
        f"    overlaps  {delta('overlaps')}\n"
        f"    tall      {delta('tall')}\n"
        f"    slivers   {delta('slivers')}\n"
        f"    match     {before.get('matchRate')} -> {after.get('matchRate')}\n"
        f"    coverage  {before.get('coverage')} -> {after.get('coverage')}\n"
        f"    regions   {delta('regions')}"
    )


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Fit the detector's constants against the cached corpus.")
    ap.add_argument("--screen", action="store_true", help="Rank the knobs by how much they can move the loss, and stop")
    ap.add_argument("--fit", action="store_true", help="Screen, then coordinate descent, then separable CMA-ES")
    ap.add_argument("--final", action="store_true", help="Score a profile on train and held-out, and report both")
    ap.add_argument("--minutes", type=float, default=90.0, help="Wall-clock budget for the fitting phases")
    ap.add_argument("--max-evals", type=int, default=None, help="Evaluation ceiling, if tighter than the clock")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--chunk-pages", type=int, default=40)
    ap.add_argument("--profile", type=str, default=None, help="Profile to start from (default: the shipped one)")
    ap.add_argument("--out", type=str, default=None, help="Where to write the fitted profile")
    ap.add_argument("--log", type=str, default=None, help="Where to write the run log")
    ap.add_argument("--seed", type=int, default=20260922)
    args = ap.parse_args()

    if not (args.screen or args.fit or args.final):
        ap.error("nothing to do: pass --screen, --fit or --final")

    base = load_profile(args.profile) if args.profile else load_profile()
    train = splits_mod.train_ids()
    held_out = splits_mod.held_out_ids()
    splits_mod.assert_trainable(train)

    run = Run(log_path=args.log)
    budget = Budget(seconds=args.minutes * 60 if (args.screen or args.fit) else None,
                    evaluations=args.max_evals)

    with ChunkPool(workers=args.workers, chunk_pages=args.chunk_pages) as pool:
        if args.screen or args.fit:
            print(f"Baseline on {len(train)} training books "
                  f"({sum(c.pages for c in pool.chunks_for(train))} pages, "
                  f"{len(pool.chunks_for(train))} chunks, {pool.workers} workers)")
            t0 = time.time()
            base_ev = pool.evaluate(base, train)
            per_eval = time.time() - t0
            run.baseline_loss = base_ev.loss
            run.baseline_totals = base_ev.totals()
            run.best_loss = base_ev.loss
            run.best = base
            run.best_totals = base_ev.totals()
            run.save()
            print("  " + _report("baseline", base_ev.totals()))
            print(f"  one evaluation: {per_eval:.2f}s -> about "
                  f"{int(budget.remaining_seconds() / max(per_eval, 1e-6))} candidates in the budget\n")

            print("Screening every tunable knob:")
            movers = screen(pool, base, train, run, budget)
            print(f"\n  {len(movers)} of {len(TUNABLE)} knobs moved the loss; "
                  f"{len(TUNABLE) - len(movers)} are inert on this corpus")
            if movers:
                print("  strongest: " + ", ".join(k.key for k in movers[:10]))

        if args.fit and not budget.spent():
            print("\nCoordinate descent over the movers:")
            best = coordinate_descent(pool, run.best or base, train, movers, run, budget)
            print(f"  after descent: loss {run.best_loss:.5f} "
                  f"({len(changed_from_default(run.best or base))} knobs moved)")

            if not budget.spent() and movers:
                print("\nSeparable CMA-ES over the same knobs:")
                sep_cmaes(pool, run.best or best, train, movers, run, budget, seed=args.seed)
            print(f"\n  after CMA-ES: loss {run.best_loss:.5f}")

        if (args.screen or args.fit):
            print(f"\n{run.evaluations} evaluations in {time.time() - run.started:.0f}s")
            if run.best is not None and run.best_totals and run.baseline_totals:
                print("\nTraining split, baseline -> fitted:")
                print(_compare(run.baseline_totals, run.best_totals))
            if args.out and run.best is not None and run.best_loss < (run.baseline_loss or float("inf")):
                h = save_profile(run.best, args.out,
                                 note=f"fitted {time.strftime('%Y-%m-%d')} on {len(train)} training books")
                print(f"\nwrote {args.out}  (profile {h}, "
                      f"{len(changed_from_default(run.best))} knobs moved)")
            elif args.out:
                print("\nnothing beat the baseline; no profile written")

        if args.final:
            # The only place the held-out books are ever scored. Reported beside
            # the training split, because a gain that appears on one and not the
            # other is not a gain.
            target = run.best if (args.fit and run.best is not None) else base
            print(f"\nFinal scoring of profile {profile_hash(target)}:")
            tr = pool.evaluate(target, train)
            ho = pool.evaluate(target, held_out)
            print("  " + _report("train", tr.totals()))
            print("  " + _report("held-out", ho.totals()))
            if target != DEFAULTS:
                d_tr = pool.evaluate(DEFAULTS, train)
                d_ho = pool.evaluate(DEFAULTS, held_out)
                print("\n  against the shipped defaults:")
                print("  " + _report("train d", d_tr.totals()))
                print("  " + _report("held d", d_ho.totals()))
                print("\n  Training split:")
                print(_compare(d_tr.totals(), tr.totals()))
                print("\n  Held-out split:")
                print(_compare(d_ho.totals(), ho.totals()))
            if args.log:
                run.log_path = args.log
                run.save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
