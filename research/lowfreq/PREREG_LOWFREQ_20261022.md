# Pre-registration: low-frequency families (4h / daily) - 2026-10-22 (written and committed before any run)

**Scope.** Research only. No yaml edits, no promote, no live-gate change, no /pipeline/*, no /model/train/*.
No writes to models/, journals, MT5 or the desk. Nothing is wired anywhere, even on a pass.
Script: `research/lowfreq/lowfreq_20261022.py`. Outputs go to `data/edge_check/lowfreq_20261022/` (not committed).
Each family runs as its own Idle-priority background process on the PC.

**Why.** On 1h bars costs decide the sign of every family tried (new-families round: 0/15, N=146; best: USDJPY squeeze PF 1.026 at p90, 1.22 at p50).
Longer holds make the round-trip cost a smaller share of each trade.

## Bars (resampled from validated `data/history/<PAIR>_1h.csv`; hourly rows are indexed by bar open, UTC)
- **Trading day = 21:00 UTC -> 21:00 UTC**, the same boundary as the 2026-10-14 known-edges round. It is 17:00 New York in summer (EDT) and 16:00 in winter. It is fixed in UTC, with no DST shift.
  - Trading-day label = date of (hour_open + 3h).
  - Saturday-labelled hours (the Friday 21:00+ stub) join Friday. Sunday-labelled hours join Monday. Weekends otherwise produce no bars.
- **4h bars:** six 4h bins per trading day starting 21, 01, 05, 09, 13 and 17 UTC. A bin with fewer than 2 hourly rows is merged into the previous bin of the same pair.
- **Closed bars only.** A resampled bar is stamped at its close (last hourly open + 1h) and exists only once that close has passed. The incomplete last bin of the data is dropped.
  The decision is taken at a bar close and the entry is at the **next resampled bar's Open**. No lookahead is unit-tested (a signal at t computed on data truncated at t equals the full-data signal).
- **Data notes:**
  - Hourly data runs to 2026-10-12 18:00.
  - From 2026-09 most hourly rows carry Volume 0 but real OHLC (MT5 tail). Volume is not used.

## Costs and financing (primary = judged)
- Entry plus exit cost = 2 x measured one-way **p90** (`data/paper_shadow/measured_costs.csv`), constant per pair.
- Financing per rollover = the 21:00 UTC crossings while held. It has two parts:
  - **Accrual** = side x (r_base - r_quote) / 100 / 365, using **real BIS central-bank policy rates**.
    - Source: `https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip`, a free official source with no signup, downloaded 2026-10-21 (zip SHA256 A90D76D1...3E3D).
    - The daily series for US, XM, JP, GB, AU, NZ, CA and CH are extracted to `data/research/bis/cbpol_daily_8ccy.csv`. That file is not committed.
    - Rates are carried forward between official publications (a step series, not interpolated) and lagged one calendar day.
  - **Minus a broker markup of 1 pip per rollover**, the convention of the HTF hold family (2026-10-20). No triple-Wednesday is applied.
  - A policy rate is not a broker swap. The markup is a cost assumption; real retail swaps can be worse, so this may still be optimistic for carry.
- **Secondary (reported, not judged):** price plus p90 only (no financing), and p50.

## Families and fixed grids (8 variants; no other values will be tried)

**F1 - volatility-squeeze breakout on 4h and daily (4 variants).**
- Squeeze: the percentile rank of ATR14 at t-1 within ATR14 over bars t-250..t-1 is <= q, with q in {0.10, 0.20}.
- Range: max High / min Low of bars t-20..t-1. BUY if Close_t > the range high; SELL if Close_t < the range low. There is no session filter.
- Exit: SL 1.0 x ATR14(t), TP 2.0 x ATR14(t), horizon 10 bars. High/Low first touch, SL wins ties, timeout at the bar-10 close. One position per pair.
- R = net_return x entry / (1.0 x ATR14(t)).
- Variants: F1_4h_q10, F1_4h_q20, F1_D_q10, F1_D_q20.
- Overlap and prior evidence against:
  - The 1h squeeze breakout B_* (2026-10-22) is banned. These variants differ only by timeframe and window, which is a timeframe test of the same idea, declared here.
  - Donchian 20 daily (2026-10-14) failed with PF 0.92 at p90. It had no squeeze and used stop-and-reverse.

**F2 - carry diversifier, cross-sectional (2 variants).**
- Each week, the decision is at the close of the last daily bar of the week. Entry is at the next bar's Open.
- For every pair, d = r_base - r_quote, taken from the BIS rates on the decision date minus 1 day. Pairs with |d| < 0.25 pp are ineligible.
- Hold the top k pairs by |d|, k in {3, 5}, long the higher-yield currency (side = sign(d)).
- A trade is a continuous holding of a pair on one side. A pair that stays selected on the same side keeps its position at no cost; otherwise the position exits at the next Open after the decision.
- R = net_return x entry / (2 x ATR14 daily at the entry decision). Equal risk per trade is the vol-target.
- "Low-weight" is a sizing note for any future use. It is not part of the test.
- Variants: F2_top3, F2_top5.
- Overlap: carry on 2026-10-14 used monthly sign on all 10 pairs, OECD 3m interbank rates, price only and vol-scaling. It was not a candidate (PF 1.01 full / 1.08 holdout).
  The differences here are policy rates, weekly rebalancing, top-k selection and financing accrual. The prior result is weakly positive evidence, not a pass.

**F3 - daily time-series momentum (2 variants).**
- Signal at each daily close: sign(Close_t / Close_{t-L} - 1), with L in {20, 120} daily bars. Always in the market, stop-and-reverse at the next Open when the sign changes.
- R = net_return x entry / (2 x ATR14 daily at the entry decision). Equal risk per trade is the vol-target.
- **L = 60 is excluded:** it is the banned TSMOM 3m (63-day) of 2026-10-14 under a new name.
- Overlap: TSMOM 12-1 / 3m and Donchian 20/55 (all failed 2026-10-14, portfolio and vol-scaled). The HTF daily-trend *filter* on the old model (F1/F2, 2026-10-20) failed.
  L=20 and L=120 are close relatives, and those failures are prior evidence against.
- Variants: F3_L20, F3_L120.

**Trials: 8 new -> N = 146 + 8 = 154.** Bonferroni alpha = 0.05 / 154 = **3.25e-4**.

## Split
- Holdout cutoff 2025-10-01 00:00 UTC (`research/holdout.json`). In-sample = entries before the cutoff; holdout = entries at or after it.
- The in-sample phase gives the simulator only hourly data < cutoff + 30 days. Only entries < cutoff are kept, and positions still open at the data end are closed at the last close.
- The holdout is scored ONCE, only for in-sample passes, after the in-sample results are committed.

## Primary tests
- **F1 and F3:** EURUSD and USDJPY, each its own pass test. The 10 pairs give a robustness label only (pooled, plus >= 6/10 pairs with PF > 1).
- **F2** is cross-sectional by construction, so its primary test is the **pooled carry book** (all pairs' trades). A per-pair test is meaningless when selection rotates across pairs.

## Pass rules (primary costs = p90 plus financing)
**In-sample PASS = all of:**
1. PF >= 1.10.
2. Trades >= the declared minimum: F1 4h 100, F1 daily 30, F2 30, F3 L20 30, F3 L120 15.
3. |max DD R| <= net R.
4. One-sided t-test mean per-trade R > 0, with p < 3.25e-4.

**Holdout PASS = all of:**
1. PF > 1.10.
2. Net R > 0.
3. |max DD R| <= |in-sample max DD R|.
4. Trades >= the minimum: F1 4h 15, F1 daily 5, F2 5, F3 5.
5. One-sided per-trade t-test with p < 0.05 / (number of cases sent to holdout).

**Power (reported honestly):** for every case, the minimum detectable mean R at 80% power = (z(1 - alpha) + 0.84) x sd(R) / sqrt(n), at the in-sample alpha.
Daily and weekly variants have few trades and are expected to be under-powered. A fail there means "not shown", not "shown zero".

A PASS produces only a written report to Ashiqul. It is not wired anywhere. Failed variants join the banned list.

## Implementation clarification (added before any run, while writing the no-lookahead tests)
- "Last daily bar of the week" cannot be known causally: on a Tuesday it is not yet known whether more bars will follow that week.
  The F2 weekly decision is therefore the **Friday daily bar close** (the Friday bar includes the 21:00 stub hour and closes 22:00 UTC). Weeks without a Friday bar skip the rebalance.
  No other rule changed.
