# Results: entry-edge round (H1 cost gate, H2 currency strength, H3 USD de-dup) - 2026-10-20

Prereg: 06dc692 (`PREREG_ENTRY_EDGE_20261020.md`), committed before any run. Code and tests: 2dc0c8c (`research/entry_edge/entry_edge_20261020.py`).
Harness = champion recipe walk-forward on `data/history` (xgboost, train 2000 / step 250, min_conf 0.60, TP/SL 2/2 ATR, horizon 8). No fold trains at or after 2025-10-01.
The re-simulated baseline reproduced the HTF-round baseline exactly (trade count and total R, every pair, p90 and p50).
H2 re-ran the walk-forward with 9 extra features. Its prediction bar counts equal the baseline's on every pair, so the fold boundaries did not shift.
Costs: 2 x measured one-way p90 (primary), p50 secondary. Yaml was untouched, nothing was promoted, N = 131 trials.

## In-sample (entries 2015-05-05 -> 2025-09-30), p90 costs, R units. Written and committed BEFORE the holdout was scored.

| Variant | Trades | PF | Total R | Max DD R | Win rate | Welch p (one-sided) | In-sample bar |
|---|---|---|---|---|---|---|---|
| baseline | 23,723 | 0.760 | -2,607 | -2,613 | 47.7% | - | - |
| H1 cost-to-risk gate <= 0.10R | 9,501 | 0.867 | -534 | -540 | 49.0% | 8.3e-15 (kept vs 14,733 removed) | **PASS** (PF +0.107, DD better, p < 3.8e-4) |
| H2 currency-strength features | 23,765 | 0.756 | -2,667 | -2,675 | 47.7% | 0.61 (vs baseline) | FAIL (PF lower, DD worse) |
| H3 one USD bet at a time | 13,443 | 0.729 | -1,667 | -1,671 | 47.2% | 0.998 (kept vs 10,280 removed) | FAIL (PF lower) |

p50: baseline 0.873, H1 0.943, H2 0.868, H3 0.855.

Per-pair PF at p90 (trades in brackets):

| Pair | baseline | H1 | H2 | H3 |
|---|---|---|---|---|
| EURUSD | 0.948 (2778) | 0.947 (2702) | 0.917 (2734) | 0.901 (992) |
| USDJPY | 0.803 (1871) | 0.873 (1206) | 0.747 (1895) | 0.803 (778) |
| GBPUSD | 0.840 (2832) | 0.869 (2161) | 0.845 (2849) | 0.768 (1120) |
| AUDUSD | 0.789 (1885) | 0.758 (584) | 0.852 (1862) | 0.739 (807) |
| NZDUSD | 0.726 (1734) | 0.665 (188) | 0.750 (1729) | 0.782 (682) |
| USDCAD | 0.738 (2745) | 0.763 (672) | 0.743 (2767) | 0.736 (916) |
| USDCHF | 0.698 (2735) | 0.863 (482) | 0.720 (2722) | 0.744 (1005) |
| EURJPY | 0.700 (2046) | 0.728 (828) | 0.709 (2080) | 0.700 (2046) |
| EURGBP | 0.661 (2843) | 0.878 (363) | 0.645 (2904) | 0.661 (2843) |
| GBPJPY | 0.711 (2254) | 1.130 (315) | 0.672 (2223) | 0.711 (2254) |

**In-sample reading.** Only H1 clears its in-sample bar. Its PF is still 0.867, so it loses less but still loses: cutting costly trades helps without creating a positive edge.
H2 adds nothing: the model does not use the cross-currency information to improve entries. H3 lowers PF, so the USD trades it skips were, if anything, slightly better than the ones kept.
