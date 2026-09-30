"""Offline meta-label strike-2 candidate sweep vs pin champion book."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from forex_lab.config_loader import load_config
from forex_lab.history import load_history
from forex_lab.features import build_features

def main() -> None:
    cfg = load_config()
    pc = json.loads(
        Path("data/replay/2cee7e7163134266ad8c2b39234442d8/paper_champion.json").read_text(
            encoding="utf-8"
        )
    )
    book = pd.DataFrame(
        [
            {
                "entry_bar_time": t.get("entry_bar_time") or t.get("entry_time"),
                "side": t["side"],
                "confidence": float(t.get("confidence") or np.nan),
                "session": (t.get("session") or "").lower(),
                "pnl": float(t["realized"]) if t.get("realized") is not None else np.nan,
                "outcome": t.get("outcome"),
            }
            for t in pc["closed"]
        ]
    )
    book["entry_ts"] = pd.to_datetime(book["entry_bar_time"], utc=True, errors="coerce")

    frame = load_history("EURUSD", cfg, "1h", start="2014-01-01", end=None)
    try:
        feat = build_features(frame, cfg, pair="EURUSD")
    except TypeError:
        feat = build_features(frame, cfg)
    if "atr_pctile" not in feat.columns:
        vp = int((cfg.get("feature_extras") or {}).get("vol_percentile_window") or 100)
        atr = feat["atr_pct"]
        amin = atr.rolling(vp, min_periods=max(5, vp // 5)).min()
        amax = atr.rolling(vp, min_periods=max(5, vp // 5)).max()
        feat = feat.copy()
        feat["atr_pctile"] = (atr - amin) / (amax - amin).replace(0, np.nan)

    feat_idx = feat.copy()
    feat_idx.index = pd.to_datetime(feat_idx.index, utc=True)
    cols = ["atr_pctile", "atr_pct"] + [c for c in feat_idx.columns if c.startswith("sess_")]
    book2 = book.dropna(subset=["entry_ts"]).set_index("entry_ts").sort_index()
    if book2.index.tz is None:
        book2.index = book2.index.tz_localize("UTC")
    joined = book2.join(feat_idx[cols], how="left")
    joined["conf_atr"] = joined["confidence"] * joined["atr_pctile"]
    print("join atr null", float(joined["atr_pctile"].isna().mean()), "n", len(joined))
    print("sessions", joined["session"].value_counts().to_dict())

    def score(keep: pd.Series) -> dict:
        df = joined.loc[keep]
        pnl = df["pnl"].astype(float)
        wins = float(pnl[pnl > 0].sum())
        losses = float(-pnl[pnl < 0].sum())
        pf = wins / losses if losses > 0 else float("nan")
        cum = pnl.cumsum()
        peak = cum.cummax()
        dd = float((cum - peak).min()) if len(cum) else float("nan")
        return {
            "n": int(len(df)),
            "wr": float((pnl > 0).mean()),
            "pf": float(pf),
            "sum": float(pnl.sum()),
            "dd_sum": dd,
        }

    base = score(joined["pnl"].notna())
    cand: dict[str, dict] = {}

    def add(name: str, veto: pd.Series) -> None:
        v = veto.fillna(False)
        cand[name] = {**score(~v), "veto_n": int(v.sum())}

    for atr_thr in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
        add(f"asia_quiet_{atr_thr}", (joined["session"] == "asia") & (joined["atr_pctile"] <= atr_thr))
    for atr_thr in [0.15, 0.20, 0.25, 0.30]:
        add(
            f"asia_ny_quiet_{atr_thr}",
            joined["session"].isin(["asia", "ny"]) & (joined["atr_pctile"] <= atr_thr),
        )
    for atr_thr in [0.15, 0.20, 0.25]:
        add(
            f"london_quiet_{atr_thr}",
            (joined["session"] == "london") & (joined["atr_pctile"] <= atr_thr),
        )
    for thr in [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20]:
        add(f"conf_atr_lt_{thr}", joined["conf_atr"] < thr)
    for atr_m, conf_m in [
        (0.20, 0.70),
        (0.25, 0.70),
        (0.30, 0.70),
        (0.20, 0.75),
        (0.25, 0.75),
        (0.35, 0.70),
    ]:
        add(
            f"qw_a{atr_m}_c{conf_m}",
            (joined["atr_pctile"] <= atr_m) & (joined["confidence"] < conf_m),
        )
    add("skip_asia_all", joined["session"] == "asia")
    for thr in [0.10, 0.12, 0.15, 0.18]:
        add(
            f"asia_confatr_lt_{thr}",
            (joined["session"] == "asia") & (joined["conf_atr"] < thr),
        )
    # conf*atr interaction with floor: weak product only when conf < 0.70
    for thr in [0.10, 0.12, 0.15]:
        add(
            f"weak_confatr_lt_{thr}",
            (joined["confidence"] < 0.70) & (joined["conf_atr"] < thr),
        )
    # asia session + conf < 0.70 (session-weak, no ATR)
    add("asia_conf_lt_070", (joined["session"] == "asia") & (joined["confidence"] < 0.70))
    add("asia_conf_lt_065", (joined["session"] == "asia") & (joined["confidence"] < 0.65))

    rows = []
    for k, v in cand.items():
        rows.append(
            {
                "rule": k,
                **v,
                "d_pf": v["pf"] - base["pf"],
                "d_sum": v["sum"] - base["sum"],
                "d_dd": v["dd_sum"] - base["dd_sum"],
            }
        )
    tab = pd.DataFrame(rows)
    # prefer rules that improve PF and DD without large sum loss; veto_n >= 40
    tab = tab.sort_values(["d_pf", "d_dd", "d_sum"], ascending=[False, False, False])
    print("BASE", base)
    print(tab.to_string(index=False))
    # shortlist: veto>=40, d_sum>=-0.05 (don't kill sum), d_pf>0 or d_dd>0
    short = tab[(tab["veto_n"] >= 40) & (tab["d_sum"] >= -0.15)].head(15)
    print("\nSHORTLIST veto>=40 d_sum>=-0.15 top15 by pf/dd/sum")
    print(short.to_string(index=False))
    Path("_META_LABEL_STRIKE2_SWEEP.csv").write_text(tab.to_csv(index=False), encoding="utf-8")
    print("wrote _META_LABEL_STRIKE2_SWEEP.csv")

if __name__ == "__main__":
    main()
