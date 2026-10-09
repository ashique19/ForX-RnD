"""Study #1 - is the edge real?  Uses EXISTING replay trade logs only.

No training, no Replay, no writes outside data/edge_check/ (gitignored).
Run:  .venv\\Scripts\\python.exe research\\edge_check\\edge_reality.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import edge_lib as L  # noqa: E402

OUT = L.ROOT / "data" / "edge_check"
HOLDOUT = json.loads((L.ROOT / "research" / "holdout.json").read_text(encoding="utf-8"))
CUTOFF = pd.Timestamp(HOLDOUT["cutoff_utc"])
CLEAN_EURUSD = L.HISTORY / "EURUSD_1h.csv.bak_pre_jetta_20260930_010124"
N_WF_SCREENS = 14  # rows in reports/experiments.md EURUSD screen table


def pins() -> dict[str, str]:
    sb = json.loads((L.ROOT / "data" / "pin_scoreboard.json").read_text(encoding="utf-8-sig"))
    return {r["pair"]: r["job_id"] for r in sb["rows"]}


def summarize(r: pd.Series, ts: pd.Series, n_trials: int, var_sr: float) -> dict:
    r = pd.Series(r.values, index=pd.DatetimeIndex(ts.values)).dropna()
    years = max((r.index.max() - r.index.min()).days / 365.25, 1e-9) if len(r) else np.nan
    d = L.deflated_sharpe(r, n_trials, var_sr)
    tpy = len(r) / years if len(r) else np.nan
    pre, post = r[r.index < CUTOFF], r[r.index >= CUTOFF]
    return {
        "n": len(r),
        "pf": L.profit_factor(r),
        "win_rate": float((r > 0).mean()) if len(r) else np.nan,
        "exp_bp": float(r.mean() * 1e4) if len(r) else np.nan,
        "sr_trade": d["sr"],
        "sr_ann": d["sr"] * np.sqrt(tpy) if np.isfinite(d["sr"]) else np.nan,
        "trades_per_year": tpy,
        "max_dd": L.max_drawdown_additive(r),
        "psr0": d["psr0"],
        "sr0": d["sr0"],
        "dsr": d["dsr"],
        "min_trl_trades": L.min_track_record(d["sr"], d["skew"], d["kurt"]),
        "pf_pre_holdout": L.profit_factor(pre),
        "pf_holdout": L.profit_factor(post),
        "dd_holdout": L.max_drawdown_additive(post),
        "n_holdout": len(post),
    }


def monthly(r: pd.Series, ts: pd.Series) -> pd.DataFrame:
    s = pd.Series(r.values, index=pd.DatetimeIndex(ts.values)).dropna()
    g = s.groupby(s.index.to_period("M"))
    return pd.DataFrame({"n": g.size(), "pf": g.apply(L.profit_factor), "sum_ret": g.sum(), "dd": g.apply(L.max_drawdown_additive)})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    costs = L.measured_costs()
    runs = L.index_runs()
    report: dict = {"cutoff_utc": str(CUTOFF)}

    # --- 1. data integrity per pair (computed) ---
    integ = {}
    for pair in ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "EURJPY", "EURGBP", "USDCHF", "GBPJPY"]:
        h = L.load_history(pair)
        prof = L.corruption_profile(h, pair)
        integ[pair] = {
            "wickless_share_all": float(prof.wickless_share.mean()),
            "median_open_gap_pips_all": float(prof.median_open_gap_pips.median()),
            "by_year": prof.round(3).to_dict(orient="index"),
        }
        live = L.ROOT / "data" / f"{pair}_1h.csv"
        if live.exists():
            lv = pd.read_csv(live, index_col=0, parse_dates=True)
            j = h[["Close"]].join(lv[["Close"]], rsuffix="_yf", how="inner")
            integ[pair]["close_vs_yfinance_median_abs_pips"] = float(((j.Close - j.Close_yf).abs() / L.pip_size(pair)).median()) if len(j) else None
            integ[pair]["close_vs_yfinance_overlap_bars"] = int(len(j))
    clean = L.load_history("EURUSD", CLEAN_EURUSD)
    integ["EURUSD_clean_backup"] = L.corruption_profile(clean, "EURUSD").round(3).to_dict(orient="index")
    report["integrity"] = integ

    # --- 2. fill bias of logged entries vs clean Dukascopy bid/ask open (EURUSD) ---
    fb = {}
    for job in ["2cee7e71", "0f541b01", "76c3d050", "a0d93fc6"]:
        t = L.load_trades(L.find_job(job)).join(clean[["AskOpen", "BidOpen"]], on="entry_ts")
        sgn = np.where(t.side == "BUY", 1, -1)
        true_fill = np.where(t.side == "BUY", t.AskOpen, t.BidOpen)
        t["edge_pips"] = sgn * (true_fill - t.entry_price) / 1e-4
        for seg, g in t.dropna(subset=["edge_pips"]).groupby(t.entry_ts.dt.year >= 2022):
            fb[f"{job}_{'2022plus' if seg else 'pre2022'}"] = {
                "n": int(len(g)), "median_fill_edge_pips": float(g.edge_pips.median()), "logged_pf": L.profit_factor(g.net_pnl)}
    report["fill_bias_eurusd"] = fb

    # --- 3. calibrate close-ATR factor on clean EURUSD 2015-2021 and validate close-path re-sim ---
    cc = clean[clean.index < "2022-01-01"]
    factor = float((L.true_range_atr(cc) / L.close_atr(cc.Close)).median())
    report["close_atr_factor"] = factor
    val = {}
    for job in ["2cee7e71", "a0d93fc6"]:
        t = L.load_trades(L.find_job(job))
        t = t[t.entry_ts < "2022-01-01"]
        # replay costs on clean data: real bid/ask spread + 0.2 pip slip each side
        spread = (clean.AskOpen - clean.BidOpen).reindex(t.entry_ts).median()
        rs = L.resim_close_path(t, clean.Close, L.close_atr(clean.Close, factor=factor), cost_rt_px=float(spread) + 0.4e-4)
        val[job] = {"n": int(len(t)), "logged_pf": L.profit_factor(t.net_pnl), "resim_pf": L.profit_factor(rs.ret),
                    "logged_wr": float((t.net_pnl > 0).mean()), "resim_wr": float((rs.ret.dropna() > 0).mean()),
                    "side_agree_exit": float((rs.exit_reason.values == t.exit_reason.replace({"horizon": "timeout"}).values).mean())}
    report["resim_validation_clean_pre2022"] = val

    # --- 4. trial count + variance of per-trade SR across EURUSD variants ---
    eu = runs[(runs.pair == "EURUSD") & runs.has_trades]
    eu_close = L.load_history("EURUSD").Close
    eu_atr = L.close_atr(eu_close, factor=factor)
    c_eu = costs.loc["EURUSD"]
    sr_logged, sr_resim, monthly_logged, monthly_resim = {}, {}, {}, {}
    for _, row in eu.iterrows():
        t = L.load_trades(L.ROOT / "data" / "replay" / row.job)
        if t.empty:
            continue
        sr_logged[row.job[:8]] = L.sharpe_stats(t.net_pnl)["sr"]
        rs = L.resim_close_path(t, eu_close, eu_atr, cost_rt_px=2 * c_eu.one_way_p50 * 1e-4)
        sr_resim[row.job[:8]] = L.sharpe_stats(rs.ret)["sr"]
        if str(row.start).startswith("2015") and (row.end is None or (isinstance(row.end, float) and np.isnan(row.end))):
            monthly_logged[row.job[:8]] = t.set_index("entry_ts").net_pnl.resample("MS").sum()
            monthly_resim[row.job[:8]] = rs.set_index("entry_ts").ret.resample("MS").sum()
    n_trials = len(sr_logged) + N_WF_SCREENS
    var_logged = float(np.nanvar(list(sr_logged.values()), ddof=1))
    var_resim = float(np.nanvar(list(sr_resim.values()), ddof=1))
    report["trials"] = {"n_eurusd_replay_runs_with_trades": len(sr_logged), "n_wf_screens": N_WF_SCREENS, "N": n_trials,
                        "var_sr_logged": var_logged, "var_sr_resim": var_resim}

    # --- 5. PBO via CSCV on EURUSD full-history variants (monthly returns) ---
    pbo = {}
    for nm, mm in [("logged", monthly_logged), ("resim_p50", monthly_resim)]:
        m = pd.DataFrame(mm).fillna(0.0)
        m = m.loc[:, ~m.T.duplicated()]  # drop byte-identical variants
        m = m[(m.index >= "2015-03-01") & (m.index < "2026-09-01")]
        pbo[nm] = L.pbo_cscv(m, n_splits=16)
    report["pbo_eurusd"] = pbo

    # --- 6. per-pair champion pins: logged vs close-path re-sim at measured p50 / p90 ---
    per_pair, months = {}, []
    pin_map = pins()
    pin_map["EURUSD_weekly_champion_dukascopy"] = "a0d93fc6"
    pin_map["EURUSD_overlap_book"] = "76c3d050"
    for label, job in pin_map.items():
        pair = label[:6]
        t = L.load_trades(L.find_job(job[:8]))
        h = L.load_history(pair)
        atr = L.close_atr(h.Close, factor=factor)
        pip = L.pip_size(pair)
        c = costs.loc[pair]
        rec = {"job": job[:8], "logged": summarize(t.net_pnl, t.entry_ts, n_trials, var_logged)}
        for q in ["p50", "p90"]:
            rs = L.resim_close_path(t, h.Close, atr, cost_rt_px=2 * float(c[f"one_way_{q}"]) * pip)
            rec[f"resim_{q}"] = summarize(rs.ret, rs.entry_ts, n_trials, var_resim)
            if q == "p50":
                mo = monthly(rs.ret, rs.entry_ts)
                mo["pair"] = label
                months.append(mo.reset_index().rename(columns={"index": "month", "entry_ts": "month"}))
                rec["monthly_resim_p50"] = {
                    "months": int(len(mo)), "share_pf_gt_1": float((mo.pf > 1).mean()),
                    "worst_month_sum_ret": float(mo.sum_ret.min()), "worst_month_dd": float(mo.dd.min()),
                    "worst_month": str(mo.sum_ret.idxmin()) if len(mo) else None,
                }
            rs.to_csv(OUT / f"resim_{label}_{q}.csv", index=False)
        mlog = monthly(t.net_pnl, t.entry_ts)
        rec["monthly_logged"] = {"months": int(len(mlog)), "share_pf_gt_1": float((mlog.pf > 1).mean())}
        per_pair[label] = rec
    pd.concat(months).to_csv(OUT / "monthly_resim_p50.csv", index=False)
    report["per_pair"] = per_pair
    (OUT / "edge_reality.json").write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["close_atr_factor", "resim_validation_clean_pre2022", "trials", "pbo_eurusd", "fill_bias_eurusd"]}, indent=1, default=float))
    rows = []
    for label, rec in per_pair.items():
        for b in ["logged", "resim_p50", "resim_p90"]:
            s = rec[b]
            rows.append({"pin": label, "book": b, **{k: s[k] for k in ["n", "pf", "win_rate", "exp_bp", "sr_trade", "sr_ann", "max_dd", "dsr", "min_trl_trades", "pf_pre_holdout", "pf_holdout", "dd_holdout", "n_holdout"]}})
    tab = pd.DataFrame(rows)
    tab.to_csv(OUT / "per_pair_table.csv", index=False)
    pd.set_option("display.width", 250)
    print(tab.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    for label, rec in per_pair.items():
        print(label, "monthly p50", rec["monthly_resim_p50"], "logged", rec["monthly_logged"])


if __name__ == "__main__":
    main()
