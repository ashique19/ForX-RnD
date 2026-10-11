# EDGE REALITY CHECK - 2026-10-09 (Asia/Dhaka)

Study #1 ("is the edge real?") and Study #3 ("score every board call").
Existing artifacts only: no new Replay, no training, no promote, no yaml edits.
Reproduce: `.venv\Scripts\python.exe research\edge_check\edge_reality.py`
(outputs land in gitignored `data/edge_check/`), and
`.venv\Scripts\python.exe scripts\score_calls.py [--backfill-signals signals\latest_signals.csv] [--cost p90]`.

Labels used below: **[computed]** = number produced by the scripts from files on
disk; **[hypothesis]** = explanation not yet proven.

---

## 0. Headline

**The Replay PF of about 2.4-3.2 comes from corrupted history bars, not from a trading edge.** [computed]

* Starting 2026-09-30 01:01 Dhaka, the 1h history store was re-pulled from the Dukascopy
  **Jetta** REST feed (`forex_lab/history.py::decode_jetta_side`). That decoder rebuilds
  every bar's Open/High/Low as `previous CLOSE + delta x multiplier`. The Jetta feed
  actually encodes each field as a delta from the **previous bar's same field**. On
  EURUSD 2022+ the stored `(Open_t - Close_{t-1})` equals the true
  `(Open_t - Open_{t-1})` with median |diff| **0.0 pips** and corr **0.980**. Against the
  decoder's own assumption the median |diff| is 4.6 pips and corr is 0.034. [computed]
* So every stored Open is shifted by the previous bar's body, which is already known
  when the decision is made, and the wick sanitiser then forces High/Low = max/min(O,C):
  * Jetta-era bars: **~60% wickless** and a median |Open - prev Close| of **3-9 pips**.
  * Clean Dukascopy bars: **0% wickless** and **0.04-0.09 pips**.
  * Close prices are unaffected. They match the clean Dukascopy file (median diff 0.0)
    and the yfinance cache (median 0.25-3.2 pips). [computed]
* The Replay fills at the next bar's (fake) bid/ask Open. Compared with the real
  Dukascopy bid/ask open, the logged EURUSD entries on Jetta bars are:

  | job | segment | trades matched | median fill advantage vs real open | logged PF |
  |---|---|---:|---:|---:|
  | 2cee7e71 (EURUSD pin) | 2015-2021 (clean bars) | 1288 | **-0.2 pip** (= the slip) | 1.016 |
  | 2cee7e71 | 2022+ (Jetta bars) | 604 | **+10.35 pips** | 2.921 |
  | 0f541b01 (exit-hold-3) | 2022+ | 146 | **+18.25 pips** | 3.338 |
  | 76c3d050 (overlap book, "PF 2.7-3.2") | 2015-2021 | 105 | -0.2 pip | 0.988 |
  | 76c3d050 | 2022+ | 149 | **+18.40 pips** | 2.726 |

  The barrier is about 2 ATR, roughly 20 pips. A free 10-18 pip head start on every
  entry is enough on its own to make PF ~3. [computed: fill advantage; hypothesis:
  that it accounts for the whole PF gap, supported by section 2]
* Same pin and same config, split only by data: EURUSD **2015-2021 PF ~1.0** (clean
  Dukascopy rows kept) and **2022-2026 PF 2.4-3.3** (rows replaced by Jetta). Every
  pair pinned on Jetta data shows PF ~3 in **every** year back to 2015, and logged
  monthly PF > 1 in **99-100% of 140 months**. No real FX edge behaves like that. [computed]

## 1. Reconciling the README walk-forward (PF < 1) with Replay (PF 2.4-3.2)

