# Forex Research Lab

Research-only **BUY / SELL / HOLD** signal pipeline with walk-forward success-rate metrics.

**You trigger trades manually.** This project has **no live broker APIs** and places **no live orders**. Paper Buy/Sell on the screen records a **local practice fill** only (`broker.backend: paper`).

## Disclaimer

- Not financial advice. For education / research only.
- `yfinance` FX data (`EURUSD=X`, etc.) is **not** the same as your broker’s executable quotes (spreads, liquidity, session gaps differ). The watchlist **Last** is last/mid-ish from that cache; **Spread** is the config pip estimate; **Session** is a UTC clock badge — none of these are live broker truth.
- Paper uPnL is a local mark vs last/mid-ish — **not** live broker PnL. `BrokerPort` is unchanged.
- Past backtest metrics **do not** predict future results.
- Always paper-trade and validate independently before risking capital.
- Beating SMA / always-long in this lab is a **research signal**, not a deployable edge (slippage, weekend gaps, and broker quotes are not modeled).
- News headlines (Google News RSS) can be **late, incomplete, or wrong**. The bias note is a keyword heuristic on fetched titles only — not a trade instruction.
- The event calendar uses an **unofficial** weekly Forex Factory JSON dump (`nfs.faireconomy.media`). Times can be revised; confirm on Fed / BLS / ECB / BoE sources. Fail-soft if offline.
- Advisory cards (no new opens / hold / close / tighten SL) are **decision support**. They never auto-submit via `BrokerPort`. MTF badges are causal SMA slope on the same CSV — not a live trend service.
- Extra TA (`feature_extras.pandas_ta`) and FRED macro (`feature_extras.fred`) packs are **off by default**. A EURUSD walk-forward screen showed a ~0.01 PF tick for TA (fold noise, still PF < 1) and a null FRED pack with a slightly worse drawdown — not an edge. FRED uses an as-of lag (not ALFRED vintages). A missing `FRED_API_KEY` is fine — public CSV is tried; if that fails the pack adds no columns.

## Install (Windows)

```bat
cd C:\AI\forex-lab
"%LOCALAPPDATA%\Programs\Python\Python311\python.exe" -m venv .venv
.venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements.txt
```

Or with any Python 3.10+:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Quick start

