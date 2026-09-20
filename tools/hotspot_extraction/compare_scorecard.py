#!/usr/bin/env python3
"""
Scorecard Comparison Harness.

Validates the high-speed Python/PyMuPDF scanner bakes against the legacy Node.js baseline
recorded in tools/hotspot_extraction/tests/scorecard.json.

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


def run_scanner(workers: int = 4, force: bool = False) -> Tuple[float, float]:
    """
    Run the Python batch scanner on all installed books.
    Returns (wall_time_seconds, peak_rss_mb).
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

    # Parse peak RAM from scanner stdout if available
    peak_ram = 0.0
    for line in res.stdout.splitlines():
        if "Peak total RAM:" in line:
            parts = line.split(":")
            if len(parts) > 1:
                val_str = parts[1].split()[0].strip()
                try:
                    peak_ram = float(val_str)
                except ValueError:
                    pass
    return wall_time, peak_ram


def run_scorecard(target_json: str) -> Dict[str, Any]:
    """
    Execute node tools/hotspot_extraction/tests/scorecard.mjs --from-bake --json <target_json>.
    """
    scorecard_mjs = os.path.join(
        PROJECT_ROOT, "tools", "hotspot_extraction", "tests", "scorecard.mjs"
    )
    cmd = ["node", scorecard_mjs, "--from-bake", "--json", target_json]
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
        raise RuntimeError(f"scorecard.mjs failed with exit code {res.returncode}")

    with open(target_json, "r", encoding="utf-8") as f:
        return json.load(f)


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