| Candidate cause | Finding |
|---|---|
| **Different data** | **Root cause.** README WF (`reports/latest_metrics.json` @HEAD, `reports/experiments.md`) runs on yfinance 1h mid: EURUSD model PF **0.978** (1066 trades, 41 folds); GBPUSD 0.945; USDJPY 0.943. Replay pins run on Jetta-decoded bars with fabricated Open/High/Low (section 0). On **clean** Dukascopy bars the same Replay code gives PF ~1.0: 887059a3 0.995 (4802 trades), 57f4dd89 0.961, a694db86 (2015-2022-01) 1.011 (1301 trades), 474b4b21 (2024-10+ mid fills) 0.893. [computed] |
| Costs | Not the cause. README WF uses 1 pip round trip on mid. Replay uses real bid/ask plus 0.2 pip slip each side. Measured one-way costs (p50 0.4-1.7 pips) make PF *lower*, not higher (section 3). [computed] |
| Periods | Contributes nothing on clean data: README WF covers about 2024-09 to 2026-09, and Replay 2024-09+ on clean Dukascopy gives PF 0.89-1.05 (runs 2708c12a, a627017e, 5e2408a3, f84c4c41, 474b4b21). The PF jump lines up exactly with the Jetta re-pull boundary (2022-01 for EURUSD, 2015 for other pairs). [computed] |
| Label leakage | Replay `_refit` is causal (`trainable_mask` requires label settle <= clock; LookaheadError guard), so it is not a classic label leak. The leak is through the **prices**: a fake Open that encodes the previous bar's move. [computed: decode test; Replay train/test causality by code inspection] |
| Overlap book vs full | The "overlap book" (session ldn_ny + weekday Mon/Thu block + news blackout) is a filtered subset of the same stream. It inherits the same fill advantage (+18.4 pips on 2022+). On clean pre-2022 bars it is PF 0.988. [computed] |
| Filter selection on full history | Real, but second order. 67 configurations were tried (section 2). The clean-data weekly champion a0d93fc6 (PF 1.47 logged, 156 trades) is the product of that search. [computed count; hypothesis: its PF is inflated by selection] |

## 2. Per-pair champion statistics (existing trade logs, re-priced)

**Method** [computed]: the logged entry times and sides are kept, and each call is
re-labelled on the **Close path only**, the one price field that is not corrupted:
* entry = close of the decision bar;
* barrier = 2.0/2.0 x ATR14 (close-based ATR x 2.051, a factor calibrated on clean
  EURUSD 2015-2021 true-range ATR), 8-bar horizon, SL not checked in the first 2 bars
  (as `exit_hold`);
* cost = measured round trip = 2 x one-way p50 / p90 from
  `data/paper_shadow/measured_costs.csv`.

**Validation on clean bars** [computed]: EURUSD pin 2cee7e71 pre-2022, 1288 trades,
logged PF 1.016 vs re-sim PF **1.017**, win rate 0.511 vs 0.524. The re-sim
reproduces the real-price Replay closely.

Statistics:
* **DSR** (Deflated Sharpe Ratio, Bailey & Lopez de Prado), per-trade Sharpe.
  * N = **67** trials: 53 EURUSD Replay runs with trade logs + 14 WF screens in
    `reports/experiments.md`.
  * Var[SR] across those trials: 0.0287 logged, 0.0060 re-sim.
* **MinTRL**: minimum number of trades for PSR(SR>0) >= 95%.
* **Holdout**: bars >= 2025-10-01 UTC (see `research/holdout.json`). The champions were
  tuned on full history that includes this window, so these holdout numbers are a
  **baseline, not a pass**.

| Pair (pin job) | n | Logged PF (Jetta bars) | **Re-sim PF p50** | Re-sim PF p90 | Exp. p50 (bp/trade) | Ann. SR p50 | DSR p50 | MinTRL p50 | Holdout PF p50 / DD | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| USDJPY (ef6fe062) | 5694 | 3.04 | **0.90** | 0.81 | -1.13 | -0.83 | 0.000 | inf | 0.93 / -5.7% | **noise** |
| EURJPY (a4258785) | 5934 | 3.04 | **0.90** | 0.79 | -1.17 | -0.88 | 0.000 | inf | 0.88 / -5.4% | **noise** |
| AUDUSD (6a5653c8) | 5977 | 2.98 | **0.87** | 0.80 | -1.93 | -1.25 | 0.000 | inf | 0.94 / -8.7% | **noise** |
| NZDUSD (83c2715d) | 5695 | 2.85 | **0.85** | 0.74 | -2.39 | -1.46 | 0.000 | inf | 0.94 / -6.5% | **noise** |
| GBPJPY (1b8485ec) | 5904 | 2.78 | **0.87** | 0.72 | -1.92 | -1.23 | 0.000 | inf | 0.89 / -7.1% | **noise** |
| USDCAD (a9b26129) | 6164 | 2.74 | **0.89** | 0.81 | -1.23 | -1.09 | 0.000 | inf | 0.80 / -8.1% | **noise** |
| GBPUSD (d6a6f7a6) | 6149 | 2.60 | **0.89** | 0.82 | -1.39 | -1.06 | 0.000 | inf | 0.93 / -7.4% | **noise** |
| USDCHF (d9f72604) | 6211 | 2.60 | **0.87** | 0.76 | -1.51 | -1.27 | 0.000 | inf | 0.85 / -12.0% | **noise** |
| EURGBP (1bece8f2) | 6255 | 2.38 | **0.86** | 0.73 | -1.43 | -1.31 | 0.000 | inf | 0.98 / -2.4% | **noise** |
| EURUSD pin (2cee7e71) | 3791 | 1.99 | **0.99** | 0.94 | -0.13 | -0.09 | 0.000 | inf | 0.85 / -10.0% | **noise** |
| EURUSD overlap book (76c3d050) | 670 | 2.56 | **0.83** | 0.79 | -1.92 | -0.54 | 0.000 | inf | 0.80 / -4.0% | **noise** |
| EURUSD weekly champion, clean Dukascopy (a0d93fc6) | 156 | 1.47 | **1.71** | 1.62 | +5.45 | +0.74 | **0.58** | 63 trades | 1.13 / -1.2% (23 trades) | **doubtful** |

