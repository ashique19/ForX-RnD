"""Exploration paper book pass (separate from the Stage-2 main burst).

Reads GET /board only. Never calls /paper/order or /portfolio?sync, never
writes the main journal, never changes YAML / gates / min_conf, never trains.
Journals to data/paper_shadow/explore_journal.jsonl (gitignored).

Run:
  .venv\\Scripts\\python.exe scripts\\explore_paper_burst.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.explore_book import EXPLORE_CONFIG, explore_journal_path, load, run_pass  # noqa: E402
from forex_lab.explore_book import status as book_status  # noqa: E402
from forex_lab.paper_shadow import fill_count, journal_path  # noqa: E402

DHAKA = ZoneInfo("Asia/Dhaka")
HALT = ROOT / "data" / "paper_explore" / "HALT"


def _now_dhaka() -> datetime:
    return datetime.now(DHAKA)


def _hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def http_json(base: str, method: str, path: str) -> tuple[int, object]:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    req = Request(base.rstrip("/") + path, method=method, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=60) as response:
            return int(response.status), json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        return int(exc.code), None
    except (OSError, URLError, ValueError) as exc:
        return 0, {"error": str(exc)}


def _step(result: dict, key: str, fn):
    """Run one post-pass step; a failure is recorded, never aborts the remaining steps."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        result.setdefault("step_errors", []).append({"step": key, "error": f"{type(exc).__name__}: {exc}"})
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Exploration paper book pass (never Stage-2)")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--no-mt5-exec", action="store_true", help="skip MT5 DEMO order mirroring")
    args = ap.parse_args(argv)
    halt = HALT
    if halt.exists():
        print(json.dumps({"ok": True, "halted": True, "reason": f"HALT file present: {halt}"}, indent=2))
        return 0
    now_dhaka = _now_dhaka()
    main_j = journal_path()
    result = {
        "generated_at_dhaka": now_dhaka.strftime("%Y-%m-%d %H:%M:%S Asia/Dhaka"),
        "book": "explore",
        "config": EXPLORE_CONFIG,
        "explore_journal": str(explore_journal_path()),
        "explore_events_before": len(load()),
        "main_journal_sha_before": _hash(main_j),
        "main_stage2_fills_before": fill_count(),
    }
    if now_dhaka.weekday() >= 5:
        result.update({"ok": True, "market_closed": True, "reason": "weekend Asia/Dhaka; no exploration pass"})
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    health_status, health = http_json(args.base_url, "GET", "/health")
    if health_status != 200 or not isinstance(health, dict) or health.get("ok") is not True:
        result.update({"ok": False, "reason": "desk API health check failed", "health_status": health_status})
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2
    board_status, board = http_json(args.base_url, "GET", "/board")
    if board_status != 200 or not isinstance(board, dict):
        result.update({"ok": False, "reason": "board fetch failed", "board_status": board_status})
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 3
    rows = [r for r in (board.get("rows") or []) if isinstance(r, dict)]
    before_ids = {str(e.get("position_id")) for e in load() if e.get("event") == "open"}
    exec_on = not args.no_mt5_exec and os.environ.get("FORX_MT5_EXEC", "on").lower() != "off"
    hold: list[str] = []
    live: list[str] = []
    if exec_on:
        # Broker truth first: positions the MT5 demo already closed are booked at
        # the broker's real exit; unknown ones are held (paper won't exit them).
        from forex_lab.mt5_exec import reconcile_broker  # noqa: PLC0415

        rec = _step(result, "reconcile_broker", reconcile_broker) or {}
        result["broker_reconcile"] = rec
        # Broker state unknown (reconcile failed) -> hold every mirrored exit this pass.
        hold = list(rec.get("hold") or []) if rec else None
        live = list(rec.get("live") or [])
    else:
        result["broker_reconcile"] = {"enabled": False, "reason": "mt5 exec off; paper-only exits"}
    if hold is None:
        from forex_lab.explore_book import open_positions  # noqa: PLC0415

        mirrored = {str(e.get("position_id")) for e in load() if e.get("event") == "mt5_open" and e.get("ok")}
        hold = [pid for pid in open_positions(load()) if pid in mirrored]
    out: dict = {}
    try:
        out = run_pass(rows, hold_ids=hold, broker_exit_ids=live)
    finally:
        # Whatever happens after the pass, new opens are mirrored in THIS pass (no orphans).
        new_ids = [str(e.get("position_id")) for e in load()
                   if e.get("event") == "open" and str(e.get("position_id")) not in before_ids]
        if not exec_on:
            result["mt5_exec"] = {"enabled": False, "reason": "--no-mt5-exec" if args.no_mt5_exec else "FORX_MT5_EXEC=off"}
        else:
            from forex_lab.mt5_exec import close_and_book, mirror  # noqa: PLC0415

            # Demo close goes out in this same pass; paper close is booked at the real fill.
            result["broker_exits"] = _step(result, "close_and_book",
                                           lambda: close_and_book(out.get("pending_broker_exits") or []))
            result["mt5_exec"] = _step(result, "mirror", lambda: mirror(new_ids))
    result.update(out)
    result["status"] = _step(result, "status", book_status)
    result["explore_events_after"] = len(load())
    result["main_journal_sha_after"] = _hash(main_j)
    result["main_journal_untouched"] = result["main_journal_sha_before"] == result["main_journal_sha_after"]
    result["main_stage2_fills_after"] = fill_count()
    result["ok"] = not result.get("step_errors")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    sys.exit(main())
