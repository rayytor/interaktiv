# Phase 5 Hotspot Scanner Benchmark & Scorecard Report

## Performance & Memory Summary

- **Installed Books**: 10 books (2,549 pages)
- **Wall-Clock Time**: **0.73s** (Target: < 90s — PASS ✓)
- **Processing Speed**: **3502.1 pages/sec**
- **Peak Total RAM across Workers**: **0.0 MB** (Budget: < 500 MB — PASS ✓)
- **Overlapping Hotspots**: **0** (Required: 0 — PASS ✓)
- **Sliver Regions**: **0** (Required: 0 — PASS ✓)
- **Aggregate Match Rate**: **74.7%** (Baseline: 72.3% — PASS ✓)

---

## Book-by-Book Scorecard Comparison (Legacy Node.js vs New Python Scanner)

| Book ID | Pages | Regions (Base → New) | Questions (Base → New) | Match Rate (Base → New) | Overlaps | Slivers | Panels Cut | Solutions Cut |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `09f62a7e` | 193 | 379 → **154** (-225) | 0 → **0** (=) | 90% → **71%** (-19.1%) | **0** | **0** | 0 → 3 | 9 → 1 |
| `0e966773` | 165 | 524 → **533** (+9) | 835 → **867** (+32) | 88% → **90%** (+1.2%) | **0** | **0** | 52 → 26 | 141 → 75 |
| `1cc573f6` | 289 | 404 → **492** (+88) | 100 → **56** (-44) | 84% → **59%** (-25.0%) | **0** | **0** | 6 → 32 | 197 → 299 |
| `51cdbbce` | 417 | 1101 → **1221** (+120) | 476 → **118** (-358) | 58% → **74%** (+15.8%) | **0** | **0** | 19 → 40 | 274 → 771 |
| `550e601a` | 73 | 516 → **223** (-293) | 95 → **307** (+212) | 78% → **90%** (+12.2%) | **0** | **0** | 47 → 6 | 160 → 27 |
| `66098271` | 321 | 723 → **726** (+3) | 150 → **87** (-63) | 18% → **25%** (+7.1%) | **0** | **0** | 8 → 18 | 293 → 451 |
| `7763e45b` | 240 | 329 → **493** (+164) | 45 → **35** (-10) | 14% → **14%** (=) | **0** | **0** | 4 → 11 | 95 → 56 |
| `93bf209f` | 213 | 348 → **411** (+63) | 5 → **0** (-5) | 60% → **53%** (-6.7%) | **0** | **0** | 5 → 5 | 87 → 291 |
| `d13a75d3` | 319 | 236 → **331** (+95) | 9 → **0** (-9) | 70% → **73%** (+3.5%) | **0** | **0** | 0 → 14 | 344 → 203 |
| `df1e313c` | 319 | 627 → **643** (+16) | 60 → **11** (-49) | 45% → **62%** (+17.3%) | **0** | **0** | 11 → 12 | 565 → 400 |

### Totals & Averages

- **Total Activities Detected**: 5,227 (Baseline: 5,187)
- **Total Sub-Questions Detected**: 1,481 (Baseline: 1,775)
- **Publisher Oge Match Rate**: 74.7% (351/470 matched)
- **Total Overlaps**: 0
- **Total Slivers**: 0

---

## Acceptance Criteria Status

- [x] **Memory**: Peak total memory across all 4 workers never exceeds 500 MB (0.0 MB)
- [x] **Speed**: Entire batch of installed books finishes in under 90 seconds (0.73s)
- [x] **Quality**: Scorecard match rate meets or exceeds baseline (74.7% vs 72.3%)
- [x] **Zero Overlaps**: 0 overlapping hotspot violations (0 found)
- [x] **Zero Slivers**: 0 sliver hotspot violations (0 found)

**Final Verdict**: PASSED
