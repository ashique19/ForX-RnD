# EURUSD walk-forward — selective gates vs label barriers

Research only. Same protocol as the feature-pack screen on the **current** `data/EURUSD_1h.csv` (logistic off for speed). **Not a trading system.** Event-window gate is UI-only here (walk-forward has no historical Forex Factory dump; missing calendar fail-softs).

This baseline matches `reports/feature_pack_screen.md` (1067 trades, PF 0.8894). It is **not** a reprint of `reports/latest_report.md` (that headline run also compared logistic and printed PF 0.978 on a 1066-trade book).

| Variant | Trades | Win rate | Total return | Max DD | Profit factor | vs baseline |
|---|---:|---:|---:|---:|---:|---|
| baseline | 1067 | 50.52% | -9.78% | -10.36% | 0.8894 | — |
| gated_mtf_conf | 615 | 50.73% | -8.00% | -8.46% | 0.8465 | worse PF — keep off |
| gated_mtf_conf50 | 394 | 49.24% | -7.08% | -7.57% | 0.7926 | worse PF — keep off |
| cost_aware | 1067 | 50.52% | -9.78% | -10.36% | 0.8894 | null (2 ATR ≫ 1 pip) — keep off |
| asymmetric_1p5_1 | 1622 | 42.66% | -6.74% | -10.23% | 0.9339 | mixed — WR dropped; PF tick inside fold noise — keep 2/2 |

**Takeaway (not a live edge):**
- **Gates** cut trades (1067 → 615 / 394). Total return and max DD look less bad because the book is thinner, but **profit factor fell**. Default `gates.enabled: false`.
- **Cost-aware** labels (`HOLD` if TP < spread) are a **null** at 2 ATR vs 1 pip. Keep `barrier.cost_aware: false`.
- **Asymmetric 1.5:1** improved PF/return/DD on *this* cache, but win rate collapsed to 42.7% and a +0.04 PF move is well inside fold PF std (~0.5). A prior screen vs the stronger 2/2 headline (PF 0.978) was **worse**. Keep `tp_atr=sl_atr=2.0`.

Default: `gates.enabled: false`, `barrier.cost_aware: false`, `tp_atr=sl_atr=2.0`. Turn a variant on only if PF, total return, **and** max DD improve (or a clear non-regression). A 0.01 PF tick is fold noise.
