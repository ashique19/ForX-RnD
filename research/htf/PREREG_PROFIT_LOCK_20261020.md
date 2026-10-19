# Pre-registration addendum: profit-lock + family-wide multiple-testing bar - 2026-10-20 (written before any of these variants is run, and before ANY result of the earlier variants was looked at)

Same harness, frozen baseline (TP 2.0 / SL 2.0 ATR, horizon 8, next-bar-open entry), costs (measured p90 spread primary), locked holdout and R metrics as `PREREG_DAILY_TREND_20261020.md`.
The saved walk-forward predictions are reused, with no retrain. 1R = the 2 ATR stop, which also equals the target distance at 1:1. So "80% of target" = MFE >= 0.8R = 1.6 ATR.
No swap is charged here: the holds stay at 8 bars or less, like the baseline. Research only, with no live change.

**Variants (3). All use closed bars only; the activation is known only after the bar that touched +0.8R has closed.**
- **PL_A lock +0.5R.** Once a bar's High (long) or Low (short) has reached entry +/- 0.8R, the stop moves to entry +/- 0.5R from the NEXT bar on. TP 1R and horizon 8 are unchanged.
  If the activating bar also hit the original SL, the trade is an SL loss (the harness's sl_conflict rule).
- **PL_B close-back exit.** After activation, the first later bar whose Close is back inside entry +/- 0.6R (below +0.6R for a long) causes an exit at the Open of the following bar. The original TP/SL/horizon stay active until then.
- **PL_C partial 50% at 0.8R (reference).** Half the position exits at exactly +0.8R when that level is touched. If the same bar also touches the SL, the SL wins and there is no partial.
  The other half keeps the original TP/SL/horizon. Trade R = 0.5 x 0.8R + 0.5 x the rest's R. The spread is charged once on the full size.

**Prior evidence.**
- The 2026-10-20 what-if on 33 live trades: breakeven at +0.5R gave PF 0.94 vs 0.95, which is noise and not helpful.
- exit_hold=3 (2026-10-02, failed) is a different mechanism. It *deferred* the SL on the first bars, loosening early. The profit-lock *tightens* the stop after a gain.
  Neither was a flip or lock exit, so this test is not a retest of a banned family.

**Family-wide multiple-testing bar.** This applies to all 13 variants in this study (F1, F2, X1, X2, X3, H24, H48, H120, H120_TP4, H120_TRAIL2, PL_A, PL_B, PL_C). It is added before any result is seen.
For a variant to PASS overall it must meet all three:
1. Its own pre-registered in-sample criteria.
2. Statistical significance in-sample: a one-sided paired test on **calendar-month pooled total R (variant minus baseline)** across all in-sample months, t-test on the monthly differences.
   It must give p < **0.05 / 13 = 0.0038** (Bonferroni).
3. Its own holdout criteria, scored once.
Anything else is reported as no edge. **Trials: 3 more, so N = 126** (113 + 13).