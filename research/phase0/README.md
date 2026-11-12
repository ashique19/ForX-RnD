# Phase 0 diagnostics

Research-only checks for the hourly classifier. They do not change `config/default.yaml`, `min_confidence`, costs, sessions, risk, or anything under `data/`, `models/`, or `signals/`.

The questions and the frozen window are in `PREREG.md`. Bars at or after **2026-11-01 00:00 UTC** are dropped before any feature, label, fit, or summary. Do not point this run at that window.

History is read from `data\history\EURUSD_1h.csv` and `data\history\USDJPY_1h.csv` (gitignored; they live on the machine that has the prices). Saved walk-forward predictions are used when `data\edge_check\htf_20261020\<PAIR>_preds.csv` exists. If it does not, the script generates them with `forex_lab.backtest.walk_forward_backtest`. It never writes a synthetic price file.

Judged round-trip cost, applied in memory only: EURUSD 1.4 pips, USDJPY 2.4 pips. The walk-forward lengths, the 0.60 confidence floor, and the 2 ATR / 8-bar barriers stay the yaml values.

## Run on Windows

From the repository root (`C:\AI\forex-lab` in the install notes), with the virtualenv `INSTALL.bat` created:

```bat
cd C:\AI\forex-lab
.venv\Scripts\python.exe -m research.phase0.run --pairs EURUSD USDJPY
```

That is the command. Do not activate the venv first and do not call the store `python`. `.venv\Scripts\python.exe` is the interpreter that has the project dependencies.

The first walk-forward fit (and a second, purged fit) on the full hourly history is the slow part. Leave it running. It prints a line per pair and the two output paths when it finishes.

Outputs, next to this file:

- `research\phase0\RESULTS_<YYYYMMDD>.md`
- `research\phase0\RESULTS_<YYYYMMDD>.json`

`<YYYYMMDD>` is today's UTC date. A second run on the same UTC date replaces those two files. They are the only files the command writes.

If a history CSV is missing, that pair is recorded as an error and the process exits 1. The script does not invent prices.

Optional flags (the real run leaves them unset):

| Flag | Default | Role |
|---|---|---|
| `--history-dir` | `data/history` | Read-only `{PAIR}_1h.csv` directory |
| `--preds-dir` | (also checks `data/edge_check/htf_20261020`) | Read-only `{PAIR}_preds.csv` |
| `--out-dir` | `research/phase0` | Must not be under `data\`, `models\`, or `signals\` |
| `--model-type` | yaml `model.type` (`xgboost`) | In-memory only |
| `--seeds` | 200 | Random-side seeds |
| `--n-boot` | 1000 | Time-block bootstrap draws |
| `--block` | 20 | Contiguous trades per bootstrap block |
| `--embargo-bars` | label horizon (8) | Extra gap after the label purge |

## What the report contains

(a) Index trace of each walk-forward fold: whether a training label's scan, bars `t+1` through `t+horizon`, intersects that fold's test bars, and whether the holdout mask removed those rows. If it does, profit factor and expectancy for the production fit versus a purged and embargoed fit that reuses `fit_predict_bundle`.

(b) Zero-cost and after-cost expectancy of the out-of-sample trades against random sides at the same decision times, against a sign flip of the same path, and against a resimulated flipped side. Model and flipped series carry a time-block bootstrap interval.

(c) Confidence deciles versus realized net R, and P&L by stop, target, and time exit.

(d) Count of bars where the stop and the target are both touched, and the share of trades.

(e) Round-trip cost versus the median absolute 8-bar move, by pair and session. Diagnostic only.
