# Frozen rules: forward paper shadow of two low-frequency candidates - 2026-10-22

Paper only. **No MT5 orders.** No desk trading endpoints, no main/explore journals, no yaml.
- **Script:** `scripts/forward_shadow_lowfreq.py`, scheduled task `\ForX-ForwardShadow` (daily 03:20 Asia/Dhaka, after the 20:59-21:05 UTC broker break; runs as soon as possible after a missed start).
- **Journal:** `data/forward_shadow/forward_journal.jsonl` (gitignored, never committed; included in the nightly encrypted ForX-DataBackup).
- **Source of the rules:** prereg 0fd7527 (lowfreq) and 95c0ab3 (swap re-test). Both candidates were picked **after** seeing in-sample results (post hoc), so only forward data can confirm them.

## Rules (frozen; any change = a new tracker and a new trial in the trial count)
- **Bars:** hourly bars from validated `data/history/<PAIR>_1h.csv`, extended **only for timestamps after its last bar** by the desk tip cache `data/<PAIR>_1h.csv`.
  - The overlap check on 2026-09-01..10-12 gave median |close diff| 0.2-0.9 pips and a maximum of 14 pips on GBPJPY.
  - Daily bars use the 21:00 -> 21:00 UTC trading day (the Friday 21:00 stub joins Friday, the Sunday open joins Monday). Only closed bars are used. Missing hours are never filled.
  - A date is processed once every pair has that closed bar. If a pair is still missing 3 days later, the date is processed without it (logged).
- **T1 MOM120** (10 pairs; **primary cases EURUSD and USDJPY**, the other 8 are a label):
  - At each daily close, target = sign(Close_D / Close_{D-120 daily bars} - 1). It is always in the market.
  - On a change, exit and reverse at the next daily bar's Open.
  - Risk unit = 2 x ATR14 (daily, true range) at the decision bar.
- **T2 CARRY3** (pooled book = the primary case):
  - At each Friday daily close, d = r_base - r_quote from BIS central-bank policy rates (data.bis.org WS_CBPOL, value of D minus 1 calendar day). Pairs with |d| < 0.25 pp are ineligible.
  - Hold the top 3 by |d|, long the higher yielder. Changes execute at the next daily Open.
  - Risk unit = 2 x ATR14 daily at the decision.
- **Costs:** round trip = 2 x one-way p90 from `data/paper_shadow/measured_costs.csv` (frozen at these values), in pips:
  EURUSD 1.4, USDJPY 2.4, GBPUSD 2.8, AUDUSD 2.6, NZDUSD 3.2, USDCAD 3.6, USDCHF 3.0, EURJPY 4.0, EURGBP 2.8, GBPJPY 7.6.
- **Financing:** the measured MT5 demo swap of the held side (read-only `symbol_info`, snapshotted each run).
  - Per weekday 21:00 UTC rollover, return += swap% / 100 / 360; the Wednesday rollover counts x3, giving 7 nights per week.
  - Catch-up days use the snapshot of the run that books them. If MT5 is unreachable, the last journaled snapshot is used (tagged).
- **Forward start:** day 0 = the first run (2026-10-22). It records state only. The first forward decision is the first daily close **after** the init time.
  Nothing before day 0 is booked.

## Pre-declared evaluation (3 primary cases: T1 EURUSD, T1 USDJPY, T2 pooled)
- **Interim review at 6 months (2027-04-22):** descriptive only. No pass/fail except the kill switch.
- **Final review at 12 months (2027-10-22),** or later once each case has >= 6 closed trades, capped at 24 months. A case with fewer than 6 closed trades at the cap is "not shown".
- **PASS per case, all of:**
  1. Closed-trade PF (p90 costs + measured swaps) > 1.10, and net R (closed + open marked) > 0.
  2. One-sided t-test that the mean daily mark-to-market change in R (days with an open position) is > 0, with p < 0.05 / 3 = **0.0167**.
  3. Max drawdown of the daily marked equity no worse than 1.5 x the in-sample max DD: T1 EURUSD 16.4R, T1 USDJPY 24.9R, T2 12.6R.
- **Kill switch:** breaching the 1.5 x DD limit at any time fails that case early. The tracker keeps recording.
- **Trial accounting:** the discovery count stays N = 156. This forward test is a separate confirmatory test of 3 pre-declared cases at alpha 0.0167, on data unseen at freeze time.
- A PASS produces only a written report to Ashiqul. Nothing is wired to the desk or MT5.
