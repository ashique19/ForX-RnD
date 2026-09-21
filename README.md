# Forex Research Lab

Research-only **BUY / SELL / HOLD** signal pipeline with walk-forward success-rate metrics.

**You trigger trades manually.** This project has **no live broker APIs** and places **no live orders**. Paper Buy/Sell on the screen records a **local practice fill** only (`broker.backend: paper`).

## Disclaimer

- Not financial advice. For education / research only.
- `yfinance` FX data (`EURUSD=X`, etc.) is **not** the same as your broker’s executable quotes (spreads, liquidity, session gaps differ). The watchlist **Last** is last/mid-ish from that cache; **Spread** is the config pip estimate; **Session** is a UTC clock badge — none of these are live broker truth.
- Paper uPnL is a local mark vs last/mid-ish — **not** live broker PnL. `BrokerPort` is unchanged.
- Paper RIGHT/WRONG is a local lookback vs cached bars (TP/SL or horizon). It is **not** a live edge.
- Past backtest metrics **do not** predict future results.
- Always paper-trade and validate independently before risking capital.
- Beating SMA / always-long in this lab is a **research signal**, not a deployable edge (slippage, weekend gaps, and broker quotes are not modeled).
- News headlines (Google News RSS) can be **late, incomplete, or wrong**. The bias note is a keyword heuristic on fetched titles only — not a trade instruction.
- The event calendar uses an **unofficial** weekly Forex Factory JSON dump (`nfs.faireconomy.media`). Times can be revised; confirm on Fed / BLS / ECB / BoE sources. Fail-soft if offline.
- Advisory cards (no new opens / hold / close / tighten SL) are **decision support**. They never auto-submit via `BrokerPort`. MTF badges are causal SMA slope on the same CSV — not a live trend service.
- Selective **open gates** (`gates.enabled`, default **off**) can HOLD a BUY/SELL flash and disable Paper BUY/SELL when MTF disagrees, confidence is below the floor, or a high-impact event window is live. Missing calendar or MTF **fail-soft** (no crash, no invented block). EURUSD walk-forward did not clear the PF / total return / max DD bar — see `reports/gate_screen.md`. Not a live edge.
- Extra TA (`feature_extras.pandas_ta`) and FRED macro (`feature_extras.fred`) packs are **off by default**. A EURUSD walk-forward screen showed a ~0.01 PF tick for TA (fold noise, still PF < 1) and a null FRED pack with a slightly worse drawdown — not an edge. FRED uses an as-of lag (not ALFRED vintages). A missing `FRED_API_KEY` is fine — public CSV is tried; if that fails the pack adds no columns.
- The **daily digest** (yesterday/today in **Asia/Dhaka**) is a research snapshot of freshness, signal flips, paper RIGHT/WRONG, calendar events ahead, and Awareness FAIL/STALE sources. It is **not** a live edge and does not call `BrokerPort`.
- The **champion/challenger retrain gate** walk-forward-compares a new fit against the saved champion. It **promotes only** if profit factor, total return, and max drawdown all improve (or a configured non-regression bar). Otherwise it keeps the champion and reports **null**. First run **seeds** the slot — that is not a claimed improvement. Not a live edge.

## Install (Windows)

Double-click **`INSTALL.bat`** (or run it from a command prompt). It prints an **OK / MISSING** checklist and **does not fail silently**.

```bat
cd C:\AI\forex-lab
INSTALL.bat
```

What it checks **before** creating `.venv`:

