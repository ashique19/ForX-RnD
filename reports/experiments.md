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

### HTF / extras / news (this PR)

Optional *features* are on so a **retrained** model can use them: `feature_extras.higher_tf: [4h]`, `sess_ldn_ny`, `vol_pct`. The committed EURUSD joblib still predicts with its original columns (`signals.py` dropna is on model `feature_cols` only). `signals.htf_trend_filter` and London+NY *filters* stay **off** — prior screens were worse. Cross-pair is `null` (skip if CSV missing). Google News RSS is UI context only; it does not enter labels or the model.

### pandas-ta / FRED feature packs (this PR)

Same walk-forward protocol as the table above, on the **current** `data/EURUSD_1h.csv` (41 folds). These numbers are a 4-way screen against each other — not a reprint of `reports/latest_report.md` (that run also compared logistic and may be an older cache).

FRED loaded via public CSV (**no** `FRED_API_KEY`): `DFF`, `DGS10`, `T10Y2Y`, `DTWEXBGS`, `VIXCLS`, as-of `lag_days=1`. pandas-ta pack used the native causal subset (not numba).

| Variant | Trades | Win rate | Total return | Max DD | Profit factor | vs baseline |
|---|---:|---:|---:|---:|---:|---|
| baseline (current extras, packs off) | 1067 | 50.52% | -9.78% | -10.36% | 0.8894 | — |
| + pandas-ta | 1079 | 49.77% | -8.94% | -10.27% | 0.8997 | +0.01 PF, still &lt; 1; fold noise |
| + FRED | 1059 | 49.67% | -9.67% | -10.58% | 0.8895 | null; slightly worse DD |
| + both | 1045 | 50.62% | -8.78% | -10.28% | 0.9000 | same as TA; FRED adds no lift |

**Default stays off for both packs.** A 0.01 profit-factor tick is well inside fold PF std (~0.5–0.6). Every variant still has PF < 1 and negative total return. This is **not** a trading edge. Enable in `config/default.yaml` only for research retrains.

Reproduce: `python3 scripts/screen_feature_packs.py` (writes `reports/feature_pack_screen.md`).
