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
- Filters: min_confidence=0.4 min_dir_edge=0.0 sessions=all min_vol_regime=0.0 min_tp_pips=0.0 one_position=True calibrate=None prune=0.0 cost_aware=False
- Label mix (full labeled set): BUY 27.52% / SELL 26.81% / HOLD 45.67% (n=12173)

## Label scheme

For each decision bar `t` (features at/before `t` only):

```
fill      = Open[t+1]          # next-bar open; not used as a feature
ATR       = Wilder ATR at t    # causal
long  TP  = fill + tp_atr * ATR ;  long  SL = fill - sl_atr * ATR
short TP  = fill - tp_atr * ATR ;  short SL = fill + sl_atr * ATR
scan      = High/Low of bars t+1 .. t+horizon
BUY  if the long trade hits TP before SL
SELL if the short trade hits TP before SL
HOLD if neither side wins (timeout, conflict, or both fail)
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


## Experiments tried (this PR)

Same walk-forward protocol unless noted: train 2000 / test 250 / step 250, next-open fill, one-position, 1 pip spread, ATR triple-barrier, XGBoost, `min_confidence=0.40`, `min_dir_edge=0.0`. Logistic was off for screening speed; the headline EURUSD table above includes logistic.

**None of these is a trading system.** Fold profit-factor std is ~0.5–0.6, so small PF moves around 1.0 are noise.

### EURUSD screens (pooled test trades)

| Variant | Trades | Win rate | Total return | Profit factor | vs baseline |
|---|---:|---:|---:|---:|---|
| Baseline 2/2 ATR (this report) | 1066 | 52.2% | -2.06% | 0.978 | — |
| Asymmetric 1.5:1 ATR (matched labels+BT) | 1603 | 43.0% | -6.52% | 0.936 | worse |
| Asymmetric 2:1 ATR (matched labels+BT) | 1359 | 37.7% | -10.82% | 0.879 | worse |
| Cost-aware HOLD if TP < spread | 1066 | 52.2% | -2.06% | 0.978 | no change (2 ATR >> 1 pip) |
| Trade only London+NY | 762 | 49.0% | -7.76% | 0.888 | worse |
| High-vol only (`vol_regime>=1`) | 456 | 48.0% | -7.12% | 0.835 | worse |
| Min TP 8 pips | 1066 | 52.2% | -2.06% | 0.978 | no change |
| Isotonic calibration | 1096 | 49.3% | -16.86% | 0.812 | worse |
| Platt / sigmoid calibration | 1096 | 49.3% | -16.86% | 0.812 | worse |
| Prune bottom 25% train-fold gain | 1075 | 52.3% | -1.68% | 0.983 | tiny; other prune fractions worse |
| No confidence floor | 1117 | 51.5% | -6.12% | 0.935 | worse |
| Drop session flags (keep hour Fourier) | 1078 | 52.6% | -0.47% | 0.997 | tiny; not stable with other knobs |
| `min_child_weight` 12 / 16 / 20 / 24 / 30 | ~1070 | mixed | +2.6% to -5.5% | 1.03 to 0.94 | hops around fold noise |
| Pooled EUR+GBP+JPY train, EURUSD test | 1007 | 50.7% | -8.34% | 0.899 | worse |

`|P(BUY)-P(SELL)|` stays at 0. A previous PR already showed that edge filter selected worse trades.

In-sample XGBoost gain is dominated by `sess_ny` / `hour_sin` / `sess_asia` — time-of-day overfitting, not a price-action edge. Dropping those features did **not** produce a stable OOS improvement.

### Other pairs (same protocol; not universality)

| Pair | Model PF | Model ret | SMA PF | Always-long PF |
|---|---:|---:|---:|---:|
| EURUSD | 0.978 | -2.06% | 0.929 | 0.943 |
| GBPUSD | 0.945 | -4.92% | 0.899 | 0.934 |
| USDJPY | 0.943 | -6.30% | 0.915 | 0.873 |

Single-split transfer (EURUSD train 80% → other pair after that timestamp, **not** walk-forward): EURUSD→GBPUSD PF 1.15 on 234 trades; EURUSD→USDJPY PF 0.87 on 229 trades. Mixed, small, and the weaker of the two is the honest one.

Reproduce screens: `python3 scripts/screen_variants.py` (not a user CLI command).

## Disclaimer

This lab is for research education only. It does **not** place broker orders.
Data from yfinance is not identical to broker executable quotes. Past backtest results do not predict future performance.
Even if the model beats these baselines, that is a research signal — not evidence of a deployable edge after slippage, gaps, and session holes.
