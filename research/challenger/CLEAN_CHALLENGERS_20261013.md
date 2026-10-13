# Clean-bar challengers — 2026-10-13 (USDJPY, EURUSD, EURJPY)

Approved by Ashiqul 2026-10-13 23:29 Dhaka. **Challenger only:** no promote, no champion write, models/ untouched, yaml frozen,
existing feature set. Script: `research/challenger/clean_challenger_20261013.py`. Outputs: `data/challengers/20261013/` (not committed).

**Method.** `forex_lab.model.train_models` on clean HistData bars before the holdout cutoff (2025-10-01 UTC), with the holdout
`train_mask` enforced and `paths.models_dir` redirected in memory to the challenger folder. It fits on the first 80% (to ~2023-08) and
validates on the last 20% (~2023-08 -> 2025-09-30 = "pre-holdout OOS"). Both challenger and frozen champion are scored with the production
predict + `apply_signal_filters` path and `forex_lab.backtest._simulate_trades`. Round trip = 2 x measured one-way p50 / p90.
DSR: N = 70 trials, V[SR] from the 2026-10-12 rescore. The "holdout" and "since 2025-10" windows are the same window (2025-10-01 -> 2026-10-12).
Family baseline: `walk_forward_backtest` on pre-cutoff clean bars (in-memory refits, p50).

| Pair | Book | Pre-holdout OOS: n / PF p50 / PF p90 / DD p50 / DSR p50 | Holdout: n / PF p50 / PF p90 / DD p50 / DSR p50 |
|---|---|---|---|
| USDJPY | champion | 523 / 0.87 / 0.80 / -10.0% / 0.00 | 307 / 1.85 / 1.67 / -2.3% / 0.99 |
| USDJPY | challenger | 3 / 0.33 / 0.30 / -0.7% / n/a | 0 / — / — / — / n/a |
| EURUSD | champion | 135 / 1.70 / 1.60 / -2.7% / 0.90 | 85 / 1.70 / 1.57 / -0.7% / 0.86 |
| EURUSD | challenger | 3 / 0.69 / 0.64 / -0.2% / n/a | 1 / — / — / — / n/a |
| EURJPY | champion | 470 / 0.97 / 0.86 / -4.4% / 0.00 | 266 / 1.61 / 1.39 / -5.0% / 0.89 |
| EURJPY | challenger | 3 / 1.13 / 1.07 / -0.8% / n/a | 0 / — / — / — / n/a |

Walk-forward baseline on pre-holdout clean bars (p50): USDJPY 1871 trades PF 0.95 DD -18.0%; EURUSD 2778 trades PF 1.00 DD -12.9%;
EURJPY 2046 trades PF 0.84 DD -40.5%. DSR is 0.00 for all three.

**Why the challengers barely trade.** With clean bars the label mix is balanced (~25% sell / 49% hold / 25% buy). The clean fits are much
less confident: median max-probability is 0.42-0.48 against 0.49-0.57 for the champions. Only 0.02-0.04% of bars are directional with confidence >= the frozen
`min_confidence` 0.60, against 3-14% for the champions. So the frozen confidence gate mutes them. Changing `min_confidence` is a yaml change
and is out of scope here.

**Verdict: all three challengers FAIL the gate.** With 0-3 trades, PF cannot "clearly beat" the champion, and none clears 30 trades. Do not shadow them.
The champions also fail on the pre-holdout OOS slice for USDJPY (0.87) and EURJPY (0.97). Their holdout PF > 1 is a single-window result,
and the clean walk-forward family baseline is PF <= 1.00 for all three. That points to no robust edge in the current feature/label family on clean bars.
EURUSD's champion is the only one at PF > 1.5 on both OOS windows (135 + 85 trades), and its DSR of 0.86-0.90 is still below 0.95.
Next options, which need Ashiqul: (a) keep the champions on paper shadow as-is; (b) a written hypothesis to re-pick `min_confidence`
for clean-bar models, as one pre-registered value judged on the holdout (a yaml change, needs explicit approval).
