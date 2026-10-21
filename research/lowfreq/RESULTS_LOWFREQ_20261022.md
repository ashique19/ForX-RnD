# Low-frequency families F1-F3 (4h / daily) - results 2026-10-22

Prereg: `PREREG_LOWFREQ_20261022.md`. Commit 0fd7527, plus a clarification committed before the run in 29fa5eb (F2 weekly decision = Friday close, for causality).
Script and 10 no-lookahead tests: `research/lowfreq/lowfreq_20261022.py`, `tests/test_lowfreq_20261022.py` (29fa5eb).
Three Idle-priority processes (F1/F2/F3) ran in parallel on DESKTOP-GK9IH4N, 2026-10-22 00:04-00:05 Asia/Dhaka, finishing before the 00:30 batch.
- **Bars:** resampled from `data/history` 1h. Trading day = 21:00->21:00 UTC; the Fri 21:00 stub joins Friday. 4h bins start 21/01/05/09/13/17 UTC. Closed bars only; entry at the next bar's Open.
- **Costs (judged):** 2 x p90 measured, plus financing per weekday 21:00 rollover.
  - Financing = real BIS policy-rate differential accrual (data.bis.org WS_CBPOL, lagged 1 day) minus a 1 pip/rollover markup.
  - Weekend nights and triple-Wednesday are not charged. This understates financing magnitude by about 2/7, which flatters long holds when the markup outweighs the accrual (most cases here).
- **Split:** in-sample entries 2015 -> 2025-09-30; holdout from 2025-10-01.
- **Trials:** N = **154** (146 + 8). Alpha = 3.25e-4.

## In-sample. Primary: EURUSD and USDJPY each on their own (F1, F3); the pooled carry book (F2).

| Variant | Scope | Trades | PF (p90+fin) | Net R | Max DD R | Win rate | p (t) | MDE R (80%) | PF no financing | PF p50+fin | Accrual R | In-sample |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| F1_4h_q10 | EURUSD | 210 | 0.820 | -25.9 | -30.9 | 0.329 | 0.910 | 0.389 | 0.855 | 0.854 | 0.4 | FAIL |
| F1_4h_q10 | USDJPY | 220 | 0.800 | -30.2 | -38.3 | 0.336 | 0.938 | 0.377 | 0.822 | 0.873 | 2.4 | FAIL |
| F1_4h_q10 | 10-pair pooled, pairs PF>1 1/10 | 2174 | 0.817 | -278.2 | -297.6 | 0.354 | 1.000 | 0.122 | 0.848 | 0.893 | 5.5 | robustness label: no |
| F1_4h_q20 | EURUSD | 347 | 0.842 | -36.8 | -43.3 | 0.340 | 0.933 | 0.301 | 0.878 | 0.876 | 0.7 | FAIL |
| F1_4h_q20 | USDJPY | 361 | 0.966 | -7.9 | -35.8 | 0.382 | 0.621 | 0.303 | 0.992 | 1.050 | 3.5 | FAIL |
| F1_4h_q20 | 10-pair pooled, pairs PF>1 0/10 | 3402 | 0.808 | -454.7 | -484.8 | 0.355 | 1.000 | 0.097 | 0.840 | 0.881 | 7.9 | robustness label: no |
| F1_D_q10 | EURUSD | 48 | 0.670 | -11.2 | -14.7 | 0.333 | 0.898 | 0.769 | 0.746 | 0.680 | 0.1 | FAIL |
| F1_D_q10 | USDJPY | 43 | 0.561 | -14.4 | -16.0 | 0.256 | 0.959 | 0.798 | 0.585 | 0.579 | 1.1 | FAIL |
| F1_D_q10 | 10-pair pooled, pairs PF>1 3/10 | 433 | 0.782 | -66.0 | -74.7 | 0.339 | 0.992 | 0.268 | 0.850 | 0.809 | 2.4 | robustness label: no |
| F1_D_q20 | EURUSD | 69 | 0.535 | -24.1 | -26.0 | 0.290 | 0.991 | 0.608 | 0.594 | 0.544 | 0.0 | FAIL |
| F1_D_q20 | USDJPY | 56 | 0.592 | -17.3 | -21.7 | 0.268 | 0.966 | 0.707 | 0.627 | 0.610 | 1.0 | FAIL |
| F1_D_q20 | 10-pair pooled, pairs PF>1 1/10 | 626 | 0.744 | -112.9 | -124.2 | 0.327 | 1.000 | 0.219 | 0.809 | 0.769 | 2.9 | robustness label: no |
| F2_top3 | pooled book (primary) | 20 | 1.520 | 13.5 | -15.8 | 0.550 | 0.265 | 4.469 | 2.587 | 1.527 | 24.6 | FAIL |
| F2_top5 | pooled book (primary) | 38 | 0.596 | -38.3 | -61.2 | 0.342 | 0.840 | 4.250 | 0.823 | 0.598 | 32.3 | FAIL |
| F3_L20 | EURUSD | 290 | 0.648 | -33.5 | -36.9 | 0.321 | 0.988 | 0.217 | 0.811 | 0.657 | 0.3 | FAIL |
| F3_L20 | USDJPY | 297 | 0.954 | -4.2 | -26.9 | 0.316 | 0.588 | 0.271 | 1.113 | 0.983 | 1.2 | FAIL |
| F3_L20 | 10-pair pooled, pairs PF>1 0/10 | 2862 | 0.707 | -278.4 | -279.1 | 0.308 | 1.000 | 0.071 | 0.867 | 0.730 | 3.2 | robustness label: no |
| F3_L120 | EURUSD | 72 | 1.032 | 1.1 | -13.0 | 0.278 | 0.469 | 0.871 | 1.577 | 1.041 | 1.8 | FAIL |
| F3_L120 | USDJPY | 95 | 0.934 | -3.2 | -17.1 | 0.295 | 0.553 | 1.066 | 1.280 | 0.948 | 1.8 | FAIL |
| F3_L120 | 10-pair pooled, pairs PF>1 1/10 | 1222 | 0.523 | -274.2 | -299.3 | 0.268 | 1.000 | 0.157 | 0.773 | 0.536 | 7.4 | robustness label: no |

MDE = the minimum mean R per trade detectable at 80% power and alpha 3.25e-4. For daily and weekly variants it is far larger than any plausible edge, so these tests are badly under-powered.

## Holdout
**Not run.** No primary case passed in-sample, so per the prereg the holdout stays untouched for these 8 variants.

## Verdict
**0/8 pass. All 8 are banned.**
- **F1 squeeze breakout, 4h / daily:** still loses at the lower frequency. The best case is USDJPY 4h q20: PF 0.966 (1.05 at p50).
  The daily variants are worse (PF 0.54-0.67) with tiny n, and the 1h USDJPY near-miss does not carry over.
- **F2 carry, BIS policy rates, top-k weekly:**
  - top3 shows PF 1.52 and +13.5R, but only 20 trades: below the declared minimum of 30, p = 0.27, and an MDE of 4.5R.
  - top5 loses (PF 0.60).
  - The holdings are long (median about 170 days), so the cross-sectional book barely rotates. That is not evidence of an edge, and it cannot be tested at this sample size.
- **F3 daily TSMOM:**
  - L20 loses (PF 0.65 EURUSD / 0.95 USDJPY).
  - L120 EURUSD is flat (PF 1.03) after financing and 1.58 before it. The 1 pip/day markup over long holds eats the gross trend edge, and pooled it is 0.52.
