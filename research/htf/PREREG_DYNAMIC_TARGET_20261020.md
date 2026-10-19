# Pre-registration addendum (FINAL): dynamic target - 2026-10-20 (written before any result of any variant was looked at)

Same harness, frozen baseline (TP 2.0 / SL 2.0 x ATR at decision, horizon 8, next-bar-open entry), measured p90 spread, locked holdout and R metrics (1R = the 2 x decision-ATR stop).
The saved walk-forward predictions are reused, with no retrain. There is no swap (holds of 8 bars or less). Research only.
The trend-extension case is already covered by the H24, H48, H120, H120_TP4 and H120_TRAIL2 variants and is not duplicated here.

**Variants (2). Closed bars only. The new TP applies from the NEXT bar. The SL is unchanged at entry -/+ 2 x decision ATR.**
- **DT1 re-set.** After each bar i closes, TP = entry +/- 2.0 x ATR14(i), using the ATR as of that closed bar. It can move in or out.
- **DT2 outward while agreeing.** As DT1, but the TP only moves away from entry (never closer), and only while the model's argmax (`pred_raw`) on bar i is still the trade side.
  Otherwise the TP stays where it is.

**Multiple testing: final family = 15 variants.** F1, F2, X1, X2, X3, H24, H48, H120, H120_TP4, H120_TRAIL2, PL_A, PL_B, PL_C, DT1, DT2.
This supersedes the 13 in PREREG_PROFIT_LOCK_20261020.md. The Bonferroni bar becomes a one-sided monthly paired p < **0.05/15 = 0.00333**, in-sample.
This is on top of each variant's own in-sample criteria (pooled PF +0.05, >= 6/10 pairs +0.02 with >= 30 trades each, pooled max DD in R no worse, >= 500 trades) **and** its holdout criteria, scored once.
**Trials N = 128** (113 + 15). **The pre-registration is final. No more variants are added.**