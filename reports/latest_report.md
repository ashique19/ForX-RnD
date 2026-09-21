# Forex Lab Report — EURUSD

> Research only. No live orders. yfinance ≠ broker quotes. Past ≠ future.

- Model: `xgboost`
- Walk-forward folds: **41**
- Test bars: **10228**
- Label accuracy (test): **39.88%**
- Horizon: 4 bars | threshold: 0.0005 | spread: 1.0 pips

## Model success metrics

| Metric | Value |
|---|---|
| # trades | 6888 |
| Win rate (overall) | 49.75% |
| Win rate BUY | 49.90% (n=3355) |
| Win rate SELL | 49.62% (n=3533) |
| Avg return / trade | -0.000045 |
| Total return (compound) | -27.49% |
| Max drawdown | -35.93% |
| Profit factor | 0.937207 |

## Baselines

| Strategy | Trades | Win rate | Total return | Max DD | Profit factor |
|---|---:|---:|---:|---:|---:|
| Model | 6888 | 49.75% | -27.49% | -35.93% | 0.937207 |
| SMA crossover | 10223 | 46.11% | -58.34% | -68.86% | 0.871157 |
| Always long | 10224 | 48.01% | -40.18% | -54.18% | 0.923116 |

## How to read success rate

- **Win rate** = fraction of closed trades with net_return > 0 after spread cost.
- Compare model win rate / total return / profit factor against SMA and always-long baselines.
- A higher win rate alone is not enough if avg return or profit factor is worse than baseline.

## Disclaimer

This lab is for research education only. It does **not** place broker orders.
Data from yfinance is not identical to broker executable quotes. Past backtest results do not predict future performance.
