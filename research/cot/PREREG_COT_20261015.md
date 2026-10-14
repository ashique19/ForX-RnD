# Pre-registration: CFTC COT positioning signals - 2026-10-15 (written before any COT data is downloaded or scored)

Research and backtest only. No yaml, no models, no training/promote, no /pipeline or /model/train, no desk or live changes.

**Data.** CFTC Legacy *Futures-Only* Commitments of Traders, annual files `https://www.cftc.gov/files/dea/history/deacot{YYYY}.zip`, 2014-2026
(2014 is warm-up only). Contracts (CME): EUR 099741, GBP 096742, JPY 097741, AUD 232741, NZD 112741, CAD 090741, CHF 092741.
Speculative net = Noncommercial Long (All) - Noncommercial Short (All). One positioning series only (legacy non-commercial); TFF leveraged funds are **not** tested (that would be another trial).
Prices: clean `data/history/<PAIR>_1h.csv` (same as KNOWN_EDGES_20261014), 10 pairs AUDUSD EURGBP EURJPY EURUSD GBPJPY GBPUSD NZDUSD USDCAD USDCHF USDJPY.

**Point-in-time rule (no look-ahead).** A report "as of" Tuesday T is published on Friday T+3 (15:30 ET). It is usable only from the **first Monday open after the publish date**.
- Normal week: usable from Monday T+6.
- If a US federal holiday falls on Wed/Thu/Fri of the report week, the release slips to Monday, so it is usable from Monday T+13.
- 2025 shutdown: the CFTC catch-up publish dates from press release 9147-25 (09/30/2025 -> 11/19/2025, ..., 12/16/2025 -> 12/23/2025, 12/23/2025 -> 12/29/2025) are used as the publish dates.
- 2018-19 shutdown: reports as of 2018-12-18 through 2019-02-26 are treated as published 2019-03-08, which is conservative.
- Each Monday, a currency uses the latest report whose usable date is on or before that Monday. If that report is more than 21 days older than the Monday, the currency goes flat.

**Execution.** The position is set at the Monday open and held for the week. Daily bars end at 21:00 UTC (17:00 NY). The Monday open is the open of the first 1h bar at or after Sunday 21:00 UTC.
On a rebalance Monday, the old position earns Friday close -> Monday open, and the new position earns Monday open -> Monday close. Other days earn close-to-close.
Size = signal x min(3, 10% / annualized 60-day EWM vol), using vol known before the Monday. This is the same vol target as KNOWN_EDGES.
Costs: every position change pays |Delta position| x measured one-way cost (`data/paper_shadow/measured_costs.csv`), at p50 and at p90.
Portfolio = equal-weight mean across the 10 pairs, daily returns.

**Signals (per currency c vs USD, d_c in {-1, 0, +1}; standard definitions, no tuning, no alternatives):**
1. **COT_Z52_CONTRA**: z = (net - mean of the last 52 reports) / std of the last 52 reports (including the current one, needs 52 reports). Then d_c = -1 if z > +2, d_c = +1 if z < -2, otherwise 0.
2. **COT_D4_MOM**: d_c = sign(net_t - net_{t-4}), following the 4-report change in positioning.
3. **COT_D4_CONTRA**: d_c = -sign(net_t - net_{t-4}).

**Mapping to pairs.** For XXXUSD the pair signal is d_XXX. For USDXXX it is -d_XXX. For crosses A/B (EURGBP, EURJPY, GBPJPY) it is clip(d_A - d_B, -1, +1).

**Second test: COT as a filter on the EURUSD champion** (the clean-rescore trades in `data/edge_check/clean_rescore_EURUSD_{p50,p90}.csv`, unchanged).
The crowd for EUR is the sign of the EUR spec net in the latest report usable at the trade's entry_time (same rule as above).
- **FILTER_ALIGNED**: keep only trades whose side equals the crowd (BUY when spec net > 0, SELL when spec net < 0).
- **FILTER_AGAINST**: keep only trades against the crowd.
The unfiltered champion is reported as the baseline. It is **not** a new trial, and it was tuned on full history including the holdout.

**Windows.** Full = 2015-01-01 -> 2025-09-30. Holdout = 2025-10-01 -> latest bar (`research/holdout.json`), **scored once**, after the full-window table is written.

**Metrics.** PF, annualized Sharpe (sqrt 252 on daily returns; filter tests use daily sums of trade net returns by exit date), max drawdown (additive), trades
(position entries/flips across pairs; trade count for the filter tests), and deflated Sharpe.
**Trials:** 3 signals + 2 filters = 5 new trials, so **N = 113** (108 + 5), used for every DSR here. V[SR] = variance of the 3 signals' daily SR on the full window at p50 (the same V[SR] is used for the filters).

**Candidate bar (pre-committed):** full-window DSR at p90 >= 0.95, **and** holdout PF at p90 > 1.0, **and** holdout max DD no worse than the full-window max DD.
For the filters, additionally, the filtered full-window PF at p90 must exceed the unfiltered champion's.
Otherwise the result is "not a candidate". No parameter changes after seeing any result.
