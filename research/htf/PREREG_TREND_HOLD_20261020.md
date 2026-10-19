# Pre-registration addendum: let trend-aligned trades run - 2026-10-20 (written before any of these variants is run)

Same harness, frozen baseline, costs, locked holdout (2025-10-01), metrics in R and pass bar as `PREREG_DAILY_TREND_20261020.md`.
The variants reuse the saved walk-forward predictions; nothing is retrained. Research only, with no live change.

**Code check: the max hold is 8 bars everywhere today.**
- yaml `horizon: 8` (config/default.yaml:27).
- backtest `forex_lab/backtest.py:150`.
- paper broker `forex_lab/broker.py:344-351`.
- scoring `forex_lab/score.py:197`.
- board levels `forex_lab/board.py:355,426`.
- Replay `forex_lab/replay.py:581`.
- exploration `forex_lab/explore_book.py:45` (`default_horizon_bars: 8`) plus the board row's horizon, with the duration exit at 508.
The model's labels are also built at horizon 8 (`forex_lab/features.py:643`). Holding longer is therefore off-label for the model.

**Trend definition (fixed now): F1.** The daily SMA20 vs SMA50 on completed NY-17:00 days, known at the decision time.
A trade is "aligned" when it is a BUY with F1 up or a SELL with F1 down. **Only aligned trades get the variant.** Every other trade keeps the current behaviour (TP 2 / SL 2 ATR, 8 bars).
All baseline signals are kept, with no filtering. `one_position` stays on, so a longer hold blocks later signals for that pair.

**Variants (5)**
- H24: aligned trades get horizon 24 bars, with the same TP 2.0 and SL 2.0 ATR.
- H48: as H24, with horizon 48.
- H120: as H24, with horizon 120 (5 days).
- H120_TP4: horizon 120, TP 4.0 ATR, SL 2.0 ATR.
- H120_TRAIL2: horizon 120 with no TP. The initial SL is 2.0 ATR. After each bar closes, a long's stop is raised to (highest High since entry - 2.0 x ATR_decision); a short's stop is lowered symmetrically.
  The stop only tightens. A bar is checked against the stop set by the previous bars, so there is no same-bar update. Exit at the stop price, or at the Close at the horizon.

**Swap (overnight/weekend).** No broker swap table is stored in the repo. This test does not log in to or query MT5.
A documented conservative estimate is used: every rollover crossed (17:00 New York) costs **1.0 pip** whatever the side, and the Wednesday rollover costs **3x** (the weekend).
Positive carry is never credited. Sensitivity: 2.0 pips per rollover, reported but not judged.
The swap is applied to **every** trade in every variant, including the baseline, using its real entry and exit times.
Primary costs = measured p90 round-trip spread + swap 1.0.

**Pass bar.** The same as the filters, against the baseline with the same swap. In-sample:
- pooled PF >= baseline + 0.05;
- PF >= baseline + 0.02 on >= 6/10 pairs, each pair with >= 30 trades;
- pooled max DD in R no worse;
- pooled trades >= 500.
Holdout, scored once after the in-sample results are on disk: the same criteria, with >= 100 pooled trades and >= 15 per pair. Overall PASS = in-sample AND holdout.
There is no picking of another horizon or multiple after the fact.

**Trials.** 5 more, so **N = 123** (113 + 2 filters + 3 exits + 5 holds).