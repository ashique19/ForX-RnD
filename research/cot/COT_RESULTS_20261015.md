# CFTC COT positioning study - results 2026-10-15

This follows the pre-registration `PREREG_COT_20261015.md` (commit 3cd6418), with fixed definitions and no tuning. Scripts: `research/cot/fetch_cot.py` and `research/cot/cot_study_20261015.py`.
Data: CFTC Legacy Futures-Only reports, 2014-01-07 to 2026-10-06, 666 reports per currency, non-commercial net. Each report is used only from the first Monday open after its publish date.
That includes holiday slips, the 2018-19 shutdown (treated as published 2019-03-08) and the 2025 catch-up schedule. A report more than 21 days old means flat.
N = 113 trials. V[SR] = variance of the 3 signals' daily SR on the full window at p50. Full window = 2015-01-01 to 2025-09-30. The holdout (2025-10-01 onward) was scored once, after the full table was written.

## Test 1 - COT signals, 10-pair equal-weight portfolio, weekly Monday-open rebalance, vol-targeted

| Signal | Full PF p50 / p90 | Full Sharpe p50 / p90 | Full max DD p90 | Trades (full) | Full DSR p90 | Holdout PF p50 / p90 | Holdout Sharpe p90 | Holdout DD p90 | Holdout trades | Holdout DSR p90 | Candidate? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Z52 contrarian (\|z\|>2) | 1.07 / 1.06 | 0.26 / 0.24 | -4.4% | 216 | 0.22 | 1.30 / 1.29 | 1.08 | -1.1% | 27 | 0.73 | no |
| 4-week change, momentum | 0.99 / 0.98 | -0.05 / -0.09 | -14.9% | 1008 | 0.03 | 0.71 / 0.71 | -1.81 | -8.5% | 98 | 0.01 | no |
| 4-week change, contrarian | 0.99 / 0.98 | -0.05 / -0.09 | -12.0% | 1008 | 0.03 | 1.37 / 1.35 | 1.57 | -3.4% | 98 | 0.88 | no |

## Test 2 - COT crowd filter on the EURUSD champion (clean-rescore trades; research only)

The crowd is the sign of the EUR non-commercial net in the latest usable report at entry. PF is computed on daily sums of trade net returns.

| Book | Full PF p50 / p90 | Full Sharpe p90 | Full max DD p90 | Trades (full) | Full DSR p90 | Holdout PF p50 / p90 | Holdout Sharpe p90 | Holdout trades | Holdout DSR p90 | Candidate? |
|---|---|---|---|---|---|---|---|---|---|---|
| Champion, unfiltered (baseline) | 1.13 / 1.05 | 0.34 | -6.1% | 570 | 0.43 | 1.84 / 1.67 | 3.60 | 85 | 0.95 | baseline (tuned on the holdout) |
| Aligned with crowd | 1.10 / 1.02 | 0.12 | -4.3% | 302 | 0.36 | 1.88 / 1.69 | 3.59 | 45 | 0.88 | no |
| Against the crowd | 1.10 / 1.04 | 0.25 | -3.6% | 253 | 0.42 | 1.20 / 1.10 | 0.68 | 27 | 0.53 | no |

## Verdict

**Nothing meets the pre-registered bar.** That bar is full-window DSR p90 >= 0.95, plus holdout PF p90 > 1 with a DD no worse than the full window, plus (for filters) a full-window PF above the baseline.
- **Z52 contrarian** is the only signal positive in both windows (full PF 1.06, holdout 1.29 after p90 costs). But the full Sharpe is 0.24 over 10.7 years and DSR is 0.22.
  The holdout rests on 27 position changes. This is the same pattern as Carry in the known-edges study: a sign, not an edge.
- **The 4-week change** is a coin flip on the full window: momentum and contrarian are mirror images at PF 0.98. The holdout swing (contrarian PF 1.35, momentum 0.71) contradicts 10 years of flat results.
  Read with 3 signals tested, it is one noisy year, not a regime shift.
- **The filters do not improve the champion.** Both lower the full-window PF at p90 (1.02 aligned and 1.04 against, versus 1.05 unfiltered). Aligned keeps the holdout PF, but the full-window evidence is negative.
  The champion's holdout figures stay a baseline only, because it was tuned on data that includes the holdout.
- The trial count is now **N = 113**.

## Caveats and deviations
- The trade files carry no exit time, so filter-test returns are summed by **entry date**, not exit date as pre-registered. Sharpe on trade days only overstates the annualized Sharpe of sparse books. That makes this a lenient test, and the filters still fail it.
- Positions are flat for about 8 weeks of the holdout (Oct-Dec 2025), because the shutdown backlog made reports stale (more than 21 days).
- Prices are bid-only HistData/clean history (the 2023 hour-holes are left as gaps). Costs are the measured one-way p50/p90 per position change.
- Outputs: `data/research/cot/cot_results_20261015.json` and `data/research/cot/cot_fx_legacy.csv` (data only, not committed).
