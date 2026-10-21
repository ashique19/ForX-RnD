# Pre-registration: swap re-test of F3_L120 and F2_top3 with measured MT5 broker swaps - 2026-10-22 (committed before any run)

**What changes.** Only the financing model, and only for 2 already-tested variants (`PREREG_LOWFREQ_20261022.md`, results 69da17b).
- **Replaced:** BIS policy-rate accrual minus 1 pip/rollover markup.
- **With:** the measured OANDA demo swaps (`SWAPS_20261022.md`, snapshot 2026-10-21 18:13 UTC).
  - Per weekday 21:00 UTC rollover held: return += swap_side% / 100 / 360 (side = long or short swap). This applies as a fraction of price because the swap mode is interest-on-current-price.
  - **Wednesday 21:00 UTC rollover x3**, giving 7 nights per week, so the weekend is included.
- **Unchanged:** bars, signals, entries/exits, p90 round-trip cost, R definition, split (holdout from 2025-10-01), metrics, pass rules and minimum trade counts.
  Script: `research/lowfreq/swap_retest_20261022.py` (imports the lowfreq module and swaps only the financing function).

**Variants (2 new trials):**
- `F3_L120_swap`: primary tests EURUSD and USDJPY, each on its own.
- `F2_top3_swap`: primary test is the pooled carry book.

**Trials: N = 154 + 2 = 156.** In-sample alpha = 0.05 / 156 = **3.21e-4**.

**Pass rules:** identical to the lowfreq prereg.
- In-sample: PF >= 1.10; trades >= minimum (F3_L120 15, F2 30); |max DD R| <= net R; per-trade one-sided t-test p < 3.21e-4.
- Holdout only if in-sample passes: PF > 1.10, net R > 0, DD no worse than in-sample, trades >= 5, per-trade p < 0.05 / cases.

**Declared in advance, honestly:**
1. F2_top3 had 20 in-sample trades under the same signals, below its minimum of 30. Financing does not change the trade count, so **F2_top3_swap cannot pass in-sample**.
   It is run for information. The minimum is not lowered after seeing data.
2. **Limitation:** today's swaps are applied to all of history (2015-2025 rates were near zero to 5%). This tests "would the strategy survive today's broker financing", not the historical P&L.
3. Secondary, reported only (not judged): historical BIS differential plus today's broker markup per side, where markup = swap_side_now - side x BIS_diff_now (annual %, /360 per night, Wednesday x3).
4. This re-test exists because the earlier result was seen (L120 PF 1.58 before financing). That is why it counts as new trials and keeps the Bonferroni bar.
