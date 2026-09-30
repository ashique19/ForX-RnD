"""Paper journal auto-stats with conf/session bins (desk research only)."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from forex_lab.config_loader import load_config

DHAKA = ZoneInfo("Asia/Dhaka")

def _pf(s: pd.Series) -> float:
    gp = float(s[s > 0].sum())
    gl = float((-s[s < 0]).sum())
    if gl <= 0:
        return float("inf") if gp > 0 else float("nan")
    return gp / gl

def main() -> None:
    cfg = load_config()
    store = Path(str((cfg.get("broker") or {}).get("store") or "data/paper_broker.json"))
    now = datetime.now(DHAKA).strftime("%Y-%m-%d %H:%M Asia/Dhaka")
    lines = [f"# Paper journal auto-stats - {now}", f"Store: `{store}`", ""]
    if not store.exists():
        lines.append("No paper journal file yet.")
        Path("_PAPER_JOURNAL_STATS_20261001.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return
    data = json.loads(store.read_text(encoding="utf-8"))
    closed = list(data.get("closed") or [])
    opens = [p for p in (data.get("positions") or []) if str(p.get("status")) == "open"]
    lines += [f"Open positions: **{len(opens)}**", f"Closed trades: **{len(closed)}**", ""]
    for p in opens:
        lines.append(
            f"- OPEN {p.get('pair')} {p.get('side')} size={p.get('size')} "
            f"entry={p.get('entry_price')} conf={p.get('confidence')}"
        )
    if closed:
        df = pd.DataFrame(closed)
        df["realized"] = pd.to_numeric(df.get("realized"), errors="coerce")
        df["confidence"] = pd.to_numeric(df.get("confidence"), errors="coerce")
        lines.append("## Closed by pair")
        lines.append("| pair | n | WR | exp | sum | PF |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for pair, g in df.groupby(df["pair"].astype(str).str.upper()):
            s = g["realized"]
            lines.append(
                f"| {pair} | {len(g)} | {(s > 0).mean():.3f} | {s.mean():.6f} | "
                f"{s.sum():.4f} | {_pf(s):.3f} |"
            )
        lines.append("")
        # conf bins
        bins = [0.0, 0.60, 0.70, 0.80, 1.01]
        labels = ["<0.60", "0.60-0.70", "0.70-0.80", "0.80+"]
        df["conf_bin"] = pd.cut(df["confidence"].fillna(0), bins=bins, labels=labels, right=False)
        lines.append("## Closed by confidence bin")
        lines.append("| conf_bin | n | WR | exp | sum | PF |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for lab, g in df.groupby("conf_bin", observed=False):
            if g.empty:
                continue
            s = g["realized"]
            lines.append(
                f"| {lab} | {len(g)} | {(s > 0).mean():.3f} | {s.mean():.6f} | "
                f"{s.sum():.4f} | {_pf(s):.3f} |"
            )
        lines.append("")
        # exit reason
        if "exit_reason" in df.columns:
            lines.append("## Closed by exit reason")
            lines.append("| exit | n | WR | sum |")
            lines.append("|---|---:|---:|---:|")
            for reason, g in df.groupby(df["exit_reason"].astype(str)):
                s = g["realized"]
                lines.append(f"| {reason} | {len(g)} | {(s > 0).mean():.3f} | {s.sum():.4f} |")
            lines.append("")
        lines.append("## Last 8 closed")
        for _, r in df.tail(8).iterrows():
            lines.append(
                f"- {r.get('pair')} {r.get('side')} realized={r.get('realized')} "
                f"conf={r.get('confidence')} exit={r.get('exit_reason')} @ {r.get('exit_time')}"
            )
    else:
        lines.append("No closed paper trades yet - journal ready for desk session.")
    out = Path("_PAPER_JOURNAL_STATS_20261001.txt")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))

if __name__ == "__main__":
    main()
