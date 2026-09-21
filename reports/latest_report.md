# Forex Lab Report — EURUSD

> Research only. No live orders. yfinance ≠ broker quotes. Past ≠ future.
> This is **not** a profitable trading system claim — compare vs baselines on the same windows.

- Model: `xgboost`
- Walk-forward folds: **41**
- Test bars: **10173**
- Label scheme: `triple_barrier`
- Label accuracy (filtered signals vs labels): **47.44%**
- Label accuracy (raw argmax, unfiltered): **46.30%**
- Horizon: 8 bars | TP/SL ATR: 2.0/2.0 | entry: next_open | spread: 1.0 pips + commission 0.0 pips
- Filters: min_confidence=0.4 min_dir_edge=0.0 one_position=True
- Label mix (full labeled set): BUY 27.52% / SELL 26.81% / HOLD 45.67% (n=12173)

## Label scheme

For each decision bar `t` (features at/before `t` only):

```
fill      = Open[t+1]          # next-bar open; not used as a feature
ATR       = Wilder ATR at t    # causal
upper     = fill + tp_atr * ATR
lower     = fill - sl_atr * ATR
scan      = High/Low of bars t+1 .. t+horizon
BUY  if upper is touched first
SELL if lower is touched first
HOLD if timeout, or both barriers in the same bar
```

Backtest uses the same fill, barriers, and (optional) one-position rule.
Low-confidence BUY/SELL predictions are forced to HOLD before trading.

## Model success metrics

| Metric | Value |
|---|---|
| # trades | 1066 |
| Win rate (overall) | 52.16% |
| Win rate 95% CI | 49.16% – 55.16% |
| Win rate BUY | 52.02% (n=521) |
| Win rate SELL | 52.29% (n=545) |
| Avg return / trade | -0.000018 |
| Total return (compound) | -2.06% |
| Max drawdown | -7.72% |
| Profit factor | 0.978070 |

## Baselines

| Strategy | Trades | Win rate | Total return | Max DD | Profit factor |
|---|---:|---:|---:|---:|---:|
| Model (`xgboost`) | 1066 | 52.16% | -2.06% | -7.72% | 0.978070 |
| Logistic (same WF) | 1042 | 49.71% | -7.02% | -8.60% | 0.920596 |
| SMA crossover | 1478 | 49.32% | -8.56% | -15.34% | 0.929465 |
| Always long | 1478 | 49.80% | -7.00% | -12.86% | 0.942846 |

## Research takeaway

After spread costs the primary model still has **profit factor < 1** (negative expectancy). The win-rate 95% CI includes 50%, so the directional hit rate is not distinguishable from a coin flip at this sample size. It **beats SMA and always-long** on total return and profit factor under this protocol — a small research gap, not a trading system. Do not trade this. yfinance ≠ broker quotes; walk-forward on one pair is not validation.

## Walk-forward fold stability

Per-fold trade metrics (model, same costs). Std is sample std across folds with ≥1 trade.

| Stat | Value |
|---|---|
| Folds | 41 (41 with trades) |
| Trades / fold (mean) | 26.219512 |
| Win rate mean ± std | 52.29% ± 11.59% |
| Avg return/trade mean ± std | -0.000005 ± 0.000428 |
| Profit factor mean ± std | 1.138899 ± 0.630742 |

## How to read success rate

- **Win rate** = fraction of closed trades with net_return > 0 after spread + commission.
- **95% CI** is a normal-approx binomial interval on that win rate; it is not a live-trading guarantee.
- Compare model win rate / total return / profit factor / drawdown against SMA and always-long **on the same walk-forward windows and cost model**.
- A higher win rate alone is not enough if avg return or profit factor is worse than baseline.
- Fold std tells you whether a headline number is stable or driven by a few windows.
- Logistic is a linear comparison on the **same** folds/features/filters, not a second trading system.

## Disclaimer

This lab is for research education only. It does **not** place broker orders.
Data from yfinance is not identical to broker executable quotes. Past backtest results do not predict future performance.
Even if the model beats these baselines, that is a research signal — not evidence of a deployable edge after slippage, gaps, and session holes.