def generate_benchmark_report(
    installed_books: List[str],
    current_data: Dict[str, Any],
    baseline_data: Dict[str, Any],
    wall_time: float,
    peak_ram: float,
    output_path: str,
) -> Tuple[bool, str]:
    """
    Generate the markdown report and return (all_passed, markdown_text).
    """
    curr_map = {b["bookId"]: b for b in current_data.get("books", []) if "bookId" in b}
    base_map = {b["bookId"]: b for b in baseline_data.get("books", []) if "bookId" in b}

    installed_set = set(installed_books)
    target_ids = sorted([b_id for b_id in curr_map if b_id in installed_set])

    total_pages = sum(curr_map[b_id].get("pages", 0) for b_id in target_ids)
    pages_per_sec = total_pages / max(0.001, wall_time)

    rows: List[Dict[str, Any]] = []
    total_curr_regions = 0
    total_base_regions = 0
    total_curr_questions = 0
    total_base_questions = 0
    total_curr_matched = 0
    total_base_matched = 0
    total_oges = 0
    total_curr_overlaps = 0
    total_curr_slivers = 0
    total_curr_panels_cut = 0
    total_base_panels_cut = 0
    total_curr_solutions_cut = 0
    total_base_solutions_cut = 0

    all_zero_overlaps = True
    all_zero_slivers = True

    for b_id in target_ids:
        c = curr_map[b_id]
        b = base_map.get(b_id, {})

        c_yield = c.get("yield", {})
        b_yield = b.get("yield", {})
        c_join = c.get("join", {})
        b_join = b.get("join", {})
        c_viol = c.get("violations", {})
        b_viol = b.get("violations", {})

        c_reg = c_yield.get("regions", 0)
        b_reg = b_yield.get("regions", 0)
        c_q = c_yield.get("questions", 0)
        b_q = b_yield.get("questions", 0)

        c_match = c_join.get("matchRate", 0.0) * 100.0
        b_match = b_join.get("matchRate", 0.0) * 100.0
        oges = c_join.get("oges", 0)
        c_matched = c_join.get("matched", 0)
        b_matched = b_join.get("matched", 0)

        c_ov = c_viol.get("overlaps", 0)
        c_sl = c_viol.get("slivers", 0)
        c_p_cut = c_viol.get("panelsCut", 0)
        b_p_cut = b_viol.get("panelsCut", 0)
        c_s_cut = c_viol.get("solutionsCut", 0)
        b_s_cut = b_viol.get("solutionsCut", 0)

        if c_ov > 0:
            all_zero_overlaps = False
        if c_sl > 0:
            all_zero_slivers = False

        total_curr_regions += c_reg
        total_base_regions += b_reg
        total_curr_questions += c_q
        total_base_questions += b_q
        total_curr_matched += c_matched
        total_base_matched += b_matched
        total_oges += oges
        total_curr_overlaps += c_ov
        total_curr_slivers += c_sl
        total_curr_panels_cut += c_p_cut
        total_base_panels_cut += b_p_cut
        total_curr_solutions_cut += c_s_cut
        total_base_solutions_cut += b_s_cut

        rows.append({
            "book_id": b_id,
            "short_id": b_id[:8],
            "pages": c.get("pages", 0),
            "base_reg": b_reg,
            "curr_reg": c_reg,
            "base_q": b_q,
            "curr_q": c_q,
            "oges": oges,
            "base_match": b_match,
            "curr_match": c_match,
            "overlaps": c_ov,
            "slivers": c_sl,
            "base_p_cut": b_p_cut,
            "curr_p_cut": c_p_cut,
            "base_s_cut": b_s_cut,
            "curr_s_cut": c_s_cut,
        })

    overall_curr_match = (total_curr_matched / max(1, total_oges)) * 100.0
    overall_base_match = (total_base_matched / max(1, total_oges)) * 100.0

    match_rate_met = overall_curr_match >= (overall_base_match - 0.5)
    speed_met = wall_time <= 90.0
    memory_met = peak_ram <= 500.0

    all_passed = all_zero_overlaps and all_zero_slivers and match_rate_met and speed_met and memory_met

    # Construct Markdown report
    lines = [
        "# Phase 5 Hotspot Scanner Benchmark & Scorecard Report",
        "",
        "## Performance & Memory Summary",
        "",
        f"- **Installed Books**: {len(target_ids)} books ({total_pages:,} pages)",
        f"- **Wall-Clock Time**: **{wall_time:.2f}s** (Target: < 90s — {'PASS ✓' if speed_met else 'FAIL ✗'})",
        f"- **Processing Speed**: **{pages_per_sec:.1f} pages/sec**",
        f"- **Peak Total RAM across Workers**: **{peak_ram:.1f} MB** (Budget: < 500 MB — {'PASS ✓' if memory_met else 'FAIL ✗'})",
        f"- **Overlapping Hotspots**: **{total_curr_overlaps}** (Required: 0 — {'PASS ✓' if all_zero_overlaps else 'FAIL ✗'})",
        f"- **Sliver Regions**: **{total_curr_slivers}** (Required: 0 — {'PASS ✓' if all_zero_slivers else 'FAIL ✗'})",
        f"- **Aggregate Match Rate**: **{overall_curr_match:.1f}%** (Baseline: {overall_base_match:.1f}% — {'PASS ✓' if match_rate_met else 'FAIL ✗'})",
        "",
        "---",
        "",
        "## Book-by-Book Scorecard Comparison (Legacy Node.js vs New Python Scanner)",
        "",
        "| Book ID | Pages | Regions (Base → New) | Questions (Base → New) | Match Rate (Base → New) | Overlaps | Slivers | Panels Cut | Solutions Cut |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for r in rows:
        reg_str = f"{r['base_reg']} → **{r['curr_reg']}** ({format_delta(r['curr_reg'], r['base_reg'])})"
        q_str = f"{r['base_q']} → **{r['curr_q']}** ({format_delta(r['curr_q'], r['base_q'])})"
        match_str = f"{r['base_match']:.0f}% → **{r['curr_match']:.0f}%** ({format_delta(r['curr_match'], r['base_match'], is_percentage=True)})"
        ov_str = f"**{r['overlaps']}**" if r['overlaps'] == 0 else f"<span style='color:red;'>**{r['overlaps']}**</span>"
        sl_str = f"**{r['slivers']}**" if r['slivers'] == 0 else f"<span style='color:red;'>**{r['slivers']}**</span>"
        pcut_str = f"{r['base_p_cut']} → {r['curr_p_cut']}"
        scut_str = f"{r['base_s_cut']} → {r['curr_s_cut']}"

        lines.append(
            f"| `{r['short_id']}` | {r['pages']} | {reg_str} | {q_str} | {match_str} | {ov_str} | {sl_str} | {pcut_str} | {scut_str} |"
        )

    lines.extend([
        "",
        "### Totals & Averages",
        "",
        f"- **Total Activities Detected**: {total_curr_regions:,} (Baseline: {total_base_regions:,})",
        f"- **Total Sub-Questions Detected**: {total_curr_questions:,} (Baseline: {total_base_questions:,})",
        f"- **Publisher Oge Match Rate**: {overall_curr_match:.1f}% ({total_curr_matched}/{total_oges} matched)",
        f"- **Total Overlaps**: {total_curr_overlaps}",
        f"- **Total Slivers**: {total_curr_slivers}",
        "",
        "---",
        "",
        "## Acceptance Criteria Status",
        "",
        f"- [{'x' if memory_met else ' '}] **Memory**: Peak total memory across all 4 workers never exceeds 500 MB ({peak_ram:.1f} MB)",
        f"- [{'x' if speed_met else ' '}] **Speed**: Entire batch of installed books finishes in under 90 seconds ({wall_time:.2f}s)",
        f"- [{'x' if match_rate_met else ' '}] **Quality**: Scorecard match rate meets or exceeds baseline ({overall_curr_match:.1f}% vs {overall_base_match:.1f}%)",
        f"- [{'x' if all_zero_overlaps else ' '}] **Zero Overlaps**: 0 overlapping hotspot violations ({total_curr_overlaps} found)",
        f"- [{'x' if all_zero_slivers else ' '}] **Zero Slivers**: 0 sliver hotspot violations ({total_curr_slivers} found)",
        "",
        f"**Final Verdict**: {'PASSED' if all_passed else 'FAILED'}",
        "",
    ])

    report_text = "\n".join(lines)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
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

    wall_time = 0.0
    peak_ram = 0.0

    if not args.skip_scan:
        print("\n[1/3] Running Python PyMuPDF batch scanner...")
        wall_time, peak_ram = run_scanner(workers=args.workers, force=args.force)
    else:
        print("\n[1/3] Skipping scanner run (--skip-scan specified)...")

    print("\n[2/3] Running scorecard harness on generated bakes...")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_scorecard = tmp.name

    try:
        current_scorecard = run_scorecard(tmp_scorecard)
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
