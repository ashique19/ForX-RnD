# Pre-registration addendum: signal-flip exit - 2026-10-20 (written before any run; same harness as PREREG_DAILY_TREND_20261020.md)

**Code check (before this test).** No engine has a signal-flip exit, an opposite-signal close or reverse, or a trailing stop. Every exit is TP, SL, horizon or manual:
- backtest: `forex_lab/backtest.py` `_first_touch_exit` (TP/SL/timeout). In `_simulate_trades`, lines 171-172 skip any new signal while a position is open.
- paper broker: `forex_lab/broker.py` `refresh_from_ohlcv` 539-569 (SL/TP/horizon). Line 336 refuses a second position.
- shadow burst: `scripts/shadow_paper_burst.py` 184-186 (`one_position_already_open`).
- exploration book: `forex_lab/explore_book.py` `_exit_reason` 495-511 (tp/sl/duration) and 615 (`max_positions_per_pair`).
- Replay: `forex_lab/replay.py` 699 (skips signals while open or pending) and 215-250 (barrier/timeout plus `exit_hold`).
- yaml: `one_position: true`, horizon 8, barrier 2.0/2.0, `replay.exit_hold.min_bars_before_sl: 2`.
`score.py` maps an "opposite" exit reason, but nothing produces one.
The earlier failures were not flip exits. exit_hold=3 deferred same-bar SLs. Time-stop horizon 6 shortened the holding period.

**Same frozen baseline** (the WF predictions, costs at p90/p50, locked holdout 2025-10-01, metrics in R) as the daily-trend prereg. There is no retrain: the exits use the same fold predictions.
Notation: entry on the Open of bar e = t+1. The model's view at bar s is `pred_raw` (argmax) and `confidence`, known when bar s closes.
- **X1 flip exit.** For s = e .. e+horizon-2: if TP/SL was not touched in bars e..s, and at bar s `pred_raw` is the opposite side with confidence >= the yaml floor (0.60), exit at Open[s+1].
  The round-trip cost is unchanged. There is no new entry, and the next signal must be at or after the exit bar.
- **X2 flip-or-hold exit.** As X1, but the exit fires as soon as the model's argmax at s is not the entry side (HOLD or opposite, any confidence).
- **X3 flip and reverse.** X1 exit. If the entry-filtered signal at s (the full yaml signal filters) is the opposite side, a new trade opens at Open[s+1] with normal barriers.
  Otherwise it behaves as X1.

**Pass bar (each variant against the baseline, p90 costs), the same as the filters.** In-sample:
- pooled PF >= baseline + 0.05;
- PF >= baseline + 0.02 on >= 6/10 pairs, each pair with >= 30 trades;
- pooled max DD in R no worse;
- pooled trades >= 500.
Holdout, scored once after the in-sample results are on disk: the same criteria, with >= 100 pooled trades and >= 15 per pair.
Overall PASS = in-sample AND holdout. There is no re-tuning (no other confidence floor, no other window). Even a PASS only produces a proposal; there is no live change.

**Trials.** 3 more, so **N = 118** (113 + 2 filters + 3 exits).