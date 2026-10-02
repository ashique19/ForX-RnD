"""Run the single EURUSD full-history exit_hold min_bars_before_sl=3 DD hypothesis Replay.

Research-only: never promotes. Reverts config/default.yaml on any failed gate.
KEEP yaml at 3 only on clear improve-gate PASS (PF up, return up, DD not worse).
"""
from __future__ import annotations

import json
import time
import traceback
import uuid
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.config_loader import load_config
from forex_lab.history import load_history, load_meta, replay_store_dir
from forex_lab.replay import run_replay

DHAKA = ZoneInfo("Asia/Dhaka")
YAML = Path("config/default.yaml")
BAK = Path("config/default.yaml.bak_pre_exit_hold3_20261002")
JOBID_FILE = Path("_REPLAY_20261002_EURUSD_EXIT_HOLD_3_DD_JOBID.txt")
JOB_JSON = Path("_REPLAY_20261002_EURUSD_EXIT_HOLD_3_DD_JOB.json")
STRIKE = Path("_FAIL_STRIKE_20261002_EURUSD_EXIT_HOLD_3_DD.txt")
RESULT = Path("_RESULT_20261002_EURUSD_EXIT_HOLD_3_DD.txt")


def now_dhaka() -> str:
    return datetime.now(DHAKA).strftime("%Y-%m-%d %H:%M:%S Asia/Dhaka")


def write_status(folder: Path, status: dict) -> None:
    status["updated_at"] = time.time()
    (folder / "status.json").write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")


def revert(reason: str) -> None:
    if BAK.exists():
        YAML.write_text(BAK.read_text(encoding="utf-8"), encoding="utf-8")
    STRIKE.write_text(
        "EXIT_HOLD min_bars_before_sl=3 DD HYPOTHESIS FAILED\n"
        f"time: {now_dhaka()}\nreason: {reason}\n"
        "action: reverted config/default.yaml to config/default.yaml.bak_pre_exit_hold3_20261002\n",
        encoding="utf-8",
    )