```bat
cd C:\AI\forex-lab
.venv\Scripts\activate

python -m forex_lab fetch --pair EURUSD --period 2y --interval 1h
python -m forex_lab train --pair EURUSD
python -m forex_lab backtest --pair EURUSD
python -m forex_lab signals --pair EURUSD
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

The top of the page is the **signal screen** (primary): one flash card per watchlist pair — **Pair | Timeframe | Last (mid-ish) | Spread | Session | Validity | Buy/Sell (color badge) | Target | Sparkline | Risk | Signal details | News context**. Add / remove pairs; the list is saved to `config/watchlist.yaml` so it survives reruns. Rows without cached data or a trained model show `need Fetch/Train` instead of a fake signal.

**Realtime** (default **60s**, minimum 60s): rebuilds signals from the **local** cache each tick. yfinance is called only when a bar is due or the cache is approaching stale, **one pair per tick**, with exponential backoff after errors or 429-like responses. Yahoo’s download endpoint is unofficial and has no SLA — 60–120s is the practical band; do not set this like a broker stream. A **rate limited — backing off until …** banner appears if throttled. Realtime off: **Manual update** only.

**Data validity:** each card shows `OK` / `CLOSED` / `STALE` / `MISSING` / `ERROR`. In a liquid session, a last candle older than ~2× the timeframe is **STALE** and the flash is **—** plus “data stale — refresh required” (the last model class is kept as a note, not as a live call). Weekends / Friday after ~21:00 UTC show **CLOSED** with last bar time — not a false STALE alarm. Last bar, last fetch, and last signal times are on the card; the board shows **board last refreshed at …**. Clocks on the desk are **Asia/Dhaka** (`ui.timezone`, UTC+6) with an `Asia/Dhaka` tag; stored data stays UTC. Session windows remain UTC.

**Last / mid:** each card and the table **Last** column show the cached yfinance **close** as **last/mid-ish**. Yahoo FX is not a bid/ask book and is **not** your broker’s executable quote. Mid from Bid/Ask is used only if those columns exist (they do not on the default yfinance path). Do not read this as live broker last.

**Spread estimate:** `spread_pips` from `config/default.yaml` (same pip assumption as backtest) as **cost context** — not a live broker spread. Optional last-bar High−Low is shown as a **bar range (not a bid/ask spread)** when `board.quote.bar_range_proxy` is true.

**Session:** a clock badge **ASIA / LONDON / NY** (overlap **LONDON+NY**) from UTC windows in `board.sessions` (defaults: Asia 21:00–07:00 wrapping so Sunday open is not OFF, London 07:00–16:00, NY 13:00–21:00). Weekend / Friday after ~21:00 UTC → **CLOSED**. This is a desk scan, not a venue calendar. Feature-flag hours in the model (`sess_asia` 00–07, etc.) are unchanged.

**Sparkline / Risk:** each card plots the last ~48 cached closes (empty when STALE/MISSING — no invented prices) and a **Risk** panel with ATR SL/TP (same `barrier` config as labels/backtest), R:R, and config spread. Entry is last close as a **proxy** for next-open. Copy states research suggestion only — no lot size, no auto-submit, no live broker order. HOLD or STALE/MISSING → risk n/a.

**Awareness (v0):** a one-line **Feeds:** strip plus expander **Awareness / data health** lists each watchlist OHLCV feed and the news lane: what is observed, refresh cadence (realtime interval vs manual), last successful update, and OK/STALE/FAIL. The expander opens itself when anything is STALE/FAIL/MISSING. Not a full registry — no daily digest or weekly retrain.

**Paper portfolio:** **Paper BUY / SELL / CLOSE** on each card record a dummy fill at the last cached close into `data/paper_broker.json` (gitignored). The UI talks only to `forex_lab.broker.BrokerPort`. Default implementation is `PaperBroker` (`broker.backend: paper` — the only supported value). Open positions mark-to-market from later bars and auto-close when the same ATR TP/SL (or horizon timeout) would hit; outcomes are PENDING / RIGHT / WRONG / TIMEOUT / FLAT. Unrealized is a **paper mark vs last/mid-ish cache**, not live broker PnL. The journal expander filters wrong trades and shows error rate by pair, session, STALE-vs-OK, and confidence bucket, plus short “how to improve” notes. This is a practice desk that pretends to be a real book — **not** linked to any broker. A future `mt5` / `oanda` class would implement the same four methods (`submit`, `close`, `list_positions`, `list_fills`); this repo does not store API keys or wire live orders. `BrokerPort` / `PaperBroker` methods are unchanged.

**News lane (v1):** Google News RSS search per pair (no API key). Shows a few recent headlines (title, time, link) plus a short bullish/bearish/mixed/unclear note from a keyword heuristic on those titles only — it never invents articles. Labeled **news context, not a trade instruction**. Cache: `data/news_cache.json` (gitignored), default TTL **300s**, HTTP timeout **6s**. Be polite to the feed; if fetch fails, the math board still renders with an empty news state.

**Event calendar:** upcoming **High**-impact FX releases (NFP, FOMC, CPI, rate decisions, unemployment, GDP, and similar) with a countdown, currency, and which watchlist pairs are affected. Free source: unofficial Forex Factory weekly JSON at `https://nfs.faireconomy.media/ff_calendar_thisweek.json` (no API key, no SLA — not an official Forex Factory / Fed / BLS API). Cache: `data/calendar_cache.json` (gitignored), default TTL **1800s**. If the CDN is down, the board **reuses a stale cache** or shows empty + a warning; the math cards still render.

**Advice (not orders):** cards next to Paper Buy/Sell combine event proximity (before / during / after windows), the open paper position, and the model / MTF badge. Typical suggestions: **no new opens**, **hold**, **close**, **tighten SL**. Tighten SL reuses the ATR risk box at `advice.tighten_sl_atr` (default 1.0 vs `barrier.sl_atr` 2.0) and never widens a stop. **Nothing is submitted** until you click Paper BUY/SELL/CLOSE or **Apply paper SL** (PaperBroker extra — not part of the four-method `BrokerPort`). NFP / FOMC / CPI-style names can suggest flatten (`advice.flatten_action`).

**Multi-timeframe confirmation:** each card shows **MTF agree / conflict / n/a** from a causal higher-TF SMA slope (default 4h resample of the **same** pair CSV — completed bars only). Optional `board.mtf_confirm.conflict_flash`: `off` (default, badge only), `weaken` (dimmer BUY/SELL), or `hold` (flash HOLD, keep last model class as a note). This is **not** the same as `signals.htf_trend_filter` (still default off; that gate hurt EURUSD in prior screens).

**Explainability:** expand a card for local feature drivers (XGBoost `pred_contribs`, optional SHAP, logistic coef fallback), which config rules passed/failed, and a grounded rationale. This describes the fitted model on one bar — not evidence of an edge.

Fetch / Train / Backtest / Generate signals live in the collapsed sidebar **Lab** expander. Walk-forward CSV/metrics/equity/logs are in a collapsed **Research lab** expander under the board. Open those when you need data or a model, not to read the screen.

Windows (activates `.venv` if present, installs `requirements.txt` if Streamlit is missing):

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
| `data/news_cache.json` | Google News RSS cache for the UI news lane (local; gitignored) |
| `data/calendar_cache.json` | Forex Factory weekly JSON cache for the event calendar (local; gitignored) |
| `data/fred_cache/` | FRED daily CSV cache when the macro pack is enabled (local; gitignored) |
| `reports/latest_report.md` | Win-rate style metrics vs baselines + fold stability |
| `reports/experiments.md` | Screens that were tried (asymmetric R:R, calibration, sessions, pandas-ta/FRED, …); included in the report |
| `reports/feature_pack_screen.md` | Walk-forward baseline vs +TA vs +FRED vs both |
| `reports/latest_metrics.json` | Same metrics as JSON |

## How to refresh data

```bat
python -m forex_lab fetch --pair EURUSD --period 2y --interval 1h
python -m forex_lab train --pair EURUSD
python -m forex_lab backtest --pair EURUSD
python -m forex_lab signals --pair EURUSD
```

`fetch` overwrites `data/<PAIR>_<interval>.csv`. Walk-forward metrics are only as current as that file. Use `--synthetic` only for an offline demo — do not mix synthetic numbers with yfinance numbers in the same report.

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

Edit `config/default.yaml` for pairs, interval, `label_scheme`, horizon, ATR barriers, spread/commission pips, one-position, walk-forward window sizes, signal filters, `feature_extras.pandas_ta`, `feature_extras.fred`, `calendar`, `advice`, `board.mtf_confirm`, `board.sessions`, `board.quote`, and `ui.timezone`.

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

Tests check causal features (future bar edits must not change past rows), triple-barrier first-touch / timeout / conflict labels, confidence filters, the Streamlit UI smoke render against sample reports/signals, watchlist load/save plus board-row status, local explanations, Google News RSS parse + keyword bias (no network), OHLCV freshness (OK / STALE / CLOSED / MISSING), sparklines + ATR risk box, last/mid + config spread + clock session classification (Asia/London/NY, overlap, weekend closed, configurable windows), Asia/Dhaka display-time formatting, data-health rows, PaperBroker fills/SL-TP scoring, the event calendar parse/cache/fail-soft path, advisory cards (no auto-submit), MTF agree/conflict/hold-flash, the pandas-ta subset (causal / default-off), and FRED as-of lag plus fail-soft when the cache is missing (no network).

## Project layout

```
streamlit_app.py # trader signal screen (streamlit run streamlit_app.py)
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
  mtf.py         # causal HTF SMA-slope badge + optional conflict flash
  session.py     # Asia/London/NY clock badge (configurable UTC windows)
  clock.py       # store UTC, display Asia/Dhaka (ui.timezone)
  broker.py      # BrokerPort + PaperBroker (practice fills; no live venue)
  model.py       # XGBoost + logistic
  backtest.py    # walk-forward + metrics + report
  signals.py     # latest_signals.csv
  cli.py         # CLI entry
  ui/            # Streamlit helpers (watch board, health strip; no live trading)
config/default.yaml
config/watchlist.yaml  # persisted research watchlist for the Streamlit board
tests/
scripts/screen_variants.py  # optional research screen (not a user command)
scripts/screen_feature_packs.py  # pandas-ta / FRED walk-forward screen
```
