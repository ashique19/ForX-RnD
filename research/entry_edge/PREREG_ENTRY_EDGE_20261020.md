# Pre-registration: entry-edge round (3 hypotheses) - 2026-10-20 (written and committed before any run)

**Scope.** Research and paper only. `config/default.yaml` stays frozen (min_conf 0.60, all gates as they are). No promote, no live-gate change,
no /pipeline/*, no /model/train/*, no Replay, no writes to models/, journals or yaml. In-memory override only: `model.compare_logistic=false` (speed only).
Script: `research/entry_edge/entry_edge_20261020.py`. Outputs go to ignored `data/edge_check/entry_edge_20261020/`. Run serially at BelowNormal priority so the desk is not disturbed.

**Motivation.** The champion recipe loses on all 10 pairs in walk-forward after measured costs (in-sample PF 0.760, holdout 0.724 at p90; 0.87 at p50, `research/htf/RESULTS_20261020.md`).
Fifteen exit variants failed, so this round targets ENTRY: which trades are taken, and what the model sees.

## Common harness (identical to the HTF round, 85b657b / afea42b / 03d38ba)
- **Pairs (10):** EURUSD, USDJPY, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF, EURJPY, EURGBP, GBPJPY. Data: validated `data/history/<PAIR>_1h.csv` (UTC), unchanged since before the HTF run.
- **Baseline = champion recipe, walk-forward.** `walk_forward_backtest` with the frozen yaml: xgboost, train 2000 / test 250 / step 250, min_conf 0.60 and all yaml signal filters,
  TP 2.0 / SL 2.0 x ATR14, horizon 8, next-bar-open entry, one position per pair. Baseline predictions = the saved HTF-round fold predictions
  (`data/edge_check/htf_20261020/<PAIR>_preds.csv`). The re-simulated baseline must reproduce the HTF baseline trade count and total R exactly, or the run stops.
- **Split:** holdout cutoff 2025-10-01 00:00 UTC (`research/holdout.json`, enforced by `forex_lab/holdout.py`; no fold trains on a bar at or after it).
  In-sample = entries before the cutoff. Holdout = entries at or after the cutoff, up to the last history bar.
- **Costs:** primary = round trip 2 x measured one-way **p90** (`data/paper_shadow/measured_costs.csv`), constant per pair. Secondary (reported, not judged) = 2 x p50.
- **Metrics:** per trade R = net_return x entry / (2 x ATR14 at the decision bar). Reported: PF = gross +R / gross -R, total R, max DD in R (cumulative R by entry time; pooled = all pairs merged), trades, win rate, plus per-pair tables.
- **Champion to beat** = this baseline (same harness). The live pins were tuned on data that includes the holdout, so they are not a fair comparator.

## Hypotheses (exactly 3, no combinations, no tuning after any look)

**H1 - cost-to-risk entry gate (cheap pairs and hours).** Keep a baseline signal only if its p90 round-trip cost is at most 0.10R:
`cost_R = 2 x one_way_p90_pips x pip_size / (2 x ATR14 at the decision bar) <= 0.10`. Otherwise HOLD. Trades are re-simulated from the gated signals (one position per pair, as in the harness).
Why: costs turn PF 0.87 into 0.72, so trades where the cost is a large share of the stop distance start too far behind. 0.10R is fixed a priori: at 1:1 it moves breakeven win rate to 55%.
The rule selects cheap pairs and liquid, higher-volatility hours together, without mining hours. Same predictions, no retrain.
Relation to banned families: it is not `min_vol_regime 0.80` (a vol-regime percentile cut on EURUSD Replay). This is a per-pair measured-cost normalisation applied to all pairs and judged on the holdout. It is still noted as related prior evidence against.

**H2 - new input family: cross-currency strength features.** From the 10 pairs' own hourly closes (no external data, no synthetic prices), for each currency C in {USD, EUR, JPY, GBP, AUD, NZD, CAD, CHF} and k in {1, 4, 24} bars:
`S_C,k(t)` = mean over the pairs containing C that have a bar at t of (+log(Close_t / Close_{t-k}) if C is the base, - if C is the quote). Each pair's k-bar return uses its own bar sequence; the pair being traded is included.
For pair B/Q the model gets 9 extra features: `S_B,k`, `S_Q,k`, `S_B,k - S_Q,k` for k = 1, 4, 24. They are added to the frozen feature set in memory only (wrapping `forex_lab.features.build_features`), and the walk-forward is re-run with otherwise identical settings.
Causal: everything uses closes at or before bar t, and the decision is at the close of t.
Why: the champion sees only its own pair. Whether a move is USD-wide or pair-specific is information it does not have. This family has never been tested on clean data. The `dxy_yields` extra (external DXY) was only run as a Jetta-era Replay, which is INVALID per EDGE_REALITY.
Rows with NaN strength are dropped by `make_dataset`. The shift in fold boundaries is reported.

**H3 - USD-exposure de-duplication (one USD bet at a time).** From the fixed baseline trade list (no new trades are created), merge all pairs by entry time.
USD pairs = EURUSD, USDJPY, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF. A USD-pair trade is skipped if another accepted USD-pair trade is open at its entry time.
Open means accepted.entry_time <= t < exit-bar open + 1h. Simultaneous entries are ranked by model confidence (higher first), then by the pair order above. Crosses (EURJPY, EURGBP, GBPJPY) are untouched.
Why: tonight's live losses clustered on USD pairs, and correlated USD bets stack drawdown without independent edge. This is a cost/DD control and is not expected to raise PF by itself.

## Pass bar (each hypothesis, p90 costs)
**In-sample PASS = all of:**
1. Pooled PF >= baseline PF + 0.05.
2. Pooled max DD (R) no worse than the baseline.
3. Pooled trades >= 500.
4. Multiple-testing: a one-sided Welch t-test on per-trade R gives p < **0.05 / 131 = 3.8e-4**, which is Bonferroni over every trial so far (128 prior + 3).
   For H1 and H3 the test is kept trades vs removed baseline trades. For H2 it is H2 trades vs baseline trades.

**Holdout: scored ONCE**, after the in-sample table is written and committed. All 3 are scored whatever the in-sample result. **Holdout PASS = all of:**
1. Pooled PF **> 1.00** after p90 costs.
2. PF > the baseline holdout PF.
3. Max DD (R) no worse than the baseline holdout.
4. Trades >= 100.
5. A one-sided t-test that mean daily summed R > 0 (days with at least one trade) gives p < **0.05 / 3 = 0.0167**.

**Overall PASS = in-sample PASS AND holdout PASS.** A PASS produces only a written proposal to Ashiqul (e.g. a paper shadow). It never creates a rule, a yaml change or a live gate.

**Trials:** N = 128 + 3 = **131**.
