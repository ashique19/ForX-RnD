# Forex Research Lab

Research-only **BUY / SELL / HOLD** signal pipeline with walk-forward success-rate metrics.

**You trigger trades manually.** This project has **no broker APIs** and places **no live orders**.

## Disclaimer

- Not financial advice. For education / research only.
- `yfinance` FX data (`EURUSD=X`, etc.) is **not** the same as your broker’s executable quotes (spreads, liquidity, session gaps differ).
- Past backtest metrics **do not** predict future results.
- Always paper-trade and validate independently before risking capital.
- Beating SMA / always-long in this lab is a **research signal**, not a deployable edge (slippage, weekend gaps, and broker quotes are not modeled).

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

## Outputs

| Path | Meaning |
|------|---------|
| `data/EURUSD_1h.csv` | Cached OHLCV |
| `models/EURUSD_xgboost.joblib` | Primary model |
| `models/EURUSD_logistic.joblib` | Logistic baseline model |
| `signals/latest_signals.csv` | Latest BUY/SELL/HOLD rows (confidence-filtered) |
| `reports/latest_report.md` | Win-rate style metrics vs baselines + fold stability |
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
upper  = fill + tp_atr * ATR      (default tp_atr = 2.0)
lower  = fill - sl_atr * ATR      (default sl_atr = 2.0)
scan   = High/Low of bars t+1 .. t+horizon   (default horizon = 8)

BUY  if upper is touched first
SELL if lower is touched first
HOLD if the vertical barrier is hit first (timeout)
HOLD if both barriers are touched in the same bar (ambiguous path)
```

Barriers are **symmetric** in ATR units so the label does not bake in a long/short payoff bias. Backtest exits use the same fill, ATR width, first-touch rule, and (by default) **one open position at a time**.

Optional filters applied to **both** `backtest` and `signals` (so the CSV is the same policy as the report):

- `signals.min_confidence` — min P(predicted class) to emit BUY/SELL
- `signals.min_dir_edge` — min |P(BUY) − P(SELL)|

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

Returns at 1/3/6/12/24 bars, SMA/EMA ratios, MACD-style EMA spread, RSI, ATR%, short/long vol regime, ATR-normalized returns, candle range z-score, location in 20/50-bar range, SMA slope, session flags (Asia/London/NY in UTC), and hour/dow Fourier terms. No column is built from future bars. Volume z-score is included only when volume actually varies (yfinance FX volume is often all zeros).

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

Edit `config/default.yaml` for pairs, interval, `label_scheme`, horizon, ATR barriers, spread/commission pips, one-position, walk-forward window sizes, and signal filters.

## Tests

```bat
python -m pytest tests -q
```

Tests check causal features (future bar edits must not change past rows), triple-barrier first-touch / timeout / conflict labels, and confidence filters.

## Project layout

```
forex_lab/
  data.py        # yfinance fetch + synthetic fallback
  features.py    # causal features + labels
  model.py       # XGBoost + logistic
  backtest.py    # walk-forward + metrics + report
  signals.py     # latest_signals.csv
  cli.py         # CLI entry
config/default.yaml
tests/
```
