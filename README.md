# Forex Research Lab

Research-only **BUY / SELL / HOLD** signal pipeline with walk-forward success-rate metrics.

**You trigger trades manually.** This project has **no broker APIs** and places **no live orders**.

## Disclaimer

- Not financial advice. For education / research only.
- `yfinance` FX data (`EURUSD=X`, etc.) is **not** the same as your broker’s executable quotes (spreads, liquidity, session gaps differ).
- Past backtest metrics **do not** predict future results.
- Always paper-trade and validate independently before risking capital.

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

## Outputs

| Path | Meaning |
|------|---------|
| `data/EURUSD_1h.csv` | Cached OHLCV |
| `models/EURUSD_xgboost.joblib` | Primary model |
| `models/EURUSD_logistic.joblib` | Logistic baseline model |
| `signals/latest_signals.csv` | Latest BUY/SELL/HOLD rows |
| `reports/latest_report.md` | Win-rate style metrics vs baselines |
| `reports/latest_metrics.json` | Same metrics as JSON |

## Label scheme

For each bar `t`, forward return over `horizon` bars (`config/default.yaml`):

```
forward_return[t] = Close[t+N] / Close[t] - 1
BUY  if forward_return >  +label_threshold
SELL if forward_return <  -label_threshold
HOLD otherwise
```

Features (returns, SMA/EMA ratios, RSI, ATR%, volatility, hour/dow) use **only past/current** data — no leakage from the forward label.

## How to read success rate

Open `reports/latest_report.md` after `backtest`:

- **Win rate** — share of closed trades with **net** return &gt; 0 after configurable **spread cost (pips)**.
- **Win rate BUY / SELL** — same, split by side.
- **# trades**, **avg return/trade**, **total return** (compounded), **max drawdown**, **profit factor**.
- Compare against **SMA crossover** and **always-long** baselines on the same walk-forward windows.

A model with a slightly higher win rate but worse profit factor / deeper drawdown than the baseline is **not** a clear win.

## Config

Edit `config/default.yaml` for pairs, interval, horizon, label threshold, TP/SL flags, spread pips, and walk-forward window sizes.

## Project layout

```
forex_lab/
  data.py        # yfinance fetch + synthetic fallback
  features.py    # features + labels
  model.py       # XGBoost + logistic
  backtest.py    # walk-forward + metrics + report
  signals.py     # latest_signals.csv
  cli.py         # CLI entry
config/default.yaml
```
