# Pre-registration: known FX edges on clean bars — 2026-10-14 (written before any scoring)

Research and backtest only. No yaml, no models, no training, no desk or live changes. Data: clean `data/history/<PAIR>_1h.csv` for AUDUSD, EURGBP, EURJPY, EURUSD,
GBPJPY, GBPUSD, NZDUSD, USDCAD, USDCHF, USDJPY. Bid-only, with HistData 2023 hour-holes left as gaps. Daily bars = 1h resampled to days ending 21:00 UTC (17:00 NY).
**Windows:** full = 2015-01-01 -> 2025-09-30 (12-1 rules start after a 1-year warm-up). Holdout = 2025-10-01 -> latest bar, **scored once** after the full-window table is written.
**Costs:** cost per unit of turnover = measured one-way p50 / p90 (`data/paper_shadow/measured_costs.csv`), in pips, converted to a return at the bar close.
An entry plus exit therefore costs 2 x one-way. Every position change pays |Delta position| x one-way.

**Rules (standard parameters, no tuning, no alternatives tried):**
1. **TSMOM 12-1**: monthly rebalance on the last daily bar of the month. Sign of the return from t-252 to t-21 daily bars.
2. **TSMOM 3m**: the same, sign of the 63-day return.
3. **XSMOM**: monthly, rank the 10 pairs by 63-day return; long the top 3, short the bottom 3, flat otherwise.
4. **Carry**: monthly, sign of (base 3m rate - quote 3m rate), from FRED OECD `IR3TIB01{US,EZ,GB,JP,AU,NZ,CA,CH}M156N`, monthly values lagged one month.
   This free documented source was fetched 2026-10-14. EZ and GB end 2026-01 and are forward-filled; that is a known limitation for the holdout.
5. **Donchian 20**: daily; long when the close is above the prior 20-day high, short when below the prior 20-day low, otherwise hold the position (stop-and-reverse).
6. **Donchian 55**: the same with 55 days.
   Rules 1-6 are vol-targeted: position = signal x min(3, 10% / annualized 60-day EWM vol of daily returns), lagged one day. Equal-weight average across pairs (rule 3: across its 6 legs).
7. **Session breakout (1h)**: each weekday, the Asian range = high/low of 00:00-06:59 UTC. Between 07:00 and 15:59 UTC, the first close beyond the range enters
   in that direction at that close. Exit at the 16:00 UTC bar close. At most one trade per pair per day, unit size.
8. **Session mean reversion (1h)**: the same trigger, but enter against the break. The exit is the same.

**Metrics (per rule, portfolio level = equal-weight across pairs, daily returns):** PF of daily portfolio returns, annualized Sharpe
(sqrt 252), max drawdown of cumulative returns, and trades (count of position entries/flips across pairs; for rules 7/8 the number of trades).
Per-pair PF is reported for information only.
**Trials:** 8 new rules, so **N = 108** (100 prior + 8). V[SR] = variance of the 8 rules' daily SRs on the full window at p50. DSR is computed on daily returns.
**Paper-shadow candidate bar (pre-committed):** full-window DSR at p90 >= 0.95, **and** holdout PF at p90 > 1.0 with holdout max DD no worse than
the full-window max DD. Otherwise the rule is "not a candidate". No parameter changes after seeing any result.