def main() -> None:
    cfg = load_config()
    assert int(cfg.get("horizon") or 0) == 8, cfg.get("horizon")
    barrier = dict(cfg.get("barrier") or {})
    assert abs(float(barrier.get("sl_atr") or 0.0) - 2.0) < 1e-12, barrier
    assert abs(float(barrier.get("tp_atr") or 0.0) - 2.0) < 1e-12, barrier
    rc = dict(cfg.get("replay") or {})
    eh = dict(rc.get("exit_hold") or {})
    assert int(eh.get("min_bars_before_sl") or 0) == 3, eh
    assert abs(float(rc.get("slippage_pips") or 0.0) - 0.2) < 1e-12, rc
    assert bool(rc.get("exit_slippage")), rc
    assert bool(rc.get("use_bid_ask")), rc
    assert str(rc.get("source") or "auto") == "auto", rc
    sig = dict(cfg.get("signals") or {})
    assert abs(float(sig.get("min_confidence") or 0.0) - 0.60) < 1e-12, sig
    assert abs(float(sig.get("min_tp_pips") or 0.0) - 0.0) < 1e-12, sig
    assert abs(float(sig.get("min_vol_regime") or 0.0) - 0.0) < 1e-12, sig
    assert not list(sig.get("sessions") or []), sig
    assert not bool((cfg.get("portfolio_brain") or {}).get("enabled"))

    pair, interval, start = "EURUSD", "1h", "2015-01-01"
    job_id = uuid.uuid4().hex
    folder = replay_store_dir(cfg) / job_id
    folder.mkdir(parents=True, exist_ok=True)
    status = {
        "job_id": job_id,
        "kind": "replay",
        "status": "running",
        "phase": "replay",
        "pair": pair,
        "interval": interval,
        "start": start,
        "end": None,
        "hypothesis": "exit_hold_min_bars_before_sl_3_dd",
        "exit_hold_min_bars_before_sl": 3,
        "horizon": 8,
        "costs_on": {"slippage_pips": 0.2, "exit_slippage": True, "use_bid_ask": True},
        "fraction": 0.0,
        "message": "Exit-hold min_bars_before_sl=3 DD-control Replay",
        "as_of_dhaka": now_dhaka(),
        "error": None,
        "reason": None,
        "finished_at_dhaka": None,
        "updated_at": time.time(),
    }
    write_status(folder, status)
    JOBID_FILE.write_text(job_id + "\n", encoding="utf-8")
    print("STARTED", job_id, now_dhaka(), flush=True)

    def progress(payload: dict) -> None:
        status["fraction"] = float(payload.get("fraction") or status.get("fraction") or 0.0)
        status["phase"] = str(payload.get("phase") or status.get("phase"))
        status["message"] = str(payload.get("message") or status.get("message"))
        status["as_of_dhaka"] = now_dhaka()
        write_status(folder, status)
        frac = status["fraction"]
        if frac == 0.0 or int(frac * 100) % 10 == 0:
            print(f"[{frac:.1%}] {status['message']}", flush=True)

    try:
        meta = load_meta(pair, interval, cfg) or {}
        frame = load_history(pair, cfg, interval, start=start, end=None)
        result = run_replay(
            frame,
            cfg,
            pair,
            interval=interval,
            job_dir=folder,
            source=str(meta.get("source") or "cache"),
            progress=progress,
        )
        scoreboard = result.get("scoreboard") or []
        champ = next((r for r in scoreboard if r.get("book") == "champion"), {}) or {}
        chall = next((r for r in scoreboard if r.get("book") == "challenger"), {}) or {}
        pf_ok = float(chall.get("profit_factor") or float("-inf")) > float(champ.get("profit_factor") or float("inf"))
        dd_ok = float(chall.get("max_drawdown") or float("-inf")) >= float(champ.get("max_drawdown") or float("inf"))
        ret_ok = float(chall.get("total_return") or float("-inf")) > float(champ.get("total_return") or float("inf"))
        gate = {"pf_ok": pf_ok, "dd_ok": dd_ok, "return_ok": ret_ok, "pass": bool(pf_ok and dd_ok and ret_ok)}
        status.update({
            "status": "done" if gate["pass"] else "failed",
            "phase": "done" if gate["pass"] else "failed",
            "fraction": 1.0,
            "message": "DD gate passed" if gate["pass"] else "DD gate failed",
            "finished_at_dhaka": now_dhaka(),
            "source": str(meta.get("source") or "cache"),
            "rows": int(len(frame)),
            "scoreboard": scoreboard,
            "promotion": result.get("promotion"),
            "promotion_line": result.get("promotion_line"),
            "gate": gate,
            "yaml_action": "KEEP_exit_hold_3" if gate["pass"] else "REVERT_to_bak",
        })
        write_status(folder, status)
        JOB_JSON.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
        print("DONE", job_id, now_dhaka(), flush=True)
        for row in scoreboard:
            print(row.get("book"), "PF", row.get("profit_factor"), "DD", row.get("max_drawdown"), "return", row.get("total_return"), "n", row.get("n_trades"), flush=True)
        print("PROMOTION", json.dumps(result.get("promotion"), default=str), flush=True)
        print("DD_GATE", json.dumps(gate), flush=True)
        if not gate["pass"]:
            reason = (
                f"PF={pf_ok}, DD={dd_ok}, return={ret_ok}; "
                "challenger must beat champion PF/return and not worsen DD"
            )
            revert(reason)
            print("FAIL_REVERT", reason, flush=True)
        else:
            RESULT.write_text(
                "EXIT_HOLD min_bars_before_sl=3 DD HYPOTHESIS PASSED (research only)\n"
                f"time: {now_dhaka()}\n"
                f"job_id: {job_id}\n"
                f"challenger PF={chall.get('profit_factor')} return={chall.get('total_return')} "
                f"DD={chall.get('max_drawdown')} n={chall.get('n_trades')}\n"
                f"champion PF={champ.get('profit_factor')} return={champ.get('total_return')} "
                f"DD={champ.get('max_drawdown')} n={champ.get('n_trades')}\n"
                "action: KEEP config/default.yaml exit_hold.min_bars_before_sl=3; promote=null; "
                "no live sessions/gates flipped\n",
                encoding="utf-8",
            )
            print("PASS_KEEP_YAML", "research gate passed; no promote; yaml kept at 3", flush=True)
    except Exception as exc:
        status.update({
            "status": "error",
            "phase": "error",
            "error": str(exc),
            "message": "Replay failed",
            "finished_at_dhaka": now_dhaka(),
            "traceback": traceback.format_exc(),
        })
        write_status(folder, status)
        JOB_JSON.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
        revert(f"Replay error: {exc}")
        print("ERROR_REVERT", exc, flush=True)
        raise


if __name__ == "__main__":
    main()
