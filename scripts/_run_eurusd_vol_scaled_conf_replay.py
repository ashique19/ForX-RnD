"""Start EURUSD vol_scaled_conf Replay in a fresh process (ONE serial).

Enables signals.vol_scaled_conf in-memory only. Does NOT rewrite default.yaml live.
Gate vs pin 2cee7e71; promote stays research-only (caller reports promote=null).
"""
from __future__ import annotations
import json, time, traceback, uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from forex_lab.config_loader import load_config
from forex_lab.history import replay_store_dir, load_history, load_meta
from forex_lab.replay import run_replay
from forex_lab.vol_scaled_conf import apply_replay_vol_scaled_conf, vol_scaled_conf_block

DHAKA = ZoneInfo("Asia/Dhaka")
PIN = "2cee7e7163134266ad8c2b39234442d8"

def dhaka_now() -> str:
    return datetime.now(DHAKA).strftime("%Y-%m-%d %H:%M Asia/Dhaka")

def main() -> None:
    cfg = load_config()
    sig = dict(cfg.get("signals") or {})
    vsc = dict(sig.get("vol_scaled_conf") or {})
    vsc["enabled"] = True
    # hyp defaults if somehow missing
    vsc.setdefault("metric", "atr_pctile")
    vsc.setdefault("vol_ref", 0.5)
    vsc.setdefault("lo", 0.90)
    vsc.setdefault("hi", 1.15)
    vsc.setdefault("atr_window", 100)
    sig["vol_scaled_conf"] = vsc
    cfg["signals"] = sig
    apply_replay_vol_scaled_conf(cfg)
    assert bool(vsc.get("enabled")), vsc

    pair, interval, start = "EURUSD", "1h", "2015-01-01"
    job_id = uuid.uuid4().hex
    folder = replay_store_dir(cfg) / job_id
    folder.mkdir(parents=True, exist_ok=True)
    status = {
        "job_id": job_id, "kind": "replay", "status": "running", "phase": "replay",
        "pair": pair, "interval": interval, "start": start, "end": None,
        "min_confidence": float(sig.get("min_confidence") or 0.60),
        "fraction": 0.0, "message": "vol_scaled_conf challenger vs pin",
        "as_of_dhaka": dhaka_now(),
        "error": None, "reason": None, "hypothesis": "vol_scaled_conf",
        "vol_scaled_conf": vsc, "pin_ref": PIN,
        "finished_at_dhaka": None, "source": None, "bid_ask": None,
        "rows": None, "report": None, "updated_at": time.time(),
    }
    (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    Path("_REPLAY_20261001_EURUSD_VOL_SCALED_CONF_JOBID.txt").write_text(job_id + "\n", encoding="utf-8")
    print("STARTED", job_id, dhaka_now(), flush=True)
    print("NOTE", cfg.get("replay", {}).get("vol_scaled_conf_note"), flush=True)

    def progress(payload: dict) -> None:
        status["fraction"] = float(payload.get("fraction") or status.get("fraction") or 0)
        status["message"] = str(payload.get("message") or status.get("message"))
        status["phase"] = str(payload.get("phase") or status.get("phase"))
        status["as_of_dhaka"] = dhaka_now(); status["updated_at"] = time.time()
        (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        frac = status["fraction"]
        if frac == 0 or int(frac * 100) % 10 == 0:
            line = f"  [{frac:.1%}] {status['message']}"
            try:
                print(line, flush=True)
            except OSError:
                Path("_REPLAY_20261001_EURUSD_VOL_SCALED_CONF_PROGRESS.log").open("a", encoding="utf-8").write(line + "\n")

    try:
        meta = load_meta(pair, interval, cfg) or {}
        frame = load_history(pair, cfg, interval, start=start, end=None)
        result = run_replay(frame, cfg, pair, interval=interval, job_dir=folder,
                            source=str(meta.get("source") or "cache"), progress=progress)
        sb = result.get("scoreboard") or []
        status.update({
            "status": "done", "phase": "done", "fraction": 1.0, "message": "Scoreboard ready",
            "finished_at_dhaka": dhaka_now(), "source": str(meta.get("source") or "cache"),
            "bid_ask": True, "rows": int(len(frame)), "scoreboard": sb,
            "promotion": result.get("promotion"), "promotion_line": result.get("promotion_line"),
            "updated_at": time.time(),
        })
        (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        Path("_REPLAY_20261001_EURUSD_VOL_SCALED_CONF_JOB.json").write_text(
            json.dumps(status, indent=2), encoding="utf-8"
        )
        print("DONE", job_id, dhaka_now(), flush=True)
        for row in sb:
            print(row.get("book"), "PF", row.get("profit_factor"), "n", row.get("n_trades"),
                  "WR", row.get("win_rate"), "DD", row.get("max_drawdown"),
                  "ret", row.get("total_return"), flush=True)
    except Exception as exc:
        status.update({"status": "error", "phase": "error", "error": str(exc),
                       "message": "Replay failed", "finished_at_dhaka": dhaka_now(),
                       "traceback": traceback.format_exc(), "updated_at": time.time()})
        (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        print("ERROR", exc, flush=True); raise

if __name__ == "__main__":
    main()