| Check | Required | If MISSING |
|-------|----------|------------|
| **Python 3.11+** | yes | Stops. Install from [python.org/downloads](https://www.python.org/downloads/). Tick **Add python.exe to PATH** and leave **pip** checked, then re-run `INSTALL.bat`. |
| **pip** | yes | Reinstall Python with pip, or `python -m ensurepip --upgrade`. |
| **venv module** | yes | Windows: use the python.org installer. Linux: `python3-venv`. |
| **Write access** | yes | Copy the project to a writable folder (not `Program Files`). |
| **Network (pip)** | optional | Stated as MISSING; `pip install` needs internet unless wheels are cached. |
| **requirements.txt** | yes | Run `INSTALL.bat` from this repo folder. |

If Python is not installed at all, `INSTALL.bat` still prints that checklist (Python / pip / venv = MISSING) plus the download link, then **exits without creating a half-broken venv**.

On success it creates `.venv`, installs `requirements.txt`, and verifies `import streamlit` and `import forex_lab`. It then offers to start the desk (`RUN_UI.bat`).

```bat
REM skip the Y/N prompt
set INSTALL_QUIET=1
INSTALL.bat

REM install then launch http://localhost:8501
set INSTALL_LAUNCH_UI=1
INSTALL.bat --launch-ui
```

Override the interpreter with `FORX_PYTHON` if several copies are installed:

```bat
set FORX_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
INSTALL.bat
```

Any OS with Python 3.11+ already on PATH:

```bat
python -m forex_lab.install_check --preflight
python -m forex_lab.install_check --install
```

## Quick start

```bat
cd C:\AI\forex-lab
.venv\Scripts\activate

python -m forex_lab fetch --pair EURUSD --period 2y --interval 1h
python -m forex_lab train --pair EURUSD
python -m forex_lab backtest --pair EURUSD
python -m forex_lab signals --pair EURUSD
python -m forex_lab digest
python -m forex_lab retrain --pair EURUSD --dry-run
```

If `yfinance` download fails (network / Yahoo blocks), fetch falls back to a **synthetic OHLCV** generator so the pipeline still demos end-to-end. Force it with:

```bat
python -m forex_lab fetch --pair EURUSD --synthetic
```

Re-run `train` then `backtest` after a fetch so models and `reports/latest_report.md` match the new cache.

### Windows console encoding

`fetch` writes `data\<PAIR>_<interval>.csv` **before** any console print. CLI stdout is ASCII (`->`, not `→`). `INSTALL.bat`, `RUN_DEMO.bat`, and `RUN_UI.bat` set `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`, and code page 65001.

If a print still fails on cp1252, fetch **exits 0 whenever the CSV was saved**. `RUN_DEMO.bat` will not overwrite an existing `data\EURUSD_1h.csv` with synthetic data on a later error. Do not mix synthetic numbers with yfinance numbers in the same report.

## Local dashboard (Streamlit)

A **trader-facing signal screen** on **localhost:8501**. Math analysis + news context flash **BUY / SELL / HOLD** with details so **you** decide. It calls the same `forex_lab` functions as the CLI. **No live broker APIs, no auto-trading.** Paper Buy/Sell/Close talks only to a `BrokerPort` (default `PaperBroker`).

**Theme:** the desk ships a **dark dense terminal** look — high-contrast **BUY green / SELL red / HOLD grey**, tighter spacing, denser type, reduced Streamlit chrome. Polish pass: consistent type scale and spacing, scan-row legend, empty states, aligned workspace/watchlist buttons, and **STALE** rows / disabled paper BUY/SELL that read in seconds. Config is `.streamlit/config.toml` plus custom CSS in `forex_lab/ui/theme.py`. This is presentation only: clocks stay **Asia/Dhaka**, paper `BrokerPort` is unchanged, and no features are removed.

The top of the page is the **signal screen** (primary): one **dense board** — **Pair | TF | Signal | Conf | Data● | MTF | Session | Last/mid | Spread | Next event | Spark | actions**. **BUY** is green, **SELL** red, **HOLD** grey. Click a pair to open a **detail drawer** (chart / SHAP / news / risk / rationale) so the scan line stays clean. Add / remove pairs; the list is saved to `config/watchlist.yaml` so it survives reruns. Rows without cached data or a trained model show `need Fetch/Train` instead of a fake signal.

**Workspace presets** (scalp / swing, plus save-as custom): one compact row — **select / Apply / Reset / Save current**. They switch watchlist pairs, lab interval/TF, realtime refresh seconds, and a **min-confidence display overlay**. Builtins live in `config/workspaces/`. User copies and the last-applied pointer live in `data/workspaces/` (gitignored). Applying a preset **does not** rewrite `config/default.yaml`, **does not** wipe `data/paper_broker.json`, and **does not** change `BrokerPort`. Desk clocks stay **Asia/Dhaka**.

**Realtime** (default **60s**, minimum 60s): rebuilds signals from the **local** cache each tick. yfinance is called only when a bar is due or the cache is approaching stale, **one pair per tick**, with exponential backoff after errors or 429-like responses. Yahoo’s download endpoint is unofficial and has no SLA — 60–120s is the practical band; do not set this like a broker stream. A **rate limited — backing off until …** banner appears if throttled. Realtime off: **Manual update** only.

**Data validity:** each row’s **Data●** is `OK` / `CLOSED` / `STALE` / `MISSING` / `ERROR`. In a liquid session, a last candle older than ~2× the timeframe is **STALE** and the flash is **—** plus “data stale — refresh required” (the last model class is kept as a note, not as a live call). Weekends / Friday after ~21:00 UTC show **CLOSED** with last bar time — not a false STALE alarm. Last bar, last fetch, and last signal times live in the detail drawer; the board shows **board last refreshed at …**. Clocks on the desk are **Asia/Dhaka** (`ui.timezone`, UTC+6) with an `Asia/Dhaka` tag; stored data stays UTC. Session windows remain UTC.

**Last / mid:** the table **Last/mid** column shows the cached yfinance **close** as **last/mid-ish**. Yahoo FX is not a bid/ask book and is **not** your broker’s executable quote. Mid from Bid/Ask is used only if those columns exist (they do not on the default yfinance path). Do not read this as live broker last.

**Spread estimate:** `spread_pips` from `config/default.yaml` (same pip assumption as backtest) as **cost context** — not a live broker spread. Optional last-bar High−Low is shown as a **bar range (not a bid/ask spread)** when `board.quote.bar_range_proxy` is true.

**Session:** a clock badge **ASIA / LONDON / NY** (overlap **LONDON+NY**) from UTC windows in `board.sessions` (defaults: Asia 21:00–07:00 wrapping so Sunday open is not OFF, London 07:00–16:00, NY 13:00–21:00). Weekend / Friday after ~21:00 UTC → **CLOSED**. Paper journal `session` uses the same `classify_session` helper (not a second hard-coded hour table), so Sunday 22:00 UTC / Monday 21:30 UTC record **asia**, not **off**. This is a desk scan, not a venue calendar. Feature-flag hours in the model (`sess_asia` 00–07, etc.) are unchanged.

**Sparkline / Risk:** the scan row shows a unicode spark of the last cached closes (empty when STALE/MISSING — no invented prices). Open the pair for the sparkline chart and a **Risk** panel with ATR SL/TP (same `barrier` config as labels/backtest), R:R, and config spread. Entry is last close as a **proxy** for next-open. Copy states research suggestion only — no lot size, no auto-submit, no live broker order. HOLD or STALE/MISSING → risk n/a. Paper submit uses those SL/TP as defaults when the box is available.

**Awareness:** a count bar (**N OK · N STALE · N FAIL**) plus expander **Awareness** lists **every source** the desk fetches or observes: watchlist **OHLCV** (price), **news RSS** (headlines), **model file** presence (signals), **calendar** (events), and optional **FRED** (macro; **OFF** when the pack is disabled). Columns: **Source | Observing | Cadence | Last OK | Status**. Cadence is the realtime interval vs **manual** / **daily**. **Last OK** is **Asia/Dhaka**. Status is **OK / STALE / FAIL** (plus **MISSING / CLOSED / OFF**) with a short error — **STALE and FAIL never look OK**. The expander opens itself when anything is STALE/FAIL/MISSING. CLOSED (weekend) is not a panic. Paper `BrokerPort` is unchanged.

**Daily digest:** expander **Daily digest** (also `python -m forex_lab digest` / `scripts/daily_digest.py`) summarizes **yesterday + today** in **Asia/Dhaka**: data freshness, BUY/SELL/HOLD flips, paper RIGHT/WRONG counts, calendar events ahead, and Awareness FAIL/STALE/MISSING sources. Missing caches **fail-soft**. Times on the digest are **Asia/Dhaka**. Not a live edge. `BrokerPort` four methods are unchanged.

**Alerts strip:** a compact, dismissible banner at the top of the board when a watchlist pair **flips** BUY↔SELL or to/from HOLD, or validity becomes **STALE / MISSING**. Last-seen signals are stored in `data/alert_state.json` (gitignored; survives reruns). Unchanged polls do not re-fire; the same transition is rate-limited (`board.alerts.cooldown_s`, default 300s). Optional **event within 60m** reuses the high-impact calendar (once per event). **Sound is off by default** (`board.alerts.sound` plus an **Alert sound** checkbox). Alert times use `ui.timezone` (**Asia/Dhaka**). The strip never places orders; `BrokerPort` is unchanged.

**Paper portfolio:** **BUY / SELL / CLOSE** on each board row record a dummy fill at the last cached close into `data/paper_broker.json` (gitignored). The UI talks only to `forex_lab.broker.BrokerPort`. Default implementation is `PaperBroker` (`broker.backend: paper` — the only supported value). Open positions mark-to-market from later bars and auto-close when the same ATR TP/SL (or horizon) would hit. `forex_lab.score` lookback-scores PENDING → **RIGHT / WRONG** once enough cached bars exist (TP first = RIGHT, SL first = WRONG, horizon = signed move at the last bar after spread; manual close stays **FLAT**). Unrealized is a **paper mark vs last/mid-ish cache**, not live broker PnL. The **Paper book stats** section shows **hit rate**, plus breakdowns by **session**, **confidence bucket**, and **validity_at_entry (STALE vs OK)**. Filter wrongs (and session / STALE / conf) to inspect entry context: signal, confidence, session, news bias, data validity. Short **how to improve** notes are driven by those aggregates (e.g. many STALE wrongs → don’t trade stale) and always say this is **not a live edge**. Times on the journal are **Asia/Dhaka**. **STALE or MISSING disables Paper BUY/SELL** (buttons greyed out) with the caption “Paper BUY/SELL is disabled when Data● is STALE or MISSING — refresh (Fetch) first.” The card flash is **—** (not a live call). CLOSE stays available on an open paper position. Optional **selective gates** (default **off**) can also HOLD the flash and disable BUY/SELL when MTF disagrees, confidence is below the floor, or a high-impact event window is live (same before/during/after minutes as the advice cards). Missing calendar or MTF does not crash the desk. This is a practice desk that pretends to be a real book — **not** linked to any broker. A future `mt5` / `oanda` class would implement the same four methods (`submit`, `close`, `list_positions`, `list_fills`); this repo does not store API keys or wire live orders. `BrokerPort` four methods are unchanged.

**News lane (v1):** Google News RSS search per pair (no API key). Shows a few recent headlines (title, time, link) plus a short bullish/bearish/mixed/unclear note from a keyword heuristic on those titles only — it never invents articles. Labeled **news context, not a trade instruction**. Cache: `data/news_cache.json` (gitignored), default TTL **300s**, HTTP timeout **6s**. Be polite to the feed; if fetch fails, the math board still renders with an empty news state.

**Event calendar:** upcoming **High**-impact FX releases (NFP, FOMC, CPI, rate decisions, unemployment, GDP, and similar) with a countdown, currency, and which watchlist pairs are affected. Free source: unofficial Forex Factory weekly JSON at `https://nfs.faireconomy.media/ff_calendar_thisweek.json` (no API key, no SLA — not an official Forex Factory / Fed / BLS API). Cache: `data/calendar_cache.json` (gitignored), default TTL **1800s**. If the CDN is down, the board **reuses a stale cache** or shows empty + a warning; the math cards still render.

**Advice (not orders):** the **Next event** column (⚠ when the pre-event window is live) stays on the scan row. Advisory cards in the detail drawer combine event proximity (before / during / after windows), the open paper position, and the model / MTF badge. Typical suggestions: **no new opens**, **hold**, **close**, **tighten SL**. Tighten SL reuses the ATR risk box at `advice.tighten_sl_atr` (default 1.0 vs `barrier.sl_atr` 2.0) and never widens a stop. **Nothing is submitted** until you click BUY/SELL/CLOSE or **Apply paper SL** (PaperBroker extra — not part of the four-method `BrokerPort`). NFP / FOMC / CPI-style names can suggest flatten (`advice.flatten_action`).

**Selective open gates (default off):** `gates.enabled` can require **MTF agree**, a **min confidence** floor (null = reuse `signals.min_confidence`), and **no new opens** inside the same event windows as advice. Applied to the flashed BUY/SELL class **and** Paper BUY/SELL. Walk-forward cannot replay Forex Factory history, so the event gate is a no-op there (fail-soft pass). EURUSD screen: `reports/gate_screen.md`. Default stays **off** unless PF, total return, and max DD improve. Not a live edge.

**Multi-timeframe confirmation:** each card shows **MTF agree / conflict / n/a** from a causal higher-TF SMA slope (default 4h resample of the **same** pair CSV — completed bars only). Optional `board.mtf_confirm.conflict_flash`: `off` (default, badge only), `weaken` (dimmer BUY/SELL), or `hold` (flash HOLD, keep last model class as a note). This is **not** the same as `signals.htf_trend_filter` (still default off; that gate hurt EURUSD in prior screens) or `gates.require_mtf_agree` (also default off until a WF non-regression).

**Explainability:** expand a card for local feature drivers (XGBoost `pred_contribs`, optional SHAP, logistic coef fallback), which config rules passed/failed, and a grounded rationale. This describes the fitted model on one bar — not evidence of an edge.

Fetch / Train / Backtest / Generate signals live in the collapsed sidebar **Lab** expander, along with the **champion/challenger retrain gate**. Walk-forward CSV/metrics/equity/logs are in a collapsed **Research lab** expander under the board. Open those when you need data or a model, not to read the screen.

Windows (activates `.venv` if present, installs `requirements.txt` if Streamlit is missing). Prefer `INSTALL.bat` first so the OK/MISSING checklist has already passed:

```bat
cd C:\AI\forex-lab
RUN_UI.bat
```

Or:

```bat
cd C:\AI\forex-lab
.venv\Scripts\activate
streamlit run streamlit_app.py
```

Then open http://localhost:8501 (default port). Stop with Ctrl+C in that terminal.

## Outputs

| Path | Meaning |
|------|---------|
| `data/EURUSD_1h.csv` | Cached OHLCV |
| `models/EURUSD_xgboost.joblib` | Primary model |
| `models/EURUSD_logistic.joblib` | Logistic baseline model |
| `signals/latest_signals.csv` | Latest BUY/SELL/HOLD rows (confidence-filtered) |
| `config/watchlist.yaml` | Streamlit watch-board pairs (local; survives reruns) |
| `config/workspaces/scalp.yaml` | Scalp board preset (15m, liquid majors, min conf 0.45) |
| `config/workspaces/swing.yaml` | Swing board preset (1h, majors + AUD, min conf 0.40) |
| `data/workspaces/` | User-saved presets + `active.yaml` last-applied pointer (local; gitignored) |
| `data/news_cache.json` | Google News RSS cache for the UI news lane (local; gitignored) |
| `data/calendar_cache.json` | Forex Factory weekly JSON cache for the event calendar (local; gitignored) |
| `data/alert_state.json` | Last-seen watchlist signals + undismissed alerts for the strip (local; gitignored) |
| `data/digest_latest.json` | Last daily digest payload (local; gitignored) |
| `data/champion/` | Champion metadata + joblib snapshots from the retrain gate (local; gitignored) |
| `models/<PAIR>_champion.json` | Pointer to the saved champion (local; gitignored) |
| `data/fred_cache/` | FRED daily CSV cache when the macro pack is enabled (local; gitignored) |
| `reports/latest_report.md` | Win-rate style metrics vs baselines + fold stability |
| `reports/experiments.md` | Screens that were tried (asymmetric R:R, calibration, sessions, pandas-ta/FRED, …); included in the report |
| `reports/gate_screen.md` | EURUSD walk-forward: baseline vs MTF/confidence gates vs cost-aware / asymmetric ATR labels |
| `reports/latest_metrics.json` | Same metrics as JSON |

## How to refresh data

```bat
python -m forex_lab fetch --pair EURUSD --period 2y --interval 1h
python -m forex_lab train --pair EURUSD
python -m forex_lab backtest --pair EURUSD
python -m forex_lab signals --pair EURUSD
```

`fetch` overwrites `data/<PAIR>_<interval>.csv`. Walk-forward metrics are only as current as that file. Use `--synthetic` only for an offline demo — do not mix synthetic numbers with yfinance numbers in the same report.

## Daily digest

Yesterday + today in **Asia/Dhaka** (`ui.timezone`). Fail-soft if a cache is missing. CLI uses **disk caches only** (no Google News / calendar HTTP). Not a live edge. Does not call `BrokerPort`.

```bat
python -m forex_lab digest
python -m forex_lab digest --when today
python -m forex_lab digest --json
python3 scripts/daily_digest.py --when yesterday
```

The Streamlit **Daily digest** expander (under Awareness) is the same payload: data freshness, BUY/SELL/HOLD flips from `data/alert_state.json`, paper RIGHT/WRONG from `data/paper_broker.json`, calendar events ahead, Awareness FAIL/STALE/MISSING. Optional write: `data/digest_latest.json` (gitignored). Config: `digest:` in `config/default.yaml`.

## Weekly champion / challenger retrain gate

Walk-forward compare a challenger (current `config/default.yaml` + cached CSV) against the saved champion. **Promote only if all three improve**, otherwise **keep champion and report null**. First run **seeds** the champion slot from `reports/latest_metrics.json` (or a new walk-forward) — that is **not** a promotion and **not** a claimed edge.

Promotion rules (`retrain.mode`):

| Mode | Promote when |
|------|----------------|
| `improve` (default) | Challenger **profit factor** > champion, **total return** > champion, and **max drawdown** is not worse (DD is negative: challenger DD ≥ champion DD − `dd_eps`). Equal metrics → **null**. |
| `non_regression` | No metric regresses beyond the acceptance bar (defaults PF **0.05** / total return **0.03** / max DD **0.01** when the eps knobs are left at 0) **and** at least one of the three strictly improves. Equal-within-eps → **null**. |

Empty challenger / `n_trades` below `retrain.min_trades` → **null**. Walk-forward failure **fail-softs** (champion unchanged). Production joblib is refreshed only on a real **promote** (or a seed that came from a fresh walk-forward), never on **null**. Paper `BrokerPort` is untouched.

```bat
python -m forex_lab retrain --pair EURUSD --dry-run
python -m forex_lab retrain --pair EURUSD
python3 scripts/weekly_retrain.py --pair EURUSD
```

`--dry-run` compares `reports/latest_metrics.json` to the saved champion without walk-forward, train, or writing champion JSON / joblib. Full `retrain` can take several minutes (same as `backtest`). Champion JSON lives in `data/champion/<PAIR>.json`; a pointer is written to `models/<PAIR>_champion.json`. Lab sidebar: **Retrain gate** / **Retrain dry-run**. Config: `retrain:` in `config/default.yaml`. **Not a live edge.**

## Label scheme (current default: `triple_barrier`)

Configured in `config/default.yaml`. **This is a research label, not a broker order.**

For each decision bar `t`, features use **only data at or before `t`**. The simulated fill is the **next bar’s open** (`entry_timing: next_open`) so the close that produced the signal is not the fill.

```
fill   = Open[t+1]
ATR    = Wilder ATR at t          (causal)
long  TP = fill + tp_atr * ATR    (default 2.0)
long  SL = fill - sl_atr * ATR    (default 2.0)
short TP / SL = the mirror
scan   = High/Low of bars t+1 .. t+horizon   (default horizon = 8)

BUY  if the long trade hits TP before SL
SELL if the short trade hits TP before SL
HOLD if neither side wins (timeout / conflict)
```

Barriers are **per-side** (long TP = +tp_atr ATR, long SL = -sl_atr ATR, and the mirror for shorts) so an asymmetric R:R does not bake in a long/short **label** bias. Default `tp_atr = sl_atr = 2.0` matches a single upper/lower pair. Backtest exits use the same fill, ATR width, first-touch rule, and (by default) **one open position at a time**.

Optional filters applied to **both** `backtest` and `signals` (so the CSV is the same policy as the report):

- `signals.min_confidence` — min P(predicted class) to emit BUY/SELL (default `0.40`; random 3-class is ~0.33). Harsh cutoffs can hurt: on EURUSD 1h, the highest XGBoost confidence bucket was **not** the best.
- `signals.min_dir_edge` — min |P(BUY) − P(SELL)| (default `0.0`; leave the model's HOLD class to do the sitting-out).
- `signals.sessions` — optional UTC session allow-list (`london`, `ny`, `asia`). Empty = all hours. London+NY-only **hurt** EURUSD vs the unfiltered model.
- `signals.htf_trend_filter` — optional higher-timeframe SMA-slope agreement (`4h` / `1D`). **Default off**: session/vol-style filters hurt EURUSD in prior screens.
- `board.mtf_confirm` — UI badge (`agree` / `conflict` / `n/a`) from causal H4 (or D1) SMA slope. `conflict_flash: off|weaken|hold` (default **off**).
- `board.sessions` — UTC windows for the watchlist **ASIA / LONDON / NY** clock badge (end exclusive; `asia: [21, 7]` wraps midnight). Independent of model `sess_*` columns.
- `board.quote` — last/mid-ish label, source note, and whether to show last-bar High−Low as a labeled range proxy.
- `ui.timezone` — display zone for desk clocks (default **Asia/Dhaka**). Session windows stay UTC.
- `feature_extras.pandas_ta.enabled` — extra TA columns (default **off**). Native backend; optional `pandas_ta` if the package is installed.
- `feature_extras.fred.enabled` — FRED as-of macro columns (default **off**). `FRED_API_KEY` is optional; CSV works without it. Fail-soft if offline.
- `calendar` / `advice` — event calendar source URL, impact filter, cache TTL, before/during/after minutes, flatten keywords, and whether open positions get hold / close / tighten-SL cards. Advice never auto-submits.
- `gates` — selective open filters (`enabled` default **false**): `require_mtf_agree`, `min_confidence` (null = reuse `signals.min_confidence`), `no_new_opens_in_event_window`. Fail-soft if calendar/MTF is missing. Applies to flash + paper opens. Not a live edge.
- `digest` — daily yesterday/today snapshot (`when`, persist file, calendar lookahead). CLI disk-only. Not a live edge.
- `retrain` — champion/challenger walk-forward gate (`mode: improve` \| `non_regression`, eps bars, `store: data/champion`). Promote only on a clear WF improve; else null. Not a live edge.
- `board.alerts` — watchlist flip / STALE strip (`sound` default **false**, `cooldown_s` 300, optional `event_warning` within 60m). Last-seen snapshot in `data/alert_state.json`. Not orders.
- Workspace presets — `config/workspaces/{scalp,swing}.yaml` (and `data/workspaces/` for custom). Overlay watchlist pairs, `interval`, `board.realtime_seconds`, and in-memory `signals.min_confidence`. Apply / save / reset on the desk. Not a rewrite of this file or the paper journal.
- `model.calibrate` — `isotonic` or `sigmoid` on the last 20% of each train window. Both **hurt** EURUSD (over-confident wrong ranks).
- `model.prune_bottom_frac` — drop lowest train-fold XGBoost gain. Unstable across fractions; not enabled.

A screen of those knobs (asymmetric 1.5:1 / 2:1, cost-aware labels, vol filter, pooled multi-pair train, GBPUSD/USDJPY transfer, pandas-ta / FRED packs) is in `reports/experiments.md`. Headline remains **profit factor < 1**. Do not treat a small PF tick around 1.0 as an edge — fold PF std is ~0.5.

Legacy close-to-close labels are still available:

```yaml
label_scheme: forward_return
horizon: 4
label_threshold: 0.0005
entry_timing: same_close
use_tp_sl: false
one_position: false
```

That legacy rule was: `BUY` if `Close[t+N]/Close[t]-1 > threshold`, `SELL` if below `−threshold`, else `HOLD`. It is noisier on 1h FX (tiny 5-pip dead zone, overlapping trades, same-bar fill).

## Features (causal)

Returns at 1/3/6/12/24 bars, SMA/EMA ratios, MACD-style EMA spread, RSI, ATR%, short/long vol regime, ATR-normalized returns, candle range z-score, location in 20/50-bar range, SMA slope, session flags (Asia/London/NY in UTC), and hour/dow Fourier terms. Optional extras (`feature_extras`): 4h resample of the **same** pair (backward-filled completed bars), London∩NY overlap flag, short-vol percentile, optional cross-pair returns (skipped if that CSV is missing).

**pandas-ta pack** (`feature_extras.pandas_ta`, default **off**): extra causal oscillators — stochastic, ADX ±DI, Bollinger %B/bandwidth, CCI, Williams %R, ROC, Keltner position. Default backend is a **native** subset in `forex_lab/ta_pack.py` so CI does not need numba. `pip install pandas-ta` is optional (`backend: pandas_ta`). No column uses future bars. EURUSD walk-forward: PF 0.890 → 0.900 (noise; still < 1). Left off.

**FRED pack** (`feature_extras.fred`, default **off**): daily macro series (Fed funds `DFF`, 10y `DGS10`, curve `T10Y2Y`, broad dollar `DTWEXBGS`, VIX `VIXCLS`) aligned to each 1h bar with `lag_days` (default 1): an observation dated calendar day `D` is first used at `D+lag` 00:00, then forward-filled. Same-day prints never enter features. Optional `FRED_API_KEY` uses `fredapi` when installed; otherwise a public FRED CSV is downloaded and cached in `data/fred_cache/` (gitignored, TTL 24h). If the key is missing **and** CSV fails, the pack adds **no columns** (train/backtest still run). Do not add the pair’s own FRED FX print (`DEXUSEU` on EURUSD) — it is skipped automatically. EURUSD walk-forward: null vs baseline, slightly worse max DD. Left off.

The committed EURUSD joblib only uses columns it was trained with until you retrain. Volume z-score is included only when volume actually varies (yfinance FX volume is often all zeros).

## How to read success rate

Open `reports/latest_report.md` after `backtest`:

- **Win rate** — share of closed trades with **net** return &gt; 0 after **spread + optional commission** (pips).
- **Win rate 95% CI** — normal-approx binomial interval on that win rate (not a live-trading guarantee).
- **Win rate BUY / SELL** — same, split by side.
- **# trades**, **avg return/trade**, **total return** (compounded, sequential closed trades), **max drawdown**, **profit factor**.
- **Fold stability** — mean ± std of win rate / avg return / profit factor across walk-forward folds that had at least one trade.
- Compare against **SMA crossover** and **always-long** on the **same** walk-forward windows, barriers, costs, and one-position rule.

A model with a slightly higher win rate but worse profit factor / deeper drawdown than the baseline is **not** a clear win. Small or null gaps vs baseline are the expected outcome on FX 1h data; do not treat a backtest beat as a trading system.

## Config

Edit `config/default.yaml` for pairs, interval, `label_scheme`, horizon, ATR barriers, spread/commission pips, one-position, walk-forward window sizes, signal filters, `gates`, `digest`, `retrain`, `feature_extras.pandas_ta`, `feature_extras.fred`, `calendar`, `advice`, `board.mtf_confirm`, `board.sessions`, `board.quote`, `board.alerts`, and `ui.timezone`. Workspace presets (`config/workspaces/`) overlay board view fields only — they do not rewrite this file.

## Connecting a live broker later

The Streamlit Buy/Sell/Close buttons never import a vendor SDK. They call `make_broker(cfg)`, which returns a `BrokerPort` (`OrderGateway` alias) with four methods: `submit`, `close`, `list_positions`, `list_fills`.

Today `broker.backend: paper` constructs `PaperBroker` (local JSON, cached last-close fills, paper SL/TP). To plug in a real venue later:

1. Subclass `BrokerPort` (e.g. `forex_lab/broker_mt5.py`) with those four methods.
2. Set `broker.backend: mt5` (or `oanda`) in `config/default.yaml`.
3. Teach `make_broker` to construct that class.
4. Keep credentials **out of this repo** (environment variables or a gitignored local secret). Do not commit API keys.

This project does **not** ship that class, those SDKs, or live wiring. Paper remains the only supported backend.

## Tests

```bat
python -m pytest tests -q
```

Tests check causal features (future bar edits must not change past rows), triple-barrier first-touch / timeout / conflict labels, confidence filters, the Streamlit UI smoke render against sample reports/signals, **dark dense terminal theme** (`.streamlit/config.toml` + `forex_lab/ui/theme.py` BUY/SELL/HOLD contrast), watchlist load/save plus board-row status, **workspace preset load/save/apply/reset** (`tests/test_workspace.py`: scalp/swing builtins, user shadow, JSON load, in-memory overlay does not write `default.yaml`, **applying a preset does not wipe the paper journal**), local explanations, Google News RSS parse + keyword bias (no network), OHLCV freshness (OK / STALE / CLOSED / MISSING), sparklines + ATR risk box, last/mid + config spread + clock session classification (Asia/London/NY, overlap, weekend closed, configurable windows), paper journal session labels matching the board Asia wrap (`test_paper_session_matches_board_asia_wrap_and_overlap`), Asia/Dhaka display-time formatting, **Awareness panel** rows (`tests/test_health.py`: Source / Observing / Cadence / Last OK / Status, watchlist OHLCV + news RSS + calendar + optional FRED + model file presence, STALE/FAIL never look OK, Last OK in Asia/Dhaka, paper BrokerPort untouched), **daily digest** builders (`tests/test_digest.py`: Asia/Dhaka yesterday/today windows, paper RIGHT/WRONG, signal flips, calendar ahead, Awareness FAIL/STALE, fail-soft empty caches, no `BrokerPort` import), **champion/challenger promotion** (`tests/test_retrain.py`: promote only when PF / total return / max DD improve, null on regression or equal metrics, non-regression bar, seed is not a promotion, dry-run never writes champion, fail-soft WF error keeps champion, no `BrokerPort` import), PaperBroker fills/SL-TP scoring, paper lookback scorer RIGHT/WRONG/PENDING (`tests/test_score.py`: TP/SL, horizon signed move, STALE/session/conf aggregates, mistake filters), the event calendar parse/cache/fail-soft path plus next-event labels, advisory cards (no auto-submit), MTF agree/conflict/hold-flash, **selective open gates** (`tests/test_gates.py`: MTF/confidence/event, fail-soft when calendar/MTF missing, default-off), the pandas-ta subset (causal / default-off), FRED as-of lag plus fail-soft when the cache is missing (no network), watchlist alert flips / STALE / MISSING / rate-limit / event-within-60m (`tests/test_alerts.py`), the dense board columns (Pair | TF | Signal | Conf | Data● | MTF | Session | Last/mid | Spread | Next event | Spark | Actions), unicode sparklines, **Paper BUY/SELL disabled on STALE/MISSING** (`paper_submit_allowed`, AppTest on disabled buttons), and the **Windows installer preflight** (`tests/test_install_check.py`: missing Python prints `[MISSING]` plus the python.org link, old 3.10 is rejected, pip/venv/write-access failures, optional network does not fail required checks, mocked `.venv` + import verify, `INSTALL.bat` still documents the no-Python path).

## Project layout

```
streamlit_app.py # trader signal screen (streamlit run streamlit_app.py)
INSTALL.bat      # Windows installer: OK/MISSING checklist, .venv, requirements, optional RUN_UI.bat
RUN_UI.bat       # Windows helper: venv + streamlit on localhost:8501
forex_lab/
  console.py     # ASCII-safe CLI prints + UTF-8 stdio
  data.py        # yfinance fetch + synthetic fallback
  features.py    # causal features + labels
  ta_pack.py     # optional pandas-ta subset (native backend; no lookahead)
  fred.py        # optional FRED as-of macro pack (CSV or FRED_API_KEY)
  explain.py     # local drivers / rule overlay / grounded rationale
  freshness.py   # OK/STALE/CLOSED vs last bar (UI; not a broker clock)
  news.py        # Google News RSS + keyword bias (UI context only)
  calendar.py    # unofficial FF weekly JSON + cache (UI; fail-soft)
  advise.py      # no-new-open / hold / close / tighten-SL cards (not orders)
  gates.py       # selective open gates (MTF / confidence / event; default off)
  mtf.py         # causal HTF SMA-slope badge + optional conflict flash
  session.py     # Asia/London/NY clock badge (configurable UTC windows)
  clock.py       # store UTC, display Asia/Dhaka (ui.timezone)
  broker.py      # BrokerPort + PaperBroker (practice fills; no live venue)
  score.py       # paper lookback scorer (RIGHT/WRONG/PENDING; not a live edge)
  digest.py      # daily digest builders (yesterday/today, Asia/Dhaka; not a live edge)
  retrain.py     # champion/challenger walk-forward gate (promote or null; not a live edge)
  model.py       # XGBoost + logistic
  backtest.py    # walk-forward + metrics + report
  signals.py     # latest_signals.csv
  cli.py         # CLI entry (fetch/train/backtest/signals/digest/retrain)
  install_check.py  # stdlib preflight / installer (python -m forex_lab.install_check)
  ui/            # Streamlit helpers (watch board, awareness panel, alerts, workspace presets, dark terminal theme; no live trading)
config/default.yaml
config/watchlist.yaml  # persisted research watchlist for the Streamlit board
config/workspaces/     # scalp / swing board presets (pairs, TF, refresh, min conf)
tests/
scripts/screen_variants.py  # optional research screen (not a user command)
scripts/screen_feature_packs.py  # pandas-ta / FRED walk-forward screen
scripts/screen_gates.py          # MTF/confidence gates + cost-aware / asymmetric ATR WF
scripts/daily_digest.py          # same as python -m forex_lab digest
scripts/weekly_retrain.py        # same as python -m forex_lab retrain
```
