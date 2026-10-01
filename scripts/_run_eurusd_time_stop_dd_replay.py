"""Run the single EURUSD full-history horizon-6 DD hypothesis Replay.

Research-only: never promotes. Reverts config/default.yaml on any failed gate.
"""
from __future__ import annotations

import json
import time
import traceback\nimport uuid\nimport sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo\n\nROOT = Path(__file__).resolve().parents[1]\nif str(ROOT) not in sys.path:\n    sys.path.insert(0, str(ROOT))\n\nfrom forex_lab.config_loader import load_config
from forex_lab.history import load_history, load_meta, replay_store_dir
from forex_lab.replay import run_replay

DHAKA = ZoneInfo("Asia/Dhaka")
YAML = Path("config/default.yaml")
BAK = Path("config/default.yaml.bak_pre_horizon6_20261002")
JOBID_FILE = Path("_REPLAY_20261002_EURUSD_TIME_STOP_DD_6BARS_JOBID.txt")
JOB_JSON = Path("_REPLAY_20261002_EURUSD_TIME_STOP_DD_6BARS_JOB.json")
STRIKE = Path("_STRIKE_20261002_TIME_STOP_DD_6BARS.txt")


def now_dhaka() -> str:
    return datetime.now(DHAKA).strftime("%Y-%m-%d %H:%M:%S Asia/Dhaka")


def write_status(folder: Path, status: dict) -> None:
    status["updated_at"] = time.time()
    (folder / "status.json").write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")


def revert(reason: str) -> None:
    if BAK.exists():
        YAML.write_text(BAK.read_text(encoding="utf-8"), encoding="utf-8")
    STRIKE.write_text(
        "TIME-STOP DD HYPOTHESIS FAILED\n"
        f"time: {now_dhaka()}\nreason: {reason}\n"
        "action: reverted config/default.yaml to config/default.yaml.bak_pre_horizon6_20261002\n",
        encoding="utf-8",
    )


def main() -> None:
    cfg = load_config()
    assert int(cfg.get("horizon") or 0) == 6, cfg.get("horizon")
    rc = dict(cfg.get("replay") or {})
    assert abs(float(rc.get("slippage_pips") or 0.0) - 0.2) < 1e-12, rc
    assert bool(rc.get("exit_slippage")), rc
    assert bool(rc.get("use_bid_ask")), rc
    assert str(rc.get("source") or "auto") == "auto", rc
    assert abs(float((cfg.get("signals") or {}).get("min_confidence") or 0.0) - 0.60) < 1e-12
    assert not list((cfg.get("signals") or {}).get("sessions") or [])
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
        "hypothesis": "time_stop_dd_horizon_6",
        "horizon": 6,
        "costs_on": {"slippage_pips": 0.2, "exit_slippage": True, "use_bid_ask": True},
        "fraction": 0.0,
        "message": "Horizon-6 DD-control Replay",
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
        })
        write_status(folder, status)
        JOB_JSON.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
        print("DONE", job_id, now_dhaka(), flush=True)
        for row in scoreboard:
            print(row.get("book"), "PF", row.get("profit_factor"), "DD", row.get("max_drawdown"), "return", row.get("total_return"), "n", row.get("n_trades"), flush=True)
        print("PROMOTION", json.dumps(result.get("promotion"), default=str), flush=True)
        print("DD_GATE", json.dumps(gate), flush=True)
        if not gate["pass"]:
            reason = f"PF={pf_ok}, DD={dd_ok}, return={ret_ok}; challenger must beat champion PF/return and not worsen DD"
            revert(reason)
            print("FAIL_REVERT", reason, flush=True)
        else:
            print("PASS_NO_PROMOTE", "research gate passed; no promote action taken", flush=True)
    except Exception as exc:
        status.update({"status": "error", "phase": "error", "error": str(exc), "message": "Replay failed", "finished_at_dhaka": now_dhaka(), "traceback": traceback.format_exc()})
        write_status(folder, status)
        JOB_JSON.write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
        revert(f"Replay error: {exc}")
        print("ERROR_REVERT", exc, flush=True)
        raise


if __name__ == "__main__":
    main()

