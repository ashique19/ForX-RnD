import json
from pathlib import Path
import numpy as np
import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.features import make_dataset, LABEL_MAP, INV_LABEL_MAP
from forex_lab.history import load_history
from forex_lab.model import fit_predict_bundle, apply_signal_filters
from forex_lab.backtest import _attach_policy_columns

cfg = load_config()
df = load_history("EURUSD", cfg, "1h", start="2015-01-01", end=None)
X, y, ohlcv = make_dataset(df, cfg, pair="EURUSD")
print("labeled", len(X), y.value_counts().to_dict())

cut = pd.Timestamp("2024-01-01")
tr = X.index < cut
te = X.index >= cut
print("train", int(tr.sum()), "test", int(te.sum()))


def reliability_report(name, pred_frame, y_te):
    side = pred_frame["pred"].astype(int)
    conf = pred_frame["confidence"].astype(float)
    traded = side.isin([LABEL_MAP["BUY"], LABEL_MAP["SELL"]])
    s = side[traded]
    c = conf[traded]
    yt = y_te.loc[s.index].astype(int)
    correct = s.values == yt.values
    bins = [0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.80, 1.01]
    labels = ["0.40-0.50", "0.50-0.55", "0.55-0.60", "0.60-0.65", "0.65-0.70", "0.70-0.80", "0.80+"]
    cat = pd.cut(c, bins=bins, labels=labels, right=False)
    rows = []
    for lab in labels:
        m = (cat == lab).to_numpy()
        n = int(m.sum())
        if n == 0:
            rows.append({"bucket": lab, "n": 0, "mean_conf": None, "hit_rate": None, "gap": None})
            continue
        hit = float(correct[m].mean())
        mean_c = float(c.to_numpy()[m].mean())
        rows.append({"bucket": lab, "n": n, "mean_conf": mean_c, "hit_rate": hit, "gap": mean_c - hit})
    ece = 0.0
    total = max(len(c), 1)
    for r in rows:
        if r["n"] and r["mean_conf"] is not None:
            ece += (r["n"] / total) * abs(r["mean_conf"] - r["hit_rate"])
    # also overall directional + at pin floor >=0.60
    pin = c >= 0.60
    pin_hit = float(correct[pin.to_numpy()].mean()) if pin.any() else None
    pin_n = int(pin.sum())
    print(
        f"=== {name} traded={len(c)} hit={correct.mean():.4f} ECE~={ece:.4f} "
        f"pin>=0.60 n={pin_n} hit={pin_hit} ==="
    )
    for r in rows:
        mc = None if r["mean_conf"] is None else round(r["mean_conf"], 3)
        hr = None if r["hit_rate"] is None else round(r["hit_rate"], 3)
        gp = None if r["gap"] is None else round(r["gap"], 3)
        print(f"  {r['bucket']}: n={r['n']} mean_conf={mc} hit_rate={hr} gap={gp}")
    return {
        "name": name,
        "n": int(len(c)),
        "hit": float(correct.mean()) if len(c) else None,
        "ece": float(ece),
        "pin60_n": pin_n,
        "pin60_hit": pin_hit,
        "buckets": rows,
    }


def run_one(tag, cal, min_conf=0.40):
    c = json.loads(json.dumps(cfg))
    c.setdefault("model", {})["calibrate"] = cal
    c.setdefault("signals", {})["min_confidence"] = float(min_conf)
    # disable session/vol etc for pure calibration audit of model probs
    # keep only min_confidence filter
    _model, pred, _cols = fit_predict_bundle(
        c, X.loc[tr], y.loc[tr], X.loc[te], model_type="xgboost", balanced=True
    )
    pred = _attach_policy_columns(pred, X.loc[te], ohlcv.loc[te], c, "EURUSD")
    pred["pred"] = apply_signal_filters(pred, c)
    return reliability_report(tag, pred, y.loc[te])


results = [
    run_one("calibrate=null", None),
    run_one("calibrate=isotonic", "isotonic"),
    run_one("calibrate=sigmoid", "sigmoid"),
]

# Pick best by ECE then pin60_hit
valid = [r for r in results if r["n"] and r["ece"] is not None]
best = sorted(valid, key=lambda r: (r["ece"], -(r["pin60_hit"] or 0)))[0] if valid else None
print("BEST", best["name"] if best else None, "ECE", best["ece"] if best else None)

out = {
    "pair": "EURUSD",
    "hypothesis": "Raw XGB max-proba is miscalibrated vs directional label hit; isotonic/sigmoid on train-tail should lower ECE and lift pin>=0.60 hit.",
    "cut": "2024-01-01",
    "train_n": int(tr.sum()),
    "test_n": int(te.sum()),
    "label_map": INV_LABEL_MAP,
    "results": results,
    "best": best["name"] if best else None,
    "fix_candidate": (
        f"Set model.calibrate={best['name'].split('=')[1]} for one EURUSD full-history Replay "
        f"(pin settings); revert if challenger loses improve gate."
        if best and best["name"] != "calibrate=null"
        else "No calibrate method beat null on ECE; prefer regime filter next or raise min_confidence only if pin60 gap shows overconfidence."
    ),
}
Path("_CALIBRATION_AUDIT_20261001.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
print("wrote audit json")
