# Pre-registration: confidence-threshold selection — 2026-10-13 (written before any scoring)

**Scope.** Research/backtest only. Pairs: USDJPY, EURUSD, EURJPY. Models: the clean-bar challengers in `data/challengers/20261013/`
and the frozen champions in `models/` (scored in parallel, same rule). The threshold is overridden **in memory only**; `config/default.yaml`
stays frozen (`signals.min_confidence` 0.60). No training, no promote, no Replay, no /pipeline or /model/train.

**Grid (per pair, per model):** `min_confidence` in {0.40, 0.45, 0.50, 0.55, 0.60}. 0.60 is the current yaml value. All other filters stay as in yaml.

**Trials.** 5 thresholds x 3 pairs x 2 models = **30** new trials, added to the 70 already counted, so **N = 100** for every deflated Sharpe here.
V[SR] = 0.002631 (across-pair per-trade SR variance from the 2026-10-12 rescore), fixed in advance.

**Costs.** Round trip = 2 x measured one-way p50 / p90 (`data/paper_shadow/measured_costs.csv`). The simulator is `forex_lab.backtest._simulate_trades`.

**Selection window: pre-holdout OOS slice only.** Use each model's challenger validation start (USDJPY 2023-08-16 17:00, EURUSD 2023-09-01 04:00,
EURJPY 2023-08-16 11:00 UTC) through 2025-09-30 23:00 UTC. The same slice is used for the champion of each pair.
(The champions were fit on the first 80% of corrupted history, to ~mid-2024, so part of this slice is in-sample for them. That is noted, not corrected.)

**Selection rule (applied per pair and model, in this order):**
1. Eligible: >= 50 trades in the slice, and max drawdown at p90 >= -10% (additive, unit size).
2. Among eligible thresholds, pick the **highest PF at p90**. Ties within 0.01 go to the higher threshold (fewer, stricter trades).
3. If no threshold is eligible, the model has **no candidate** and is not scored on the holdout.

**Holdout (2025-10-01 -> latest bar): scored once, only for the selected threshold.** Report PF p50/p90, max DD p50/p90, trades, DSR (N = 100).

**Proposal bar for paper shadow (pre-committed).** A pair/model is proposed to Ashiqul for paper shadow only if, on the holdout,
it has >= 50 trades, PF at p90 >= 1.10, max DD at p90 >= -10%, and DSR at p50 >= 0.95. A challenger must also clearly beat its champion's holdout PF at p90 (by >= 0.10)
without a worse DD to be preferred over the champion. Anything else is reported as "no candidate". No re-selection after seeing the holdout.
