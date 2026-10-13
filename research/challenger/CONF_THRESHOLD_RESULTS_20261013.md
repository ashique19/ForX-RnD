# Confidence-threshold selection results — 2026-10-13

This follows pre-registration `PREREG_CONF_THRESHOLD_20261013.md` (commit 5c7058d, written before scoring) and was run by `research/challenger/conf_select_20261013.py`.
The threshold was overridden in memory only; yaml is unchanged. There was no training. N = 100 trials for the deflated Sharpe (DSR). The holdout was scored once per selected model.

| Pair | Model | Selected (slice) | Slice n / PF p90 / DD p90 | Holdout n | Holdout PF p50 / p90 | Holdout DD p50 / p90 | Holdout DSR p50 / p90 | Proposal bar |
|---|---|---|---|---|---|---|---|---|
| USDJPY | challenger | 0.45 | 286 / 1.18 / -2.7% | 156 | 0.89 / 0.80 | -2.9% / -3.5% | 0.01 / 0.00 | FAIL (PF < 1) |
| USDJPY | champion | none eligible (DD p90 -13.6% to -23.8%, PF p90 <= 0.87) | — | — | — | — | — | no candidate |
| EURUSD | challenger | 0.50 | 310 / 0.95 / -5.1% | 168 | 0.81 / 0.75 | -3.7% / -4.5% | 0.00 / 0.00 | FAIL (PF < 1) |
| EURUSD | champion | 0.55 | 307 / 1.72 / -4.8% | 174 | 1.48 / 1.38 | -1.9% / -2.4% | 0.75 / 0.59 | FAIL (DSR < 0.95) |
| EURJPY | challenger | 0.50 | 78 / 0.89 / -4.0% | 82 | 1.04 / 0.87 | -1.5% / -2.2% | 0.15 / 0.04 | FAIL |
| EURJPY | champion | 0.60 (only eligible; slice PF p90 0.86) | 470 / 0.86 / -9.4% | 266 | 1.61 / 1.39 | -5.0% / -6.6% | 0.86 / 0.54 | FAIL (DSR < 0.95; slice PF < 1) |

**Readings.** The slice-to-holdout transfer is poor for the challengers: USDJPY's best slice PF of 1.18 became 0.80 on the holdout. On clean bars,
the lower thresholds trade more but lose after costs. The challengers' high slice PFs at 0.50-0.55 rest on 6-42 trades and were excluded by the 50-trade rule.
EURUSD's champion is the most consistent model (slice PF p90 1.72, holdout 1.38, shallow DD), but its DSR of 0.59-0.75 after 100 trials
is not significant.

**Verdict: no pair has a threshold worth proposing for paper shadow under the pre-registered bar.** Nothing is promoted and yaml is unchanged.
The closest is EURUSD champion at 0.55, which would need about 2-3x the holdout trades at the same PF to reach DSR 0.95. Paper shadow at the current 0.60 is the
only honest way to collect that evidence. The challenger family (current features on clean bars) shows no edge at any threshold.
