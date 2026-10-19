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
