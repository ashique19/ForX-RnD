"""Serial short-window min_confidence grid for tighten pass A."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000"
START = "2024-09-24"  # ~2y window ending at CSV tip
PAIR = "EURUSD"
INTERVAL = "1h"
GRID = [0.40, 0.50, 0.55, 0.65]
OUT_MD = ROOT / "data" / "replay" / "tighten_A_confidence_grid_compare.md"
STATE = ROOT / "data" / "replay" / "tighten_A_grid_state.json"


def api(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def start_job(min_conf: float) -> dict:
    return api(
        "POST",
        "/replay/train",
        {
            "pair": PAIR,
            "interval": INTERVAL,
            "start": START,
            "pull": False,
            "min_confidence": min_conf,
        },
    )


def get_job(job_id: str) -> dict:
    return api("GET", f"/replay/jobs/{job_id}")


def wait_done(job_id: str, poll_s: float = 15.0) -> dict:
    while True:
        st = get_job(job_id)
        status = st.get("status")
        frac = st.get("fraction")
        msg = st.get("message")
        print(f"[{datetime.now():%H:%M:%S}] {job_id[:8]} status={status} frac={frac} msg={msg}", flush=True)
        if status in {"done", "error"}:
            return st
        time.sleep(poll_s)


def rows_from_status(st: dict) -> list[dict]:
    board = st.get("scoreboard") or []
    out = []
    for row in board:
        out.append(
            {
                "book": row.get("book"),
                "n_trades": row.get("n_trades"),
                "win_rate": row.get("win_rate"),
                "profit_factor": row.get("profit_factor"),
                "total_return": row.get("total_return"),
                "max_drawdown": row.get("max_drawdown"),
            }
        )
    return out


def pick_winner(trials: list[dict]) -> dict:
    """Winner by champion PF, then return, then DD (less negative). Must beat 0.40 control."""
    control = next(t for t in trials if abs(t["min_confidence"] - 0.40) < 1e-9)
    ctrl_pf = _champ(control)["profit_factor"]
    ctrl_ret = _champ(control)["total_return"]
    ctrl_dd = _champ(control)["max_drawdown"]

    def key(t: dict):
        c = _champ(t)
        pf = c["profit_factor"] if c["profit_factor"] is not None else float("-inf")
        ret = c["total_return"] if c["total_return"] is not None else float("-inf")
        dd = c["max_drawdown"] if c["max_drawdown"] is not None else float("-inf")
        return (pf, ret, dd)

    ranked = sorted(trials, key=key, reverse=True)
    best = ranked[0]
    # Prefer a grid point that beats control; if none do, still report best and note.
    beaters = [
        t
        for t in ranked
        if _champ(t)["profit_factor"] is not None
        and (
            _champ(t)["profit_factor"] > ctrl_pf
            or (
                abs(_champ(t)["profit_factor"] - ctrl_pf) < 1e-12
                and _champ(t)["total_return"] > ctrl_ret
            )
            or (
                abs(_champ(t)["profit_factor"] - ctrl_pf) < 1e-12
                and abs(_champ(t)["total_return"] - ctrl_ret) < 1e-12
                and _champ(t)["max_drawdown"] > ctrl_dd
            )
        )
    ]
    # Exclude control itself from "beater" preference unless it's genuinely best alone
    non_ctrl_beaters = [t for t in beaters if abs(t["min_confidence"] - 0.40) > 1e-9]
    if non_ctrl_beaters:
        winner = non_ctrl_beaters[0]
        note = "beats 0.40 short-window control on PF/return/DD order"
    else:
        winner = best
        note = "no grid point beat 0.40 control; picking best by PF/return/DD (may be control)"
    return {"winner": winner, "note": note, "control": control}


def _champ(trial: dict) -> dict:
    for row in trial["books"]:
        if row.get("book") == "champion":
            return row
    return trial["books"][0] if trial["books"] else {}


def fmt(x, digits=4):
    if x is None:
        return "—"
    if isinstance(x, (int,)) and not isinstance(x, bool):
        return str(x)
    try:
        return f"{float(x):.{digits}f}"
    except (TypeError, ValueError):
        return str(x)


def write_md(trials: list[dict], pick: dict) -> None:
    lines = []
    lines.append("# Tighten pass A — min_confidence short-window grid")
    lines.append("")
    lines.append(f"- Window: `{START}` → CSV tip (pull=false), EURUSD 1h")
    lines.append("- Shared knob: `signals.min_confidence` via per-job Replay API override (live `config/default.yaml` not rewritten per trial)")
    lines.append("- Full-history baseline reference (not re-run): job `c4c1e0b62797488d985e3b4cd4e43131` at 0.40")
    lines.append("- Ranking: champion PF → total return → max DD (less negative better)")
    lines.append(f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M Asia/Dhaka')}")
    lines.append("")
    lines.append("## Scoreboard (champion / challenger / sma)")
    lines.append("")
    lines.append("| min_confidence | job_id | book | n_trades | WR | PF | return | DD |")
    lines.append("|---:|---|---|---:|---:|---:|---:|---:|")
    for t in trials:
        for row in t["books"]:
            lines.append(
                f"| {t['min_confidence']:.2f} | `{t['job_id']}` | {row['book']} | "
                f"{fmt(row['n_trades'],0)} | {fmt(row['win_rate'])} | {fmt(row['profit_factor'])} | "
                f"{fmt(row['total_return'])} | {fmt(row['max_drawdown'])} |"
            )
    w = pick["winner"]
    lines.append("")
    lines.append("## Winner")
    lines.append("")
    lines.append(f"- **Winning min_confidence: `{w['min_confidence']:.2f}`** (job `{w['job_id']}`)")
    lines.append(f"- Note: {pick['note']}")
    c = _champ(w)
    lines.append(
        f"- Champion: n={fmt(c.get('n_trades'),0)} WR={fmt(c.get('win_rate'))} "
        f"PF={fmt(c.get('profit_factor'))} ret={fmt(c.get('total_return'))} DD={fmt(c.get('max_drawdown'))}"
    )
    lines.append("")
    lines.append("## Live config")
    lines.append("")
    lines.append("- Per-trial: API `min_confidence` override only (desk yaml left at 0.40 during grid).")
    lines.append("- After grid: leave live `signals.min_confidence` at winning value only if clearly better; else interim 0.55 — see post-grid step.")
    lines.append("")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_MD}", flush=True)


def main() -> int:
    trials: list[dict] = []
    if STATE.exists():
        try:
            trials = json.loads(STATE.read_text(encoding="utf-8")).get("trials") or []
        except json.JSONDecodeError:
            trials = []
    done_confs = {round(t["min_confidence"], 2) for t in trials if t.get("status") == "done"}

    for mc in GRID:
        if round(mc, 2) in done_confs:
            print(f"skip already done {mc}", flush=True)
            continue
        print(f"=== starting min_confidence={mc} start={START} pull=false ===", flush=True)
        try:
            started = start_job(mc)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            print(f"start failed: {exc.code} {body}", flush=True)
            return 1
        job_id = started["job_id"]
        print(f"job_id={job_id}", flush=True)
        done = wait_done(job_id)
        trial = {
            "min_confidence": mc,
            "job_id": job_id,
            "status": done.get("status"),
            "error": done.get("error"),
            "rows": done.get("rows"),
            "books": rows_from_status(done),
            "finished_at_dhaka": done.get("finished_at_dhaka"),
        }
        # replace any prior partial for this conf
        trials = [t for t in trials if round(t["min_confidence"], 2) != round(mc, 2)]
        trials.append(trial)
        STATE.write_text(json.dumps({"trials": trials}, indent=2), encoding="utf-8")
        if done.get("status") != "done":
            print(f"FAILED {mc}: {done.get('error')}", flush=True)
            return 1

    trials = sorted(trials, key=lambda t: t["min_confidence"])
    pick = pick_winner(trials)
    write_md(trials, pick)
    STATE.write_text(
        json.dumps({"trials": trials, "pick": {
            "min_confidence": pick["winner"]["min_confidence"],
            "job_id": pick["winner"]["job_id"],
            "note": pick["note"],
        }}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(pick["winner"] | {"note": pick["note"]}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
