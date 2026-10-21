# Swap re-test: F3_L120 and F2_top3 with measured MT5 broker swaps - results 2026-10-22

Prereg `PREREG_SWAP_RETEST_20261022.md` (95c0ab3, committed before the run). Swap table: `SWAPS_20261022.md`.
Script and tests: `research/lowfreq/swap_retest_20261022.py`, `tests/test_swap_retest_20261022.py` (f75b177).
- **Run:** in-sample on 2026-10-22 ~00:25 Asia/Dhaka, Idle priority, a few seconds of CPU.
- **Financing:** today's OANDA demo swaps (interest on current price, /360 per night, Wednesday 21:00 UTC x3, 7 nights/week) applied to all history. p90 round trip as before.
- **Trials:** N = **156**. Alpha = 3.21e-4.

| Variant | Scope | Trades | PF (p90 + swaps) | Net R | Max DD R | Win rate | p (t) | MDE R | Swap R | PF no financing | PF p50 + swaps | PF hist BIS + markup (secondary) | In-sample |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| F3_L120_swap | EURUSD | 72 | 1.435 | 14.1 | -10.9 | 0.306 | 0.229 | 1.119 | -3.3 | 1.577 | 1.448 | 1.437 | FAIL |
| F3_L120_swap | USDJPY | 95 | 1.152 | 7.1 | -16.6 | 0.316 | 0.405 | 1.312 | -4.1 | 1.280 | 1.169 | 1.162 | FAIL |
| F3_L120_swap | 10-pair pooled (label) | 1222 | 0.685 | -168.7 | -201.7 | 0.289 | 1.000 | 0.177 | -54.7 | 0.773 | 0.702 | 0.690 | label: no |
| F2_top3_swap | pooled book (primary) | 20 | 4.123 | 46.6 | -8.4 | 0.700 | 0.028 | 4.878 | 24.4 | 2.587 | 4.144 | 6.518 | FAIL |

**Holdout: not run.** No primary case passed in-sample:
- **F3_L120 EURUSD:** PF 1.435, +14.1R, DD -10.9R, 72 trades. It passes PF, trade count and recovery, but p = 0.23 against 3.21e-4. At 72 trades it would need a mean of about +1.1R per trade to be detectable.
- **F3_L120 USDJPY:** PF 1.15, +7.1R. It fails recovery (DD -16.6R > net R) and significance (p = 0.41).
- **F2_top3:** PF 4.12, +46.6R, but only 20 trades, below the pre-declared minimum of 30, and p = 0.028. As declared in the prereg, it cannot pass.
  About half of its R (+24.4R) is swap income from today's rates applied to the past.

**Reading.** The 1 pip/night markup was the main reason L120 looked flat: with measured swaps EURUSD L120 recovers most of its gross edge (PF 1.58 -> 1.44).
That is still about 12 trades a year, so this sample cannot show significance at this multiple-testing bar.
The 10-pair L120 book loses (PF 0.69), so the EURUSD result is likely pair-specific or chance.
These are hypothesis-generating only. Both variants are now spent trials (banned from re-runs under new names).