DD is the additive unit-size drawdown in return units (-0.057 = -5.7%), over the
holdout window.

**Pairs that stay PF > 1.1 after measured costs:** none of the ten pair pins. Only the
small clean-data EURUSD weekly champion (a0d93fc6) stays above 1.1 (p50 1.71, p90
1.62). It is still doubtful, because:
* DSR 0.58 is below 0.95 after 67 trials;
* it has 156 trades over 11 years (about 14 a year);
* holdout PF is 1.13 on only 23 trades;
* the yearly re-sim PF swings from 0.43 (2018) to 5.12 (2021), and 2022/2026 are < 1.

[computed]

**PBO via CSCV** [computed]:
* Data: monthly returns of the 35 de-duplicated full-history EURUSD Replay variants,
  138 months, S = 16 blocks, 12,870 combinations.
* PBO **0.10** (logged returns), **0.06** (re-sim p50).
* Read carefully: PBO only measures whether the in-sample best variant *ranks* above
  the median out of sample. The variants are highly correlated and share the same
  corrupted price stream, so a low PBO here does **not** mean there is an edge. The
  absolute re-sim PF of the variants is ~1 or below.

## 3. Month-by-month as-of slices (re-sim p50, logged trades re-sliced, not retrained)

[computed] Full per-month PF / sum / intramonth DD: `data/edge_check/monthly_resim_p50.csv`.

| Pin | Months | Share of months PF > 1 (re-sim p50) | Share PF > 1 (logged) | Worst month (re-sim) |
|---|---:|---:|---:|---|
| USDJPY | 140 | 39% | 100% | 2016-11, -8.7% |
| EURJPY | 140 | 43% | 99% | 2017-04, -6.4% |
| AUDUSD | 140 | 31% | 100% | 2022-06, -6.9% |
| NZDUSD | 140 | 34% | 99% | 2020-03, -7.8% |
| GBPJPY | 140 | 34% | 100% | 2016-04, -6.9% |
| USDCAD | 140 | 33% | 99% | 2022-04, -4.4% |
| GBPUSD | 140 | 34% | 100% | 2022-10, -7.1% |
| USDCHF | 140 | 36% | 100% | 2015-06, -8.0% |
| EURGBP | 140 | 32% | 100% | 2016-07, -7.0% |
| EURUSD pin | 140 | 45% | 68% | 2023-01, -4.4% |
| EURUSD overlap book | 108 | 51% | 73% | 2022-09, -2.4% |
| EURUSD weekly champion (clean) | 81 | 69% | 65% | 2018-11, -1.4% |

Yearly re-sim PF (p50) [computed]:

