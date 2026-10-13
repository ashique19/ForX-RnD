# Known FX edges on clean bars — results 2026-10-14

This follows pre-registration `PREREG_KNOWN_EDGES_20261014.md` (commit 5eb89ad), with fixed parameters and no tuning. Script: `research/known_edges/known_edges_20261014.py`.
It is an equal-weight 10-pair portfolio on daily returns, after measured costs. N = 108 trials. V[SR] across the 8 rules at p50 (full window). The holdout was scored once.
Carry used FRED OECD 3-month interbank rates (`IR3TIB01*M156N`), lagged one month; EZ and GB are forward-filled after 2026-01.
Trades: signal entries or flips over the full series for rules 1-6 (holdout counts not split); number of trades per window for rules 7-8.

| Rule | Full PF p50 / p90 | Full Sharpe p50 / p90 | Full max DD p90 | Trades | Full DSR p90 | Holdout PF p50 / p90 | Holdout Sharpe p90 | Holdout DD p90 | Holdout DSR p90 | Candidate? |
|---|---|---|---|---|---|---|---|---|---|---|
| TSMOM 12-1 | 0.98 / 0.98 | -0.10 / -0.11 | -17.2% | 226 | 0.00 | 1.01 / 1.00 | 0.01 | -6.4% | 0.08 | no |
| TSMOM 3m | 0.92 / 0.92 | -0.45 / -0.47 | -34.3% | 434 | 0.00 | 0.92 / 0.92 | -0.49 | -8.9% | 0.03 | no |
| XSMOM 3m | 0.94 / 0.94 | -0.34 / -0.36 | -36.1% | 383 | 0.00 | 0.90 / 0.90 | -0.64 | -10.5% | 0.02 | no |
| Carry | 1.01 / 1.01 | 0.05 / 0.04 | -16.1% | 50 | 0.00 | 1.09 / 1.08 | 0.43 | -6.1% | 0.17 | no |
| Donchian 20 | 0.93 / 0.92 | -0.42 / -0.45 | -29.2% | 1094 | 0.00 | 0.97 / 0.96 | -0.24 | -7.2% | 0.04 | no |
| Donchian 55 | 0.95 / 0.95 | -0.27 / -0.29 | -20.5% | 403 | 0.00 | 0.79 / 0.78 | -1.29 | -12.3% | 0.00 | no |
| Session breakout 1h | 0.81 / 0.66 | -1.20 / -2.32 | -73.6% | 24852 | 0.00 | 0.83 / 0.65 | -2.46 | -5.9% (2355 trades) | 0.00 | no |
| Session mean reversion 1h | 0.77 / 0.63 | -1.45 / -2.56 | -80.9% | 24852 | 0.00 | 0.66 / 0.52 | -3.65 | -8.9% (2355 trades) | 0.00 | no |

**Verdict: no rule meets the pre-registered paper-shadow bar** (full DSR p90 >= 0.95 plus holdout PF p90 > 1). The textbook FX trend and momentum rules
were flat to negative from 2015 to 2025 on these 10 pairs, which matches the published post-2010 decay of FX momentum. Breakout and mean reversion both lose,
so gross intraday edge is near zero and costs decide the sign. Carry is the only rule with a positive sign in both windows (PF 1.01 full, 1.08 holdout),
but its Sharpe is 0.04 over 10 years with only 50 position changes, which is statistically indistinguishable from zero. It is not worth building, except possibly as a low-weight
diversifier hypothesis that would need its own pre-registration.
