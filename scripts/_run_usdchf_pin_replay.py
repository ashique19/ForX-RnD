"""USDCHF after-cost full-history pin Replay."""
from __future__ import annotations
import json, time, traceback, uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from forex_lab.config_loader import load_config
from forex_lab.history import replay_store_dir, load_history, load_meta
from forex_lab.replay import run_replay

DHAKA = ZoneInfo("Asia/Dhaka")
def dhaka_now():
    return datetime.now(DHAKA).strftime("%Y-%m-%d %H:%M Asia/Dhaka")

def main():
    cfg = load_config()
    assert int((cfg.get("walk_forward") or {}).get("purge_bars") or 0) == 0
    assert not bool(((cfg.get("replay") or {}).get("size_by_conf") or {}).get("enabled"))
    assert not bool(((cfg.get("replay") or {}).get("meta_label") or {}).get("enabled"))
    pair, interval, start = "USDCHF", "1h", "2015-01-01"
    job_id = uuid.uuid4().hex
    folder = replay_store_dir(cfg) / job_id
    folder.mkdir(parents=True, exist_ok=True)
    status = {
        "job_id": job_id, "kind": "replay", "status": "running", "phase": "replay",
        "pair": pair, "interval": interval, "start": start, "end": None,
        "min_confidence": 0.60, "fraction": 0.0, "message": "USDCHF after-cost pin",
        "as_of_dhaka": dhaka_now(), "error": None, "hypothesis": "USDCHF_pin",
        "finished_at_dhaka": None, "source": None, "bid_ask": None, "rows": None,
        "updated_at": time.time(),
    }
    (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    Path("_REPLAY_20261001_USDCHF_PIN_JOBID.txt").write_text(job_id+"\n", encoding="utf-8")
    print("STARTED", job_id, dhaka_now(), flush=True)
    def progress(payload):
        status["fraction"] = float(payload.get("fraction") or 0)
        status["message"] = str(payload.get("message") or "")
        status["as_of_dhaka"] = dhaka_now(); status["updated_at"] = time.time()
        (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        frac = status["fraction"]
        if frac == 0 or int(frac*100) % 10 == 0:
            print(f"  [{frac:.1%}] {status['message']}", flush=True)
    try:
        meta = load_meta(pair, interval, cfg) or {}
        frame = load_history(pair, cfg, interval, start=start, end=None)
        result = run_replay(frame, cfg, pair, interval=interval, job_dir=folder,
                            source=str(meta.get("source") or "jetta"), progress=progress)
        sb = result.get("scoreboard") or []
        status.update({
            "status":"done","phase":"done","fraction":1.0,"message":"Scoreboard ready",
            "finished_at_dhaka": dhaka_now(), "source": str(meta.get("source") or "jetta"),
            "bid_ask": True, "rows": int(len(frame)), "scoreboard": sb,
            "promotion": result.get("promotion"), "promotion_line": result.get("promotion_line"),
            "updated_at": time.time(),
        })
        (folder/"status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        Path("_REPLAY_20261001_USDCHF_PIN_JOB.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        print("DONE", job_id, dhaka_now(), flush=True)
        for row in sb:
            print(row.get("book"), "PF", row.get("profit_factor"), "n", row.get("n_trades"),
                  "WR", row.get("win_rate"), "DD", row.get("max_drawdown"), "ret", row.get("total_return"), flush=True)
    except Exception as exc:
        status.update({"status":"error","error":str(exc),"finished_at_dhaka":dhaka_now(),"traceback":traceback.format_exc()})
        (folder/"status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        raise

if __name__ == "__main__":
    main()