| Pin | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AUDUSD | 0.90 | 0.75 | 0.82 | 1.04 | 0.84 | 0.94 | 0.67 | 0.88 | 0.99 | 0.89 | 0.89 | 0.86 |
| EURGBP | 0.92 | 0.75 | 1.03 | 0.93 | 0.70 | 0.82 | 0.90 | 0.96 | 0.77 | 0.81 | 0.96 | 0.91 |
| EURJPY | 0.90 | 1.07 | 0.70 | 1.10 | 0.99 | 0.84 | 0.84 | 0.72 | 0.89 | 1.11 | 0.90 | 0.91 |
| EURUSD pin | 1.01 | 0.81 | 1.39 | 1.13 | 1.28 | 0.83 | 0.80 | 1.21 | 0.90 | 0.98 | 0.90 | 0.78 |
| EURUSD overlap | 0.40 | 1.61 | 2.17 | 0.99 | 0.71 | 1.56 | 0.33 | 0.96 | 0.72 | 0.83 | 0.81 | 0.63 |
| EURUSD weekly champ (clean) | 2.81 | 2.15 | 1.04 | 0.43 | 2.49 | 2.16 | 5.12 | 0.61 | 3.43 | 2.63 | 3.35 | 0.92 |
| GBPJPY | 0.86 | 0.81 | 0.92 | 0.89 | 0.76 | 0.84 | 0.85 | 0.89 | 0.89 | 0.87 | 0.99 | 0.95 |
| GBPUSD | 1.03 | 0.90 | 0.81 | 0.92 | 0.79 | 0.86 | 0.84 | 1.01 | 0.80 | 1.04 | 0.88 | 0.80 |
| NZDUSD | 1.10 | 0.92 | 0.79 | 0.85 | 0.95 | 0.77 | 0.79 | 0.90 | 0.81 | 0.64 | 0.66 | 1.01 |
| USDCAD | 0.96 | 0.91 | 0.93 | 0.77 | 0.85 | 0.90 | 0.78 | 0.91 | 0.99 | 0.79 | 0.93 | 0.81 |
| USDCHF | 0.80 | 0.88 | 0.93 | 0.89 | 0.75 | 0.94 | 0.80 | 1.04 | 0.92 | 0.86 | 0.80 | 0.80 |
| USDJPY | 0.85 | 0.74 | 0.93 | 0.92 | 1.10 | 0.77 | 1.03 | 0.84 | 0.93 | 1.05 | 0.93 | 0.95 |

## 4. Measured-cost re-pricing (assumed vs measured)

* Assumed costs in Replay: real bid/ask + 0.2 pip slip each side; README WF uses
  1 pip round trip.
* Measured one-way costs (`measured_costs.csv`, Duka BidClose/AskClose, 2025-11 to
  2026-08, 5000 bars each):

  | Pair | p50 pips | p90 pips |
  |---|---:|---:|
  | EURUSD | 0.4 | 0.7 |
  | USDJPY | 0.5 | 1.2 |
  | AUDUSD | 0.9 | 1.3 |
  | NZDUSD | 1.0 | 1.6 |
  | USDCAD | 1.2 | 1.8 |
  | EURJPY | 0.9 | 2.0 |
  | EURGBP | 0.7 | 1.4 |
  | USDCHF | 0.8 | 1.5 |
  | GBPJPY | 1.7 | 3.8 |
  | GBPUSD | 0.7 | 1.4 |

* Results are in the table in section 2: at measured p50 every pair pin is
  PF 0.85-0.99, and at p90 it is 0.72-0.94. [computed]

## 5. Study #3 - scoring every board call

* **Live call log** (new): `forex_lab/call_log.py` appends every pair's board call to
  `data/paper_shadow/calls.jsonl` (gitignored).
  * Fields: side, raw side, confidence, price, the board's ATR stop/target (recomputed
    with the board's own barrier when the row has none), mute/validity/gate/skip reason.
  * When it runs: on each `shadow_paper_burst.py` pass. It is read-only with respect to
    trading, never Stage-2, and never feeds training.
  * The first pass was written 2026-10-09 (11 calls). So far 1 call is scored, 4 are
    pending their 8 closed bars, and 6 are HOLD/unscorable. Too few to read anything.
* **Scorer**: `scripts/score_calls.py`.
  * Method: triple barrier on the live 1h cache with the call's own barrier distances,
    entry at the next open, same-bar tie counted as SL, 8 closed bars, measured
    round-trip cost.
  * Output: hit rate, expectancy (pips and R) and PF by confidence bucket, by pair and
    by main-eligible.
