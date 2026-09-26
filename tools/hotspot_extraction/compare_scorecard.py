#!/usr/bin/env python3
"""
Scorecard Comparison Harness.

Grades the PyMuPDF scanner's bakes against the baseline recorded in
tools/hotspot_extraction/tests/scorecard.json.

Note that the baseline is a snapshot of the detector's own prior output, not
hand-verified ground truth, so "meets baseline" means "did not regress", never
"is correct".

Evaluates:
- Yield: Regions and sub-questions detected.
- Join: Match rate with publisher interactive items (kitapogeList).
- Violations: 0 overlapping hotspots, 0 slivers, minimized cut panels.

Produces a comprehensive comparison table and BENCHMARK_REPORT.md.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hotspot_extraction.scanner.score import score_catalogue  # noqa: E402


def run_scanner(workers: int = 4, force: bool = False) -> Tuple[float, Optional[float]]:
    """
    Run the Python batch scanner on all installed books.

    Returns (wall_time_seconds, peak_rss_mb) -- peak RSS is None when it could
    not be parsed, which the gate must treat as unmeasured rather than as 0.
    """
    cmd = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "tools", "hotspot_extraction", "scan.py"),
        "--all",
        "--workers", str(workers),
    ]
    if force:
        cmd.append("--force")

    t0 = time.time()
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    wall_time = time.time() - t0
    print(res.stdout)
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
        raise RuntimeError(f"scan.py failed with exit code {res.returncode}")

    # Parse peak RAM from scanner stdout. `scan.py` prints two lines --
    # "Peak total RAM (USS):" and "(RSS):" -- and the budget is about physical
    # pressure, so RSS is the one to grade. This used to grep for
    # "Peak total RAM:", which matches neither, so peak_ram stayed 0.0 and the
    # 500 MB budget could never fail.
    peak_uss: Optional[float] = None
    peak_rss: Optional[float] = None
    for line in res.stdout.splitlines():
        for needle, key in (("Peak total RAM (USS)", "uss"), ("Peak total RAM (RSS)", "rss")):
            if needle in line:
                try:
                    val = float(line.split(":", 1)[1].split()[0].strip())
                except (IndexError, ValueError):
                    continue
                if key == "uss":
                    peak_uss = val
                else:
                    peak_rss = val

    peak_ram = peak_rss if peak_rss is not None else peak_uss
    if peak_ram is None:
        print(
            "WARNING: could not parse peak RAM from scan.py output; "
            "the memory criterion will be reported as NOT MEASURED.",
            file=sys.stderr,
        )
    return wall_time, peak_ram


def run_scorecard(target_json: Optional[str] = None) -> Dict[str, Any]:
    """
    Score every bake with the in-process Python scorer.

    This used to shell out to `node tests/scorecard.mjs --from-bake`, which is
    how the quality gate came to depend on Node after the detector itself had
    been ported to Python -- and on a scorer that kept its own copies of
    `MIN_HOTSPOT`, `TALL_REGION` and `WHOLE_TOL`. `scanner.score` imports those
    from `scanner.regions`, so there is one definition of each.

    Verified against the JS on all 56 baked books: every yield, violation and
    stat is identical. The join differs on 15 books, always in the same
    direction (22 more entries matched), because `link_oges` keys its
    assignment by region *index* where `linkInteractiveOges` keys it by
    activity id -- so the second `a` on a two-column sheet overwrote the first
    there and its entry was then filed as `anchor-nogrow`. Bucket totals still
    sum to the manifest size in both.
    """
    data = score_catalogue(root=str(PROJECT_ROOT))
    if data.get("errors"):
        for err in data["errors"]:
            print(
                f"WARNING: could not score {err['bookId']}: {err['error']}",
                file=sys.stderr,
            )
    if target_json:
        with open(target_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def load_baseline() -> Dict[str, Any]:
    """
    Load baseline scorecard results from tools/hotspot_extraction/tests/scorecard.json.
    """
    baseline_path = os.path.join(
        PROJECT_ROOT, "tools", "hotspot_extraction", "tests", "scorecard.json"
    )
    if not os.path.isfile(baseline_path):
        raise FileNotFoundError(f"Baseline scorecard not found at: {baseline_path}")
    with open(baseline_path, "r", encoding="utf-8") as f:
        return json.load(f)


def format_delta(new_val: float, old_val: float, is_percentage: bool = False, lower_is_better: bool = False) -> str:
    diff = new_val - old_val
    if abs(diff) < 1e-4:
        return "="
    sign = "+" if diff > 0 else ""
    val_str = f"{sign}{diff:.1f}%" if is_percentage else f"{sign}{diff:g}"
    return val_str


# Thresholds the gate gets to fail on. Named, so a report says what it was held to.
MAX_WALL_SECONDS = 90.0
MAX_PEAK_RAM_MB = 500.0
AGGREGATE_MATCH_TOLERANCE = 0.5   # percentage points the aggregate may slip
PER_BOOK_MATCH_TOLERANCE = 2.0    # percentage points any single book may slip


def _mark(met: Optional[bool]) -> str:
    """PASS / FAIL / the third case: nothing was measured, which is not a pass."""
    if met is None:
        return "NOT MEASURED ✗"
    return "PASS ✓" if met else "FAIL ✗"


def _box(met: Optional[bool]) -> str:
    return "x" if met is True else " "


def generate_benchmark_report(
    installed_books: List[str],
    current_data: Dict[str, Any],
    baseline_data: Dict[str, Any],
    wall_time: Optional[float],
    peak_ram: Optional[float],
    output_path: str,
) -> Tuple[bool, str]:
    """
    Generate the markdown report and return (all_passed, markdown_text).

    Two things this deliberately does not do, because both used to turn a
    missing measurement into a passing grade:

    * It scores **every** book in the scorecard, not just the ones whose PDF
      happens to sit in `books/`. Those are the books the constants were tuned
      against, so grading only them reports the most favourable slice available
      and called it the result.
    * `wall_time` and `peak_ram` are `Optional`. `--skip-scan` measures neither,
      and a criterion with nothing behind it is reported as NOT MEASURED and
      **counts as a failure**, where it used to compare a default 0.0 against
      the budget and pass.
    """
    curr_map = {b["bookId"]: b for b in current_data.get("books", []) if "bookId" in b}
    base_map = {b["bookId"]: b for b in baseline_data.get("books", []) if "bookId" in b}

    installed_set = set(installed_books)
    target_ids = sorted(curr_map)
    scanned_ids = [b_id for b_id in target_ids if b_id in installed_set]

    total_pages = sum(curr_map[b_id].get("pages", 0) for b_id in target_ids)
    scanned_pages = sum(curr_map[b_id].get("pages", 0) for b_id in scanned_ids)

    rows: List[Dict[str, Any]] = []
    tot = {k: 0 for k in (
        "curr_regions", "base_regions", "curr_questions", "base_questions",
        "curr_matched", "base_matched", "oges", "overlaps", "slivers",
        "curr_p_cut", "base_p_cut", "curr_s_cut", "base_s_cut", "tall",
    )}
    # Why each manifest entry did not become a hotspot. This is the work queue:
    # a single match rate says how much is missing, the histogram says what to
    # fix first, and the fix order is bucket size.
    bucket_tot: Dict[str, int] = {}
    base_bucket_tot: Dict[str, int] = {}

    # Books that got worse. Aggregates hide these: one book collapsing from 84%
    # to 44% is invisible in a catalogue-wide mean, and did in fact ship.
    match_regressions: List[Tuple[str, float, float]] = []
    violation_regressions: List[Tuple[str, int, int]] = []

    for b_id in target_ids:
        c = curr_map[b_id]
        b = base_map.get(b_id, {})

        c_yield, b_yield = c.get("yield", {}), b.get("yield", {})
        c_join, b_join = c.get("join", {}), b.get("join", {})
        c_viol, b_viol = c.get("violations", {}), b.get("violations", {})

        c_reg, b_reg = c_yield.get("regions", 0), b_yield.get("regions", 0)
        c_q, b_q = c_yield.get("questions", 0), b_yield.get("questions", 0)
        c_match = (c_join.get("matchRate") or 0.0) * 100.0
        b_match = (b_join.get("matchRate") or 0.0) * 100.0
        oges = c_join.get("oges", 0)
        c_matched, b_matched = c_join.get("matched", 0), b_join.get("matched", 0)

        c_ov, c_sl = c_viol.get("overlaps", 0), c_viol.get("slivers", 0)
        c_tall = c_viol.get("tall", 0)
        c_p_cut, b_p_cut = c_viol.get("panelsCut", 0), b_viol.get("panelsCut", 0)
        c_s_cut, b_s_cut = c_viol.get("solutionsCut", 0), b_viol.get("solutionsCut", 0)

        # Correctness first: every violation rule counts, not just the two that
        # happen to be zero on the favourable slice.
        c_viol_sum = c_ov + c_sl + c_tall + c_p_cut + c_s_cut
        b_viol_sum = (
            b_viol.get("overlaps", 0) + b_viol.get("slivers", 0)
            + b_viol.get("tall", 0) + b_p_cut + b_s_cut
        )

        if b_id in base_map:
            if oges > 0 and c_match < b_match - PER_BOOK_MATCH_TOLERANCE:
                match_regressions.append((b_id, b_match, c_match))
            if c_viol_sum > b_viol_sum:
                violation_regressions.append((b_id, b_viol_sum, c_viol_sum))

        for name, n in (c.get("buckets") or {}).items():
            bucket_tot[name] = bucket_tot.get(name, 0) + n
        for name, n in (b.get("buckets") or {}).items():
            base_bucket_tot[name] = base_bucket_tot.get(name, 0) + n

        tot["curr_regions"] += c_reg
        tot["base_regions"] += b_reg
        tot["curr_questions"] += c_q
        tot["base_questions"] += b_q
        tot["curr_matched"] += c_matched
        tot["base_matched"] += b_matched
        tot["oges"] += oges
        tot["overlaps"] += c_ov
        tot["slivers"] += c_sl
        tot["tall"] += c_tall
        tot["curr_p_cut"] += c_p_cut
        tot["base_p_cut"] += b_p_cut
        tot["curr_s_cut"] += c_s_cut
        tot["base_s_cut"] += b_s_cut

        rows.append({
            "book_id": b_id,
            "short_id": b_id[:8],
            "installed": b_id in installed_set,
            "pages": c.get("pages", 0),
            "base_reg": b_reg, "curr_reg": c_reg,
            "base_q": b_q, "curr_q": c_q,
            "oges": oges,
            "base_match": b_match, "curr_match": c_match,
            "overlaps": c_ov, "slivers": c_sl, "tall": c_tall,
            "base_p_cut": b_p_cut, "curr_p_cut": c_p_cut,
            "base_s_cut": b_s_cut, "curr_s_cut": c_s_cut,
        })

    def _rate(matched: int, oges: int) -> float:
        return (matched / max(1, oges)) * 100.0

    overall_curr_match = _rate(tot["curr_matched"], tot["oges"])
    overall_base_match = _rate(tot["base_matched"], tot["oges"])

    inst_oges = sum(r["oges"] for r in rows if r["installed"])
    inst_matched = sum(
        curr_map[r["book_id"]].get("join", {}).get("matched", 0)
        for r in rows if r["installed"]
    )
    rest_oges = tot["oges"] - inst_oges
    rest_matched = tot["curr_matched"] - inst_matched

    total_cuts = tot["curr_p_cut"] + tot["curr_s_cut"]
    cut_share = total_cuts / max(1, tot["curr_regions"])

    # --- criteria -----------------------------------------------------------
    all_zero_overlaps = tot["overlaps"] == 0
    all_zero_slivers = tot["slivers"] == 0
    match_rate_met = overall_curr_match >= (overall_base_match - AGGREGATE_MATCH_TOLERANCE)
    no_book_regressed = not match_regressions
    no_book_gained_violations = not violation_regressions
    speed_met = None if wall_time is None else wall_time <= MAX_WALL_SECONDS
    memory_met = None if peak_ram is None else peak_ram <= MAX_PEAK_RAM_MB

    criteria = [
        ("Memory", memory_met,
         f"peak total RSS across workers under {MAX_PEAK_RAM_MB:.0f} MB"
         + (" — nothing measured (--skip-scan)" if peak_ram is None else f" ({peak_ram:.1f} MB)")),
        ("Speed", speed_met,
         f"batch finishes under {MAX_WALL_SECONDS:.0f}s"
         + (" — nothing measured (--skip-scan)" if wall_time is None else f" ({wall_time:.2f}s)")),
        ("Aggregate quality", match_rate_met,
         f"{overall_curr_match:.1f}% vs baseline {overall_base_match:.1f}%"),
        ("No per-book match regression", no_book_regressed,
         f"{len(match_regressions)} book(s) lost more than {PER_BOOK_MATCH_TOLERANCE:.0f} pp"),
        ("No per-book violation regression", no_book_gained_violations,
         f"{len(violation_regressions)} book(s) gained violations"),
        ("Zero overlaps", all_zero_overlaps, f"{tot['overlaps']} found"),
        ("Zero slivers", all_zero_slivers, f"{tot['slivers']} found"),
    ]
    all_passed = all(met is True for _, met, _ in criteria)

    pps = None if not wall_time else scanned_pages / wall_time

    base_scorer = baseline_data.get("scorer")
    curr_scorer = current_data.get("scorer")
    mixed_scorers = base_scorer != curr_scorer

    lines = [
        "# Hotspot Scanner Benchmark & Scorecard Report",
        "",
        "Generated by `tools/hotspot_extraction/compare_scorecard.py`. Every book in the",
        "scorecard is graded, not only the ones with a local PDF — see the `inst` column.",
        "",
    ]
    if mixed_scorers:
        lines.extend([
            f"> ⚠ **The baseline was recorded by a different scorer** "
            f"(`{base_scorer or 'legacy JS'}`) than the current numbers (`{curr_scorer}`). "
            f"The Python scorer matches the JS exactly on every yield, violation and stat, "
            f"but matches about 22 more manifest entries catalogue-wide, because the JS "
            f"join keyed its assignment by activity id and lost one of two same-lettered "
            f"activities per sheet. **Treat the match-rate deltas below as scorer + "
            f"algorithm combined until the baseline is re-recorded** with "
            f"`--write-baseline`.",
            "",
        ])
    lines.extend([
        "## Performance & Memory",
        "",
        f"- **Books graded**: {len(target_ids)} ({total_pages:,} pages); "
        f"**{len(scanned_ids)} with a local PDF** ({scanned_pages:,} pages)",
        f"- **Wall-Clock Time**: "
        + ("**not measured** (run without `--skip-scan` to measure)" if wall_time is None
           else f"**{wall_time:.2f}s**")
        + f" (Target: < {MAX_WALL_SECONDS:.0f}s — {_mark(speed_met)})",
        f"- **Processing Speed**: " + ("**not measured**" if pps is None else f"**{pps:.1f} pages/sec**"),
        f"- **Peak Total RAM across Workers**: "
        + ("**not measured**" if peak_ram is None else f"**{peak_ram:.1f} MB**")
        + f" (Budget: < {MAX_PEAK_RAM_MB:.0f} MB — {_mark(memory_met)})",
        "",
        "## Quality",
        "",
        f"- **Match rate, all {len(target_ids)} books**: **{overall_curr_match:.1f}%** "
        f"({tot['curr_matched']}/{tot['oges']}) — baseline {overall_base_match:.1f}%",
        f"- **Match rate, books with a local PDF**: {_rate(inst_matched, inst_oges):.1f}% "
        f"({inst_matched}/{inst_oges})",
        f"- **Match rate, books without one**: {_rate(rest_matched, rest_oges):.1f}% "
        f"({rest_matched}/{rest_oges})",
        f"- **Cut violations**: **{total_cuts:,}** across {tot['curr_regions']:,} regions "
        f"({cut_share:.2f} per region) — {tot['curr_p_cut']:,} panels, {tot['curr_s_cut']:,} "
        f"solutions. One region can cut several blocks, so this is a count of "
        f"violations, not of bad regions.",
        f"- **Overlaps**: {tot['overlaps']} · **Slivers**: {tot['slivers']} · "
        f"**Over-tall**: {tot['tall']}",
        "",
        "---",
        "",
        "## Book-by-book comparison (recorded baseline → current bake)",
        "",
        "| Book ID | inst | Pages | Regions | Questions | Match Rate | Overlaps | Slivers | Panels Cut | Solutions Cut |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])

    for r in rows:
        reg_str = f"{r['base_reg']} → **{r['curr_reg']}** ({format_delta(r['curr_reg'], r['base_reg'])})"
        q_str = f"{r['base_q']} → **{r['curr_q']}** ({format_delta(r['curr_q'], r['base_q'])})"
        match_str = (
            f"{r['base_match']:.0f}% → **{r['curr_match']:.0f}%** "
            f"({format_delta(r['curr_match'], r['base_match'], is_percentage=True)})"
        )
        bad = lambda n: f"**{n}**" if n == 0 else f"<span style='color:red;'>**{n}**</span>"
        lines.append(
            f"| `{r['short_id']}` | {'✓' if r['installed'] else ''} | {r['pages']} | "
            f"{reg_str} | {q_str} | {match_str} | {bad(r['overlaps'])} | {bad(r['slivers'])} | "
            f"{r['base_p_cut']} → {r['curr_p_cut']} | {r['base_s_cut']} → {r['curr_s_cut']} |"
        )

    lines.extend([
        "",
        "### Totals",
        "",
        f"- **Regions**: {tot['curr_regions']:,} (baseline {tot['base_regions']:,})",
        f"- **Sub-questions**: {tot['curr_questions']:,} (baseline {tot['base_questions']:,})",
        f"- **Panels cut**: {tot['curr_p_cut']:,} (baseline {tot['base_p_cut']:,})",
        f"- **Solutions cut**: {tot['curr_s_cut']:,} (baseline {tot['base_s_cut']:,})",
        "",
    ])

    if match_regressions:
        lines.extend([
            "### ⚠ Per-book match-rate regressions",
            "",
            "| Book | Baseline | Current | Δ |",
            "| :--- | :---: | :---: | :---: |",
        ])
        for b_id, was, now in sorted(match_regressions, key=lambda t: t[2] - t[1]):
            lines.append(f"| `{b_id[:8]}` | {was:.1f}% | **{now:.1f}%** | {now - was:+.1f} pp |")
        lines.append("")

    if violation_regressions:
        lines.extend([
            "### ⚠ Per-book violation regressions",
            "",
            "| Book | Baseline violations | Current |",
            "| :--- | :---: | :---: |",
        ])
        for b_id, was, now in sorted(violation_regressions, key=lambda t: t[1] - t[2]):
            lines.append(f"| `{b_id[:8]}` | {was:,} | **{now:,}** |")
        lines.append("")

    if bucket_tot:
        lines.extend([
            "---",
            "",
            "## Why the other entries did not become hotspots",
            "",
            "The denominator is the publisher's manifest, so a folio failure counts",
            "against the score instead of dropping out of it. Fix order is bucket size.",
            "",
            "| Bucket | Count | Share | Baseline |",
            "| :--- | ---: | ---: | ---: |",
        ])
        for name, n in sorted(bucket_tot.items(), key=lambda kv: -kv[1]):
            if not n:
                continue
            was = base_bucket_tot.get(name, 0)
            lines.append(
                f"| `{name}` | {n:,} | {n / max(1, tot['oges']):.1%} | {was:,} |"
            )
        lines.append("")

    lines.extend(["---", "", "## Acceptance criteria", ""])
    for name, met, detail in criteria:
        lines.append(f"- [{_box(met)}] **{name}**: {detail} — {_mark(met)}")
    lines.extend([
        "",
        f"**Final Verdict**: {'PASSED' if all_passed else 'FAILED'}",
        "",
    ])

    report_text = "\n".join(lines)
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    return all_passed, report_text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare scanner bakes against scorecard baseline."
    )
    parser.add_argument(
        "--skip-scan",
        action="store_true",
        help="Skip running scan.py and evaluate existing bakes in activities/books",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-bake in scan.py even if current",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of workers for scan.py (default: 4)",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help=(
            "Overwrite tests/scorecard.json with the current numbers, so later runs "
            "compare like against like. Do this deliberately: it discards the "
            "reference point every regression is measured from."
        ),
    )
    parser.add_argument(
        "--out-report",
        type=str,
        default=os.path.join(PROJECT_ROOT, "tools", "hotspot_extraction", "BENCHMARK_REPORT.md"),
        help="Path to output markdown report (default: tools/hotspot_extraction/BENCHMARK_REPORT.md)",
    )

    args = parser.parse_args()

    # Discover installed books in books/
    books_dir = os.path.join(PROJECT_ROOT, "books")
    installed_books: List[str] = []
    if os.path.isdir(books_dir):
        for entry in os.listdir(books_dir):
            if entry.lower().endswith(".pdf"):
                real_file = os.path.realpath(os.path.join(books_dir, entry))
                b_id = os.path.splitext(os.path.basename(real_file))[0]
                if b_id not in installed_books:
                    installed_books.append(b_id)

    print("=" * 72)
    print("SCORECARD COMPARISON & BENCHMARK HARNESS")
    print(f"Installed books found: {len(installed_books)}")
    print("=" * 72)

    # None, not 0.0: nothing has been measured yet, and a budget compared
    # against a default of zero is a criterion that cannot fail.
    wall_time: Optional[float] = None
    peak_ram: Optional[float] = None

    if not args.skip_scan:
        print("\n[1/3] Running Python PyMuPDF batch scanner...")
        wall_time, peak_ram = run_scanner(workers=args.workers, force=args.force)
    else:
        print("\n[1/3] Skipping scanner run (--skip-scan specified);")
        print("      speed and memory will be reported as NOT MEASURED, which fails the gate.")

    print("\n[2/3] Running scorecard harness on generated bakes...")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_scorecard = tmp.name

    try:
        current_scorecard = run_scorecard(tmp_scorecard)

        if args.write_baseline:
            baseline_path = os.path.join(
                PROJECT_ROOT, "tools", "hotspot_extraction", "tests", "scorecard.json"
            )
            with open(baseline_path, "w", encoding="utf-8") as f:
                json.dump(current_scorecard, f, ensure_ascii=False, indent=2)
            print(f"\nBaseline re-recorded: {baseline_path}")
            print(f"  scorer={current_scorecard.get('scorer')} "
                  f"books={len(current_scorecard.get('books', []))}")

        baseline_scorecard = load_baseline()

        print("\n[3/3] Analyzing results and generating benchmark report...")
        passed, report = generate_benchmark_report(
            installed_books=installed_books,
            current_data=current_scorecard,
            baseline_data=baseline_scorecard,
            wall_time=wall_time,
            peak_ram=peak_ram,
            output_path=args.out_report,
        )

        print("\n" + report)
        print(f"\nReport written to: {args.out_report}")

        return 0 if passed else 1

    finally:
        if os.path.exists(tmp_scorecard):
            os.remove(tmp_scorecard)


if __name__ == "__main__":
    sys.exit(main())
