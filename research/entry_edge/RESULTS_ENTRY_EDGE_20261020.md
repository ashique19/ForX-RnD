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

## Holdout (entries 2025-10-01 -> 2026-10-12), scored ONCE after 45ed364. p90 costs, R units.

| Variant | Trades | PF | Total R | Max DD R | Win rate | Daily t p (one-sided, alpha 0.0167) | Holdout bar |
|---|---|---|---|---|---|---|---|
| baseline | 2,462 | 0.724 | -309 | -314 | 47.1% | - | - |
| H1 cost gate | 727 | 0.896 | -30 | -45 | 49.9% | 0.90 | FAIL (PF < 1, p not significant) |
| H2 currency strength | 2,523 | 0.769 | -256 | -260 | 49.1% | 1.00 | FAIL (PF < 1) |
| H3 USD de-dup | 1,413 | 0.666 | -217 | -220 | 45.2% | 1.00 | FAIL (PF < 1 and below baseline) |

p50: baseline 0.869, H1 0.981, H2 0.921, H3 0.826. No variant reaches PF 1 at either cost level.

Per-pair holdout PF at p90 (trades in brackets):

| Pair | baseline | H1 | H2 | H3 |
|---|---|---|---|---|
| EURUSD | 0.878 (375) | 0.913 (357) | 0.955 (362) | 0.771 (168) |
| USDJPY | 0.955 (134) | 0.945 (120) | 0.824 (194) | 0.917 (45) |
| GBPUSD | 0.847 (261) | 0.618 (86) | 0.876 (257) | 1.077 (97) |
| AUDUSD | 0.913 (246) | 1.164 (51) | 0.847 (274) | 0.938 (99) |
| NZDUSD | 0.623 (149) | 5.585 (11) | 0.731 (152) | 0.582 (50) |
| USDCAD | 0.690 (253) | 0.538 (6) | 0.757 (257) | 0.697 (91) |
| USDCHF | 0.724 (271) | 2.397 (10) | 0.688 (247) | 0.629 (90) |
| EURJPY | 0.747 (212) | 0.707 (79) | 1.013 (191) | 0.747 (212) |
| EURGBP | 0.467 (343) | - (0) | 0.507 (330) | 0.467 (343) |
| GBPJPY | 0.629 (218) | 3.770 (7) | 0.692 (259) | 0.629 (218) |

The H1 per-pair figures above 1 rest on 6-11 trades, which is noise.

**Verdict: 0 of 3 pass.** H1 (skip trades whose p90 cost exceeds 0.10R) is the only rule that improves the losing recipe in both windows: PF 0.760 -> 0.867 in-sample and 0.724 -> 0.896 on the holdout, with DD far smaller.
It still loses after costs, and on the holdout it is not significant (p 0.90). On the holdout it keeps mostly EURUSD and USDJPY trades.
That says cost is a large part of the loss, but the model has no positive entry edge to keep once costs are cut. H2 gave the model cross-currency information it did not use (in-sample PF 0.756).
Its holdout PF of 0.769 is above the baseline but below 1. H3 lowers PF. One USD bet at a time cuts exposure and DD in proportion to trades, but does not lift quality.
No rule, yaml or live change. A FAIL produces no proposal. Trials N = 131.