* **HISTORY (as-of) backfill**: `signals/latest_signals.csv`, the last 64 hourly signals
  per pair (~2026-10-05 to 10-09), with the ATR computed only from bars up to each
  signal bar. Calls are hourly and overlap within 8 bars, so they are **not
  independent**: the effective sample is roughly n/8. [computed numbers; caveat is a
  method note]

  | Confidence bucket | n | Hit rate | Exp. pips (p50 cost) | Exp. R | PF p50 | PF p90 |
  |---|---:|---:|---:|---:|---:|---:|
  | < 0.45 | 58 | 0.48 | +0.24 | -0.04 | 1.02 | 0.87 |
  | 0.45-0.50 | 63 | 0.51 | -0.11 | -0.07 | 0.99 | 0.87 |
  | 0.50-0.55 | 59 | 0.44 | -5.35 | -0.18 | 0.57 | 0.48 |
  | 0.55-0.60 | 49 | 0.55 | -0.70 | -0.00 | 0.93 | 0.80 |
  | 0.60-0.65 | 34 | 0.68 | +13.20 | +0.31 | 3.73 | 3.18 |
  | 0.65-0.70 | 30 | 0.57 | +4.88 | +0.20 | 1.58 | 1.36 |
  | >= 0.70 | 45 | 0.51 | +0.42 | -0.10 | 1.05 | 0.88 |
  | main-eligible (signal BUY/SELL) | 109 | 0.58 | +5.63 | +0.11 | 1.75 | - |
  | not main-eligible | 229 | 0.49 | -1.50 | -0.08 | 0.87 | - |

  * Status: 338 calls scored, 49 pending, 253 unscorable (HOLD).
  * Reading: the 0.60-0.70 band looks better than the rest in this one week, but the
    effect is not monotonic (>= 0.70 is flat). It comes from one week with overlapping
    calls (about 8 independent samples per bucket). That is **a hypothesis to watch in
    the live call log, not evidence**.
  * The 0.50-0.60 band, which is where the exploration book trades, shows no edge
    in this sample.

## 6. Verdict per pair

* **Noise:** USDJPY, EURJPY, AUDUSD, NZDUSD, GBPJPY, USDCAD, GBPUSD, USDCHF, EURGBP,
  the EURUSD pin, and the EURUSD overlap book. Re-priced on real closes with measured
  costs they are PF 0.83-0.99, DSR 0, MinTRL infinite, with 31-51% of months PF > 1.
  Their Replay PF ~3 comes from the Jetta decode. [computed]
* **Doubtful:** the EURUSD clean-data weekly champion a0d93fc6. PF 1.71 / 1.62
  (p50 / p90) on 156 trades, but DSR 0.58 after 67 trials and holdout PF 1.13 on 23
  trades. It needs the held-out exam plus paper shadow before anyone calls it real.
* **Real:** none.

## 7. What is computed vs hypothesis

* Computed:
  * the decode mismatch (corr 0.980 vs 0.034);
  * wickless share and open gaps;
  * the fill advantage per job;
  * yearly/monthly PF;
  * re-sim PF / DSR / MinTRL / PBO;
  * the measured-cost table;
  * call-scoring buckets.
* Hypothesis:
  * that the fill advantage explains *all* of the logged PF gap (strongly supported by
    the pre/post-2022 split and the re-sim, but no clean Replay was re-run, by ban);
  * that the correct Jetta decode is "delta vs the previous bar's same field" (verified
    for Open/bid on EURUSD; High/Low were overwritten by the sanitiser, so they cannot
    be checked from stored data, and the Jetta endpoint returned HTTP 202 / no body
    when re-probed on 2026-10-09);
  * that the 0.60-0.70 board bucket is better.

## 8. Recommended next steps (not done here)

1. **Freeze every Jetta-era Replay result as invalid.** That means all runs from
   2026-09-30 01:01 Dhaka onward, plus pin_scoreboard, weekly champion pins and the
   overlap book. Stop citing "PF 2.7-3.2".
   Status 2026-10-11: those results are marked INVALID in the README, the
   roadmap, the pin scoreboard, the weekly champion pin note, and the overlap
   book. Numbers in this file are unchanged.
2. Fix `decode_jetta_side`: the base should be the previous same field, not the previous
   close. Add a data-integrity gate (wickless share, |Open - prevClose| p50) that
   refuses Replay/backtest on bad bars. Then re-pull history. This needs the user's go:
   it is a data rewrite, and the history files are gitignored but large.
3. Judge everything on the held-out window (`research/holdout.json`, cutoff
   2025-10-01) plus paper shadow, per Roadmap Revision 2026-10-09.
