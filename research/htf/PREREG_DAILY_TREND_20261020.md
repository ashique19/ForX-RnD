# Pre-registration: daily-trend filter on champion signals - 2026-10-20 (written before any run)

**Scope.** Research only. Approved by Ashiqul as a test, with no rule, no promotion and no yaml change. `config/default.yaml` stays frozen
(`signals.min_confidence` 0.60, `signals.htf_trend_filter` null). Overrides are made in memory only: `model.compare_logistic=false`, which is speed only and does not affect signals.
No /pipeline/*, no /model/train/*, no Replay jobs, no writes to models/, journals or yaml. Script: `research/htf/daily_trend_wf_20261020.py`.
The off-by-default `htf_trend_filter` path is **not** used. It needs `feature_extras.higher_tf`, which adds features to the model and so changes the baseline.
It is also a UTC resample, not a NY-17:00 day. A standalone filter on identical predictions isolates the filter effect.

**Pairs (10).** EURUSD, USDJPY, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, EURJPY, EURGBP, GBPJPY. History: validated `data/history/<PAIR>_1h.csv`, UTC.

**Baseline = champion recipe, walk-forward.** `forex_lab.backtest.walk_forward_backtest` with the frozen yaml settings:
- xgboost
- train 2000 / test 250 / step 250 bars
- min_conf 0.60 and all yaml signal filters
- TP 2.0 / SL 2.0 x ATR(14), horizon 8, next-bar-open entry, one position

The locked holdout cutoff (`research/holdout.json`, 2025-10-01 00:00 UTC, chosen 2026-10-09) is enforced by `forex_lab/holdout.py`.
No fold trains on a bar at or after the cutoff. Holdout folds are predicted by the last pre-cutoff model.
The filters are applied to **the same fold predictions** (no retrain). Trades are re-simulated with `forex_lab.backtest._simulate_trades`.

**Costs (on).** Primary: round trip = 2 x measured one-way **p90** (`data/paper_shadow/measured_costs.csv`). Secondary (reported, not judged): 2 x p50.

**Daily bars.** Built from the same hourly bars. Trading day D = floor(NY time + 7h), so day D ends at 17:00 America/New_York on D.
Days with fewer than 12 hourly bars are dropped. Daily close = close of the last hourly bar of the day. A day is usable for a signal on hourly bar t (the decision is at t+1h, when bar t closes)
only if its end time is at or before t+1h. **No lookahead.**

**Filters (exactly 2).**
- **F1 (SMA cross).** Daily SMA20 > SMA50 means up, < means down. Only BUY in up and only SELL in down. Other signals become HOLD.
- **F2 (close vs SMA20).** Last completed daily close > its SMA20 means up, < means down. The same rule applies.
If the trend is undefined (fewer than 50 completed days for F1 or 20 for F2, or equality), the signal becomes HOLD.

**Metrics (per pair and pooled).** Per trade R = net_return x entry / (SL_ATR x ATR14 at the decision bar), where 1R is the planned stop.
- PF = sum(R>0) / |sum(R<0)|
- total R
- max drawdown in R (additive, cumulative R by entry time; pooled = all pairs merged by entry time)
- trade count
- win rate
The return-based PF and the trades kept or removed are reported too.
In-sample = entries before 2025-10-01 (walk-forward OOS folds from roughly bar 2000 onward). Holdout = entries at or after 2025-10-01 up to the last bar.

**Pass bar (per filter, p90 costs). In-sample must pass ALL of these:**
1. Pooled PF(filtered) >= pooled PF(baseline) + 0.05.
2. PF(filtered) >= PF(baseline) + 0.02 on >= 6 of 10 pairs. A pair with < 30 filtered in-sample trades counts as not improved.
3. Pooled max DD(filtered) is no worse than the baseline, in R.
4. Pooled filtered in-sample trades >= 500.

**Holdout: evaluated once**, after the in-sample table is written to disk and logged. Both filters are scored whatever the in-sample result.
The same criteria 1 to 3 apply, with pooled filtered holdout trades >= 100 and a per-pair minimum of 15.
**Overall PASS for a filter = in-sample PASS AND holdout PASS.** There is no re-selection, threshold tuning or definition change after either look.
Even a PASS only produces a written proposal to Ashiqul (e.g. a paper shadow). It does not create a rule or change a live gate.

**Trials.** 2 new filters, so **N = 115** (113 + 2). Prior screens of this kind (the 4h MTF gate, the 2026-10-20 daily-trend what-if on 33 live trades) were negative or noise. This is noted as prior evidence against.