"""Start EURUSD meta-label asia_quiet Replay (strike 2/3) in a fresh process."""
from __future__ import annotations
import json, time, traceback, uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from forex_lab.config_loader import load_config
from forex_lab.history import replay_store_dir, load_history, load_meta
from forex_lab.replay import run_replay

DHAKA = ZoneInfo("Asia/Dhaka")

def dhaka_now() -> str:
    return datetime.now(DHAKA).strftime("%Y-%m-%d %H:%M Asia/Dhaka")

def main() -> None:
    cfg = load_config()
    ml = dict((cfg.get("replay") or {}).get("meta_label") or {})
    assert bool(ml.get("enabled")), ml
    assert str(ml.get("rule") or "") == "asia_quiet", ml
    pair, interval, start = "EURUSD", "1h", "2015-01-01"
    job_id = uuid.uuid4().hex
    folder = replay_store_dir(cfg) / job_id
    folder.mkdir(parents=True, exist_ok=True)
    status = {
        "job_id": job_id, "kind": "replay", "status": "running", "phase": "replay",
        "pair": pair, "interval": interval, "start": start, "end": None,
        "min_confidence": float((cfg.get("signals") or {}).get("min_confidence") or 0.60),
        "fraction": 0.0, "message": "Meta-label asia_quiet strike2 (Asia-only atr<=0.20)",
        "as_of_dhaka": dhaka_now(), "error": None, "reason": None,
        "hypothesis": "meta_label_asia_quiet", "meta_label": ml,
        "finished_at_dhaka": None, "source": None, "bid_ask": None,
        "rows": None, "report": None, "updated_at": time.time(),
    }
    (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    Path("_REPLAY_20261001_EURUSD_META_LABEL_S2_JOBID.txt").write_text(job_id + "\n", encoding="utf-8")
    print("STARTED", job_id, dhaka_now(), flush=True)

    def progress(payload: dict) -> None:
        status["fraction"] = float(payload.get("fraction") or status.get("fraction") or 0)
        status["message"] = str(payload.get("message") or status.get("message"))
        status["phase"] = str(payload.get("phase") or status.get("phase"))
        status["as_of_dhaka"] = dhaka_now()
        status["updated_at"] = time.time()
        (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        frac = status["fraction"]
        if frac == 0 or int(frac * 100) % 10 == 0:
            print(f"  [{frac:.1%}] {status['message']}", flush=True)

    try:
        meta = load_meta(pair, interval, cfg) or {}
        frame = load_history(pair, cfg, interval, start=start, end=None)
        result = run_replay(
            frame, cfg, pair, interval=interval, job_dir=folder,
            source=str(meta.get("source") or "cache"), progress=progress,
        )
        sb = result.get("scoreboard") or []
        status.update({
            "status": "done", "phase": "done", "fraction": 1.0,
            "message": "Scoreboard ready", "finished_at_dhaka": dhaka_now(),
            "source": str(meta.get("source") or "cache"), "bid_ask": True,
            "rows": int(len(frame)), "scoreboard": sb,
            "promotion": result.get("promotion"),
            "promotion_line": result.get("promotion_line"),
            "updated_at": time.time(),
        })
        (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        Path("_REPLAY_20261001_EURUSD_META_LABEL_S2_JOB.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        print("DONE", job_id, dhaka_now(), flush=True)
        for row in sb:
            print(row.get("book"), "PF", row.get("profit_factor"), "n", row.get("n_trades"),
                  "WR", row.get("win_rate"), "DD", row.get("max_drawdown"),
                  "ret", row.get("total_return"), flush=True)
    except Exception as exc:
        status.update({
            "status": "error", "phase": "error", "error": str(exc),
            "reason": "exception", "finished_at_dhaka": dhaka_now(),
            "traceback": traceback.format_exc(), "updated_at": time.time(),
        })
        (folder / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        Path("_REPLAY_20261001_EURUSD_META_LABEL_S2_JOB.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        print("ERROR", job_id, exc, flush=True)
        raise

if __name__ == "__main__":
    main()
