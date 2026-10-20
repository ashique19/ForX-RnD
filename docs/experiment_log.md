# ForX experiment log

Append-only step log. Every entry: date (Asia/Dhaka), what changed, commit, measured effect,
and for hypotheses the pre-registered decision rule. Paper / demo only; nothing here promotes
a model or changes a live gate, cost, risk, min_conf or session setting.

---

## 2026-10-19 — Explore book scored on MT5 demo truth

### 37272bb — broker-truth exits (explore book)
- Before any paper exit on a mirrored position, MT5 deal history is read (read-only). If the
  broker already closed it (SL/TP/manual), the paper close is booked at the broker's real
  price/time/reason, R off the real fill and broker SL. Unknown broker state holds the exit.
  Bar-path exits now defer over missing FX hours.
- Backfill (append-only, backup `D:\ForX-backup\2026-10-19\pre_broker_truth\`): 32
  `broker_reconcile` annotations (incl. the 6 Oct-16 weekend trades) and 40 `duplicate_entry`
  annotations (same-signal-bar re-entries before e7df6ea).
- Effect: explore R **+0.98** (64 trades, earlier annotations only) -> **+0.76** (24 trades,
  PF 1.09, +8.1 net pips) after broker closes + duplicate exclusion.

### 6f3a4d3 — MT5 spread sampler fix
- Cause of only 11 sampled hours (14–19 Oct): a stale MT5 session made the sampler write
  0 rows for ~26 PC-on hours while logging success; the guard only checked the process
  existed. Plus PC-off periods.
- Fix: 0 valid quotes in FX hours = failure -> reconnect, exit after 4; FX-weekend idle;
  heartbeat file; guard replaces a live-but-stale sampler.

### 927a856 — mirrored exits booked at the real demo close fill
- Paper decides the exit; the demo close is sent in the same pass and the paper close is
  booked at the deal-history fill. A failed demo close books nothing.
- Backfill (backup `...\pre_fill_booking\`): 19 `demo_fill` annotations (18 mismatches + 1
  already within 0.5 pip).
- Effect: explore R **+0.76 -> -0.79** (24 trades, PF 0.92, -33.3 net pips). Paper vs demo
  **51/51** closed demo trades matched within 0.5 pip; -$17.74 on both.

### ea9a217 — burst crash fix + 6 orphan exclusions
- 927a856 shadowed `status` in `explore_paper_burst.py`; the crash came after the paper pass
  but before the mirror, leaving 6 opens (09:02:43Z) never sent to the demo.
- Fix: rename, post-pass steps isolated, mirror always runs for new opens; e2e burst test
  with mocked MT5 + name-shadowing guard.
- Backfill (backup `...\pre_orphan_fix\`): 6 `excluded_no_demo_fill` annotations (no price),
  removed from the paper book. Explore totals unchanged (-0.79R, 24 trades).

### 8c3fc54 — observe-only high-impact news tags
- Source: public Forex Factory weekly JSON (nfs.faireconomy.media/ff_calendar_thisweek.json,
  free, no signup; current week only). Cached per week in `data/calendar/` (gitignored) by
  `scripts/fetch_news_calendar.py` (task `\ForX-NewsCalendar`, logon + every 6 h; offline
  keeps the cache).
- History: FF last-week JSON is not served (404), so FF history starts 2026-10-18. Earlier
  trades use the USD/EUR point-in-time file (official release schedules) only; tags carry
  coverage `full` / `partial` / `none`.
- Entry rows in both books now carry `news_within_min` (event minus entry, minutes),
  `news_event`, coverage. **No trading behaviour change** (tag only, never a gate).
- Backfill (backup `...\pre_news_tag\`): 77 explore + 6 main `news_tag` annotations
  (`exclude_from_explore/stage2 = false`). Coverage: 20 full, 57 partial, 6 none.
- First report (`scripts/news_tag_report.py`): explore (demo fills) near 0 / far 2 (PF 0.17,
  -0.83R) / unknown 22; main near 0 / far 1 / unknown 2. Rule not met -> no filter.

#### Hypothesis H-NEWS-1 (pre-registered 2026-10-19)
High-impact news within +/-60 min of entry hurts after-cost results.

**Decision rule:** consider a news blackout filter **only** after at least **30 tagged
(near) trades** AND tagged PF **< 0.8** while untagged (far) PF **>= 1.0**, both on demo fills
(explore reconciled book). Otherwise: no filter. Any filter would still need the user's OK
and a separate pre-registered replay; this log entry does not authorize one.

### Partial-bar heal: closed bars only + last-3 MT5 re-fetch (2026-10-19 20:18-20:50 Dhaka)
- **Why:** the stuckness monitor (20:18 Dhaka) found 1h desk CSVs holding bars saved before
  their hour closed, never overwritten: GBPUSD/EURGBP 13:00 UTC = one tick (written 13:01Z),
  USDCHF 13:00 written 13:44Z, USDCAD had a forming 14:00 bar. `refresh_if_gap.py` saw the
  hour as present and never refetched, so signals could rest on incomplete bars.
- **Root causes (two):** (1) writers kept MT5's forming bar (guard allowed `<= floor(now)`);
  (2) the MT5 terminal's local H1 history can lag the live tick right after a symbol is
  (re)selected: at 14:16Z it returned 72 bars ending at a half-filled 13:00 bar for 9 pairs
  (meta: overlap 71 of 72), and at 14:42Z still did for GBPUSD/EURGBP. So even bars written
  after the close were partial.
- **Fix (code only, no yaml):** `data.drop_forming_bars` (open + 1h <= now UTC) on every 1h
  writer (MT5, Dukascopy, yfinance incremental + full); each MT5 refresh overwrites the last
  `HEAL_CLOSED_BARS=3` closed bars (closed-bar divergence guard waived there only when the
  cached bar lies inside the MT5 bar's range, i.e. a sub-range partial; other divergence
  still refuses); `mt5_feed.bars_1h_synced` re-requests (6 x 1 s) until the newest bar reaches
  the last tick's hour and otherwise refuses the MT5 write (falls back as for "unavailable");
  `.mt5.json` records `healed_bars` / `forming_dropped`; `refresh_if_gap.py` also refreshes a
  pair whose tip bar is forming or whose CSV was written before the tip hour closed
  (`--no-heal` to disable); `signals.generate_signals` ignores a forming last 1h bar.
- **Data step:** backup `D:\ForX-backup\2026-10-19\pre_partial_bar_fix\` (58 files incl.
  signals). Desk API restarted (START_DESK -Force) to load the code; dry run flagged EURGBP,
  GBPUSD, USDCHF (written_before_close) and USDCAD (forming_bar); then POST /refresh (market
  data only) for all 10 FX pairs.
- **Measured effect (bars healed vs backup, last 3 closed hours 11-13Z):** AUDUSD 1 (13:00),
  EURGBP 2 (12:00 vol 2590->5970; 13:00 one tick vol 1->7001), EURJPY 1, EURUSD 1, GBPJPY 1,
  GBPUSD 2 (12:00 vol 5777->11999; 13:00 one tick vol 1->13056), NZDUSD 1, USDCHF 1,
  USDJPY 1, USDCAD 0 healed + 1 forming bar (14:00) removed. Total 12 partial bars healed,
  1 forming bar dropped. After: all 10 CSVs end at 13:00Z, 0 forming bars, OHLC for 11-13Z
  identical to a fresh synced MT5 read (volume equal except USDCHF, rescaled x0.658 by design).
- **Signals regenerated per pair:** final signal at 13:00Z is HOLD on all 10 before and after.
  Raw class changed on 2: EURUSD HOLD->SELL (conf 0.370->0.376), GBPJPY HOLD->SELL
  (0.491->0.440), both below the 0.45 explore and 0.60 main floors -> no trade impact.
  Confidence moved on others (e.g. USDCHF 0.649->0.529, USDJPY 0.706->0.601, NZDUSD
  0.581->0.655, all HOLD). USDCAD's forming-bar 14:00 signal row is gone.
- **Tests:** new `tests/test_partial_bar_heal.py` (9). Suite: 683 passed; the same 13 failures
  exist at HEAD without this change (api_decision/board/daily_cache/learnings/workspace,
  pre-existing; untracked stale `tests/test_history_decode_fix.py` ignored).
- **Open:** earlier explore/main entries may have been scored on partial tip bars before this
  fix; not re-audited here.

### Audit: entries decided on forming/partial 1h bars (2026-10-19 23:53 Dhaka, read-only)
- **Why:** after the partial-bar fix (0641a94) - were past main/explore calls scored on
  incomplete bars, and does that change the scorecard? Read-only: no journal, data, model
  or yaml change. Script `scripts/audit_partial_bar_entries.py` (+7 tests).
- **Method:** signal bar per entry from the open row / matching explore decision. Truth =
  fresh read-only MT5 H1 (cache == MT5 OHLC on 699/699 overlap bars per pair). Each entry
  re-scored with the frozen models (unchanged since 2026-10-09 17:27 Dhaka) twice: on the
  closed bar, and "as used" (forming bar rebuilt from MT5 M1 up to the entry minute; for a
  stale tip, the earliest M1 cut-off that reproduces the journal). A verdict is only given
  when the replay reproduces the journaled side and conf within 0.02 ("faithful").
- **Control:** replaying decisions against later CSV snapshots matched only where data
  was stable (pre_mt5_tip 53/55, 2026-10-17 weekend 63/67 on bars >= 75 min old). Other
  snapshots did not match, and that exposed a **second data bug (not fixed here):** the
  MT5 volume rescale is self-referential. The cache's volume scale flipped from
  cache/MT5 ~0.25-0.56 to 1.0 for bars from 2026-10-14 onward on 9 of 10 pairs (USDCHF
  stays 0.658). The flip happened between the 2026-10-17 02:13 and 2026-10-19 20:16
  snapshots, so the cache now has a 2-4x volume step that feeds vol features.
- **Findings, exploration (84 entries):** 43 were scored on a FORMING bar (entry 3-60 min
  into the hour). 41 were on a "stale tip" (bar closed, but it was still the newest bar,
  so the cache had not been refreshed). Of those 41, 33 reproduce only with a partial bar
  cut at minute 1-12 (27 or 39 for 3; a 59-cut search, so a chance match is possible),
  7 reproduce on the full closed bar, and 1 is unverifiable. **Affected: 77 of 84; zero
  closed (counted) trades were on a verified-complete bar.** Faithful verdicts: 40 still
  fire on the closed bar, 16 would not, 21 unverifiable.
- **R (reconciled demo-fill basis, same as explore_sum_r.py; 32 closed):** all 32 counted
  trades are affected; total +1.77R, PF 1.16, +46.3 net pips. Would not fire on the closed
  bar: 6 trades, +3.05R. Still fire: 15 trades, -0.75R, PF 0.87. Unverifiable: 11 trades,
  -0.53R. Without the would-not-fire trades: 26 trades, -1.28R, PF 0.88. **Scorecard
  impact:** the positive exploration total comes entirely from calls the closed-bar policy
  would not take. As a test of the policy the desk now runs (closed bars only), the book
  shows no edge (-0.75R on the 15 verified trades). Pre-fix R stays a valid record of what
  was traded, but it is not evidence for the closed-bar policy.
- **Main (4 model fills):** all 4 were on forming bars. NZDUSD (+1.0R) still fires on the
  closed bar (faithful). GBPUSD (already excluded, retrain) still fires. EURJPY (-0.05R)
  and USDCHF are unverifiable; USDCHF's closed bar scores HOLD 0.556. No main verdict
  changes a gate. Stage-2 stays far from 4 green weeks.
- **Exits:** 31 bar-path paper exits re-run on true MT5 bars: 0 differ (reason and bar).
- **Proposed, NOT applied (needs the user's OK):** one append-only annotation per explore
  and main position: `annotation_type: "signal_bar_audit"`, `audit_version:
  "partial_bar_audit_v1"`, `bar_status` (forming / stale_tip / closed), `partial_proven`,
  `verdict` (still_fires / would_not_fire / unverifiable / closed_bar_reproduced),
  `closed_bar_raw`/`closed_bar_conf`, `as_used_conf`, `exclude_from_explore: false`,
  `exclude_from_stage2: false` (observe-only; totals unchanged). explore_sum_r.py would
  then add a "closed_bar_policy" view (verdict != would_not_fire). From 0641a94 on, new
  entries are closed-bar by construction. The book should be judged on post-fix entries.

## 2026-10-20 00:35 Asia/Dhaka: MT5 volume anchor fix, signal_bar_audit annotations, MT5 exec refusals as errors

**1. MT5 volume-rescale drift (fixed).**
- **Root cause:** the PC was off from about Oct 15 evening, so the cache ended at Oct 15 19:00-20:00 UTC.
  The next incremental MT5 refresh (72 bars, Oct 13 21:00 UTC onward) overlapped the cache on only
  47-48 bars with volume on both sides. That is below `MT5_MIN_OVERLAP=50`, so `mt5_volume_ratio`
  fell back to 1.0 and wrote raw MT5 tick volume. A later positive volume wins the merge, so this
  replaced the scaled volume of every bar from Oct 13 21:00 UTC on.
- Every later refresh then re-derived cache/MT5 from bars MT5 had already written: 1.0, now "confirmed"
  over 70+ bars. The error kept itself going on 9 of 10 pairs. USDCHF was rewritten by a 2000-bar
  bootstrap at 0.658 and was unaffected.
- **Fix (`forex_lab/mt5_volume_anchor.py`, `try_mt5_refresh`):**
  - One frozen factor per pair, stored in `data/mt5_volume_anchor.json` (gitignored data). The factor
    is never re-derived from MT5-written bars.
  - Factor = median(pre-MT5 cache Volume / MT5 tick_volume) over the last 500 overlapping bars. It
    comes from the pre-MT5 cache snapshot `D:\ForX-backup\2026-10-14\pre_mt5_tip` (source
    yfinance_1m/Dukascopy; 0 bars equal MT5 OHLC). That is the same window the first MT5 bootstrap
    used, so it equals the factor already on the Jun 24 to Oct 13 MT5 bars and adds no new step.
  - With no anchor and an overlap under 50, the write is refused (`mt5 guard: volume scale unknown`,
    no fallback). It never silently writes 1.0.
  - The meta file now records `volume_scale_source` and `volume_ratio_live_overlap`, for drift monitoring.
  - For transparency, the all-overlap median and the data/history-file median are stored next to each
    anchor. They differ noticeably on some pairs (USDCHF 0.40 / 0.37 vs 0.658; EURUSD 0.44 / 0.43 vs
    0.358): a 500-bar median of a noisy ratio (p10-p90 about 0.2-0.7). That is not changed here,
    because rewriting the Jun 24 to Oct 13 MT5 bars was out of scope.
- **Repair:** backed up to `D:\ForX-backup\2026-10-20\pre_volume_fix\`.
  `scripts/mt5_volume_anchor.py rewrite --since "2026-10-13 21:00" --apply` set Volume =
  round(MT5 tick_volume x anchor) for the 93 bars from Oct 13 21:00 to Oct 19 17:00 UTC. It runs only
  when every bar equals MT5 OHLC exactly; OHLC text in the CSV is byte-identical, checked.
  - The desk, restarted 00:18 Dhaka with the new code, had already fixed the last ~71 bars through its
    own anchored refresh. The script changed the remaining 22 per pair (8 rounding changes on USDCHF).
- **Factor on bars since the seam (before -> after):**

  | Pair | Before | After |
  |---|---|---|
  | AUDUSD | 1.0 | 0.2846 |
  | EURGBP | 1.0 | 0.4389 |
  | EURJPY | 1.0 | 0.3487 |
  | EURUSD | 1.0 | 0.3582 |
  | GBPJPY | 1.0 | 0.3551 |
  | GBPUSD | 1.0 | 0.2540 |
  | NZDUSD | 1.0 | 0.4864 |
  | USDCAD | 1.0 | 0.3385 |
  | USDCHF | 0.658 | 0.6579 |
  | USDJPY | 1.0 | 0.5628 |

- **Seam check** (median volume of 14 days after / before Oct 13 21:00; after = 93 bars):

  | Pair | Before fix | After fix |
  |---|---|---|
  | AUDUSD | 3.74 | 1.06 |
  | EURGBP | 2.33 | 1.02 |
  | EURJPY | 2.81 | 0.97 |
  | EURUSD | 3.18 | 1.13 |
  | GBPJPY | 2.96 | 1.05 |
  | GBPUSD | 4.27 | 1.08 |
  | NZDUSD | 2.07 | 1.01 |
  | USDCAD | 3.22 | 1.09 |
  | USDCHF (control) | 1.14 | 1.14 |
  | USDJPY | 1.90 | 1.07 |

  Prices: all 1999 MT5 bars in the cache (from Jun 24 11:00) equal MT5 OHLC exactly. The 15 Friday
  21:00 cache bars that MT5 lacks are untouched.
- **Signals** (vol_z is a 20-bar rolling z-score, so only bars within 20 h of the seam can move):
  - Backup vs fixed CSVs, Oct 13 21:00 to Oct 19 16:00 UTC, 92 bars per pair. **6 historical calls
    changed, all on Oct 14 UTC:**
    - AUDUSD 01:00: HOLD -> BUY 0.616
    - EURGBP 02:00: BUY 0.620 -> HOLD
    - EURGBP 06:00 and 08:00: HOLD -> SELL 0.605 / 0.628
    - GBPJPY 06:00 and 07:00: SELL 0.618 / 0.636 -> HOLD
  - The latest call is unchanged on all 10 pairs (HOLD). Regenerated per pair with
    `python -m forex_lab signals --pair X`; all CSVs present, exit 0.
- **Influence:** the live calls back then saw correctly scaled volume. The flip was written later, and
  vol_z is scale-free once the window is past the seam. So past decisions are not affected; only the
  retroactive history and any future training or replay on it were. That is now repaired and guarded.

**2. signal_bar_audit annotations (applied, observe-only).**
- Journals were backed up first to `...\pre_volume_fix\paper_shadow\` and `...\pre_annotation\`.
- `scripts/apply_signal_bar_audit.py --apply` used the `audit_partial_bar_entries.py --json` output
  (verdicts identical to the audit). It appended 84 explore + 4 main annotations (`partial_bar_audit_v1`,
  `exclude_from_explore: false`, `exclude_from_stage2: false`).
  - Verdicts across both books: still_fires 42, unverifiable 23, would_not_fire 16,
    closed_bar_reproduced 7.
  - Explore sum_r stays +1.7682 and main exclusions are unchanged (2/2). The script is idempotent.
- `scripts/explore_sum_r.py` keeps every existing key, value and line. Two new keys come after
  `reconciled`, so the only textual change is a comma on its closing brace, and the JSON still
  parses whole:
  - `post_fix_only` (opens after the 0641a94 commit, 2026-10-19 14:47:39 UTC): 0 entries so far,
    sum_r null.
  - `closed_bar_view` (excludes the would_not_fire entries): **-1.28R over 26 closed, PF 0.875,
    +15.6 net pips** (68 entries, 16 excluded).

**3. MT5 demo refusals are now errors (steering item).**
- At 00:19 Dhaka the explore burst's demo closes were refused (retcode 10027, AutoTrading disabled by
  client; tickets 43778885/43778886), yet the burst exited 0. The cause: close failures sat only in
  `broker_exits.errors`, and the burst counted only exceptions.
- Now `explore_paper_burst.record_exec_failures` copies every failed or refused open or close (with
  retcode and ticket), plus any connect refusal, into `mt5_exec.errors` and `step_errors`, so the burst
  exits 4.
- `mt5_exec.trade_allowed_error` is a read-only pre-check. It logs `terminal_info().trade_allowed=False`
  as an error in mirror and close_and_book (and in the reconcile result) before sending. It never
  toggles the setting.
- **Why Algo Trading was off:** the terminal journal shows "automated trading is disabled because
  profile has been changed" at 2026-10-19 23:32:27 Dhaka. The chart profile files Default
  (23:32:18) and Euro (23:33:10) were saved at that moment, i.e. someone switched the chart profile.
  - That is a terminal UI action. The Python MetaTrader5 API (initialize, copy_rates, shutdown), which
    is all the volume work used, cannot switch profiles or toggle Algo Trading.
  - The terminal has not restarted since 13:27 Dhaka, and no account change was logged.
  - It is still off now (`terminal_info().trade_allowed=False`, read-only check). Until it is turned
    back on in the terminal, bursts with demo sends will exit 4.
- Tests: `tests/test_mt5_volume_anchor.py` (10), `tests/test_mt5_exec_errors.py` (6) and
  `tests/test_signal_bar_audit_views.py` (4). Full suite: 710 passed, plus the same 13 pre-existing
  failures (unchanged).
## 2026-10-20 04:05 Asia/Dhaka: stop / payoff analysis (read-only)

**Question:** do the losses come from stops not kept, stops too wide, time exits, or payoff?
Script: `scripts/stop_payoff_analysis.py` (read-only; reads the journals and MT5 deal history only).
Bars: `data/<PAIR>_1h.csv`, MT5 broker OHLC, finished bars only. `data/history` ends Oct 9-12, so it
cannot cover these trades.

- **Config:** `barrier.sl_atr 2.0`, `tp_atr 2.0`, horizon 8 bars, ATR14. The stop distance equals the
  target distance (SL/TP = 1.0, R:R 1:1), so the stop is not set further than the target.
  - Measured at entry: median SL 26.2 pips = median TP; SL 1.97 ATR (range 1.71-2.15).
  - Median ATR 13.4 pips, median 1h range 11.4 pips, cost 1.4 pips (5% of TP).
- **Explore, 33 reconciled closed trades:**
  - Win rate 54.5%. Avg win +0.72R (+19.1 pips); avg loss -0.83R (-21.1 pips). Payoff 0.88, so the
    breakeven win rate is 53.3%.
  - Total +0.63R, +28.1 net pips, PF 1.05.
  - Exits:

    | Exit | Trades | Wins | R | Net pips |
    |---|---|---|---|---|
    | Target | 11 | 11 | +11.60 | +306 |
    | Stop | 10 | 0 | -10.17 | -259 |
    | Time | 12 | 7 | -0.80 | -19 |

- **Closed-bar-valid subset (26):**
  - Win rate 50.0%. Avg win +0.69R (+20.0 pips); avg loss -0.79R (-18.8 pips). Payoff 0.88.
  - Total -1.28R, PF 0.875.
  - Exits:

    | Exit | Trades | R |
    |---|---|---|
    | Target | 8 | +7.88 |
    | Stop | 8 | -8.03 |
    | Time | 10 | -1.13 |

- **Stops honored:** one loss beyond -1.1R. NZDUSD went -1.14R: its broker SL filled 2.2 pips beyond
  plan at 21:05 UTC rollover.
  - The other 7 broker SL fills slipped 0.00-0.38 pips.
- **MT5 $ (27 mirrored trades):** total -$0.48 against $69.12 planned at the targets. 6 losses
  exceeded the planned $ win:
  - 4 are Oct 15 trades sent with the paper's absolute SL/TP while the fill was 1-3 pips off, so the
    broker stop/target distance came out at 1.09-1.23:
    - GBPUSD -$3.49 vs +$2.82 planned
    - AUDUSD -$2.17 vs $1.95
    - EURGBP -$1.72 vs $1.57
    - NZDUSD -$1.89 vs $1.87
  - Since the re-anchor fix, every mirror is at exactly 1.00.
  - NZDUSD -$1.86 vs $1.60: rollover slippage.
  - EURJPY -$2.76 vs $2.75: one cent of rounding.
- **MAE/MFE:**
  - Winners: median MAE 0.01R, worst 0.85R; 3/18 went past 0.5R against.
  - Losers: median MFE 0.19R; 2/15 reached +0.5R, 0 reached +1R.
  - Time exits: median MFE 0.61R, MAE 0.69R.
- **SMALL-SAMPLE WHAT-IF (not a gate result).** Same 33 trades, same targets and horizon, finished
  bars, SL wins a same-bar tie, 1.4-pip cost.
  - The bar replay of the current setting gives -0.62R (PF 0.95), while the journal shows +0.63R:
    3/33 exits differ because bars are bid only and broker fills differ.
  - Results:

    | Variant | Win rate | Total R | PF | Net pips | Note |
    |---|---|---|---|---|---|
    | Current (2.0 ATR = 1.0x TP, the user's stop <= target rule) | 48% | -0.62 | 0.95 | +20.5 | |
    | Breakeven at +1R | | identical | | | +1R equals the target at 1:1 |
    | Breakeven at +0.5R | 39% | -0.64 | 0.94 | | |
    | 1.75 ATR | 45% | -3.00 | 0.80 | | |
    | 1.5 ATR = 0.75x TP | 45% | -3.5 / -3.0 | 0.80 / 0.83 | | |
    | 1.25 ATR | 45% | -1.04 | 0.94 | | |
    | 1.0 ATR | 45% | +2.02 | 1.10 | | 6/33 stops inside the median 1h range |
    | 0.5x TP | 45% | +2.75 | 1.14 | | +1.38R at a fixed lot; 12/33 stops inside the median 1h range |

  - No variant put the stop inside 1.5x the spread.
  - The ranking is not monotonic (0.75x is worse than both 1.0x and 0.5x), which reads as noise at
    n=33. The earlier 1.75 ATR Replay (`tight_stop_175_dd`, EURUSD 2015-) was null on max drawdown
    (-4.8% vs -2.3%).
- **Main, 4 model fills:** SL = TP on all (1.7-2.1 ATR).
  - EURJPY: time exit, -0.05R.
  - NZDUSD: +1.0R.
  - GBPUSD: excluded.
  - USDCHF: no journaled close.
- **Verdict:** stops are kept, and they are not wider than the targets. The problem is payoff:
  - At 1:1, the costs and the small time-exit results pull the average win (+0.72R) below the average
    loss (-0.83R).
  - A 54.5% win rate only just clears the 53.3% breakeven, and the closed-bar subset (50%) does not.
  - Time exits do not cause the losses (-0.8R over 12); they cap the winners.
  - Tighter stops are not supported at this sample size. A change needs a pre-registered Replay; this
    entry does not authorize one.
## 2026-10-20 04:15 Asia/Dhaka: daily-trend (higher-timeframe) alignment what-if (read-only)

**Question:** would taking only hourly trades in the daily trend's direction help?
Script: `scripts/daily_trend_alignment.py`, with tests.

**1. Existing higher-timeframe information**
- **Live models:** none use daily or 4h inputs. All 20 joblibs carry 36-37 hourly features; the longest
  lookbacks are `ret_24`, `sma_50_ratio` and `range_pos_50` (24-50 hours).
- **Off by default in `config/default.yaml`:**
  - the daily/4h feature code (`feature_extras.higher_tf: []`, so no `tf_1D_*` / `tf_4h_*` columns)
  - prior-day levels (`levels.enabled: false`)
  - the hourly HTF trend filter (`signals.htf_trend_filter: null`)
- **Badge only:** the desk shows a 4h `mtf_confirm` agreement badge (`conflict_flash: off`, not a gate).
- **Tried before:** `reports/gate_screen.md`, EURUSD walk-forward. Gating on 4h trend agreement plus
  min confidence lowered PF from 0.889 to 0.847, and to 0.793 at min confidence 0.50, so it was kept
  off. A daily filter and daily features were never tested.
- **Banned?** No explicit ban list names this family.

**2. SMALL-SAMPLE WHAT-IF, not a gate result**
- **Method:** daily bars resampled from finished 1h MT5 bars. The FX day ends 17:00 New York; days with
  fewer than 12 bars are dropped. Only days completed before the entry count. Three definitions were
  fixed in advance:
  - A: daily close vs its 20-day SMA
  - B: 20-day SMA vs 50-day SMA
  - C: the prior day's close vs its open
  - ALL: A, B and C agree
- **Sample:** the 33 trades fall on only 11 distinct entry times, so there are far fewer independent
  bets than trades.

| Definition | 33 aligned | 33 counter | 26 closed-bar aligned | 26 closed-bar counter |
|---|---|---|---|---|
| A close vs SMA20 | 12 trades, 42% won, -1.71R, PF 0.70 | 21, 62%, +2.34R, PF 1.35 | 10, 50%, +0.44R, PF 1.12 | 16, 50%, -1.72R, PF 0.74 |
| B SMA20 vs SMA50 | 13, 54%, +0.23R, PF 1.05 | 20, 55%, +0.40R, PF 1.05 | 12, 58%, +1.24R, PF 1.34 | 14, 43%, -2.52R, PF 0.62 |
| C prior day body | 13, 46%, -2.02R, PF 0.72 | 20, 60%, +2.65R, PF 1.51 | 9, 44%, -1.05R, PF 0.79 | 17, 53%, -0.24R, PF 0.96 |
| ALL agree | 5, 40%, -1.05R | 28, 57%, +1.68R | 4, 50%, -0.04R | 22, 50%, -1.24R |

- **Main fills:**
  - EURJPY SELL (-0.05R) and NZDUSD SELL (+1.0R) are aligned on A and B, counter on C.
  - GBPUSD (excluded) is the same.
  - USDCHF (no close) is counter on A and B.

**Verdict: not promising.**
- The sign flips between definitions and between the full set and the closed-bar subset.
- On all 33 trades, counter-trend did better on A and C, and B showed no difference.
- Only B on the 26-trade subset looks good, which is what one would expect to find by chance among
  8 cuts of 11 clusters.
- The one prior walk-forward test of a 4h gate lowered PF.

**3. No hypothesis drafted.** That step was conditional on a promising result. A daily SMA20/50 filter
or `tf_1D` features would need a pre-registered walk-forward over all 10 pairs on Dukascopy history
before any live use; this entry does not authorize one.
## 2026-10-20 04:12-04:45 Asia/Dhaka - Pre-registered 15-variant study: daily trend, flip exit, trend holds, profit-lock, dynamic target (research only)
- Prereg commits came first, before any run or result:
  - 85b657b, 53c2322, 9ac69ea, 701e799, 8eaecd9 (final: 15 variants, Bonferroni 0.05/15).
- Code: `research/htf/` and tests.
- Harness: champion recipe walk-forward (`walk_forward_backtest`) on data/history for 10 pairs.
  - Run serially at BelowNormal priority. In-memory `compare_logistic=false`. Yaml untouched.
  - Holdout cutoff 2025-10-01 enforced. Measured p90 costs. Swap 1 pip per rollover (Wednesday x3) for the hold family.
- Code check:
  - There is no flip, opposite-signal or trailing exit anywhere. Every engine exits on TP, SL or the 8-bar horizon only (see PREREG_FLIP_EXIT / PREREG_TREND_HOLD for the file lines).
  - exit_hold=3 and horizon 6 were not flip exits.
- In-sample results (afea42b, written before the holdout): the baseline is 23,723 trades, PF 0.760, -2,607R.
  - Only H120_TP4 passed its own bar plus Bonferroni (PF 0.804).
  - The F1/F2 monthly-R gains come only from trading less.
- Holdout, scored once: 0 of 15 pass. H120_TP4 PF was 0.736 vs 0.716.
  - The baseline loses on all 10 pairs (PF 0.72 at p90, 0.87 at p50).
- Full tables: `research/htf/RESULTS_20261020.md`. Outputs are in ignored data/edge_check/htf_20261020/.
- Verdict: no edge from any of these ideas. No rule or live change.
- Side note, read-only:
  - Hourly RSI14 (`rsi`) is live in all 10 champions. No daily RSI exists.
  - `vol_z` (20-bar z of tick volume) is live in 9/10 champions, not in EURUSD.
  - `volatility` and `vol_regime` are return-based, not volume-based.
  - No RSI or volume family has been tested or banned.
## 2026-10-20 08:14-09:12 Asia/Dhaka - EURUSD desk CSV: old yfinance segment replaced with validated history; W43 gate rerun (report-only)

- **Trigger.** The W43 weekly gate (08:14) was refused by the bar-quality guard: median |Open - prev Close| was 1.09 pips (limit 1.0).
- **Diagnosis.** The bad segment of `data/EURUSD_1h.csv` is **2024-09-30 20:00 -> 2026-06-19 01:00 UTC (10,565 bars)**, not just to 2025-10.
  - Monthly median gap was 1.06-1.39 pips. Prices were long yfinance floats, offset about 3-4 pips from Dukascopy bid.
  - From 2026-06-19 02:00 the desk bars agree with data/history within 0.2-0.3 pip, with a median gap of 0.0-0.1.
  - Volume in the bad segment already equals the Dukascopy tick volume exactly (ratio 1.00). That comes from the earlier tip-volume repair.
- **Option chosen: splice, not trim.**
  - Retrain has no data-dir option. Pointing it at data/history would need a yaml or config change, and the history ends 2026-10-12, missing the latest week.
  - Trimming the bad segment would leave only about 2,100 bars, roughly one training window.
  - The splice fixes the file every desk and gate path already reads. The weekly gate will not fail again on this file.
- **Tool.** `scripts/splice_history_segment.py` (cdd5dbf, with tests):
  - Bars at or after the seam are kept as byte-identical text lines.
  - Bars before the seam are taken verbatim from data/history (validated 2026-10-15; bid OHLC, Dukascopy tick volume). No synthesized prices.
  - It waits for a desk refresh write, then builds a temp file and swaps it with `os.replace`, but only if the source mtime did not change.
- **Backup.** `D:\ForX-backup\2026-10-20\pre_eurusd_csv_fix\` holds:
  - `EURUSD_1h.csv`, the exact pre-swap file read at 09:10;
  - `EURUSD_1h.csv.snapshot_0820`;
  - the `.mt5.json` meta and `.source` files;
  - `latest_signals_before_0820.csv`.
- **Swap.** Atomic, at 09:10:27 Dhaka, right after the 09:10 desk refresh. The result has 12,775 rows. 10,670 history bars replaced 10,565 old ones:
  - 229 hours were present only in history (mostly hours yfinance missed);
  - 124 hours were present only in the old file (mostly yfinance Friday 21:00 after-close stubs).
- **Verification.**
  - The guard passes: median gap 0.10 pip, wickless share 0.2%.
  - No duplicate hours. All bars after the seam are identical to the pre-swap file.
  - Seam: 01:00 Close 1.14531 -> 02:00 Open 1.14533, a 0.2 pip gap.
  - Gaps: 107 normal weekends, plus 6 holiday/DST gaps (Christmas, New Year, DST Sunday). There are also 5 small holes in the validated history on 2026-05-12 and 2026-05-22, 2-4 h each. They were left unfilled, not synthesized.
  - Signals: generate_signals on the pre-swap and post-swap frames gives an identical last 64 EURUSD rows. The 09:10:55 desk `latest_signals.csv` overlaps the 08:11 snapshot on 63 rows with 0 differences.
- **W43 gate**, `python -m forex_lab retrain --json`, report-only, no --promote:
  - Verdict **null, keep champion**.
  - Challenger walk-forward: PF 0.906, return -4.26%, max DD -5.90%, 547 trades.
  - Champion record: PF 0.926, return -6.67%, max DD -8.35%, 1,089 trades.
  - The PF did not improve, and it fails the 1.05 floor.
  - Not promotable. Champion not written, nothing trained. Only `data/champion/EURUSD_challenger.json` and the history line were written.
- **Other 9 desk CSVs (read-only).**
  - 8 of them (USDJPY, GBPUSD, AUDUSD, USDCAD, USDCHF, EURJPY, EURGBP, GBPJPY) start 2026-06-19 and are clean: guard median 0.0-0.1 pip.
  - **NZDUSD carries the same old segment.** It runs 2024-10-13 -> 2026-06, with 21 months more than 1 pip off history and 5 months with a median gap over 1 pip.
    It passes the guard today only narrowly (0.668), so it is still bad data. Not changed; the same splice would fix it if approved.
- No yaml edits. No data CSV committed.