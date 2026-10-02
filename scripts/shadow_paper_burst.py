"""Burst eligible live-desk paper calls into the paper+shadow path.

This script is intentionally API-only: it never appends journal rows itself,
never changes YAML/config, and never bypasses the desk's gates or risk checks.
Run it on a weekday while the desk API is up.  The endpoint remains the source
of truth for one_position, weekday/news gates, stops/targets, and risk freezes.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "data" / "paper_shadow" / "journal.jsonl"
DHAKA = ZoneInfo("Asia/Dhaka")
DEFAULT_MIN_CONF = 0.60


def http_json(base: str, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(
        base.rstrip("/") + path,
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
            try:
                return int(response.status), json.loads(raw)
            except json.JSONDecodeError:
                return int(response.status), {"raw": raw[:500]}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail: Any = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw[:500]
        return int(exc.code), detail
    except (OSError, URLError) as exc:
        return 0, {"error": str(exc)}


def journal_count() -> int:
    if not JOURNAL.exists():
        return 0
    with JOURNAL.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def eligible(row: dict[str, Any], min_conf: float) -> tuple[bool, str]:
    pair = str(row.get("pair") or "?")
    signal = str(row.get("signal") or "").upper()
    if signal not in {"BUY", "SELL"}:
        return False, "not_directional"
    try:
        conf = float(row.get("confidence"))
    except (TypeError, ValueError):
        return False, "missing_confidence"
    if conf < min_conf:
        return False, f"below_min_conf:{conf:.4f}<{min_conf:.4f}"
    if bool(row.get("muted")):
        return False, "muted"
    if str(row.get("validity") or "").upper() != "OK":
        return False, f"validity:{row.get('validity')}"
    if str(row.get("status") or "").lower() not in {"ready", ""}:
        return False, f"status:{row.get('status')}"
    if str(row.get("gate_reason") or "").strip():
        return False, f"gate:{row.get('gate_reason')}"
    return True, f"eligible:{pair}:{signal}:{conf:.4f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Open eligible board calls through live paper desk endpoints")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONF)
    parser.add_argument("--max-opens", type=int, default=20)
    parser.add_argument("--skip-auto-sync", action="store_true", help="Do not tick the existing auto paper path first")
    args = parser.parse_args()
    if args.min_confidence < 0 or args.min_confidence > 1:
        parser.error("--min-confidence must be between 0 and 1")
    if args.max_opens < 1:
        parser.error("--max-opens must be positive")

    now_dhaka = datetime.now(DHAKA)
    result: dict[str, Any] = {
        "generated_at_dhaka": now_dhaka.strftime("%Y-%m-%d %H:%M:%S Asia/Dhaka"),
        "base_url": args.base_url.rstrip("/"),
        "min_confidence": args.min_confidence,
        "max_opens": args.max_opens,
        "journal_path": str(JOURNAL),
        "journal_before": journal_count(),
        "auto_sync": None,
        "candidates": [],
        "attempts": [],
        "fills_added": 0,
    }
    # Dhaka Saturday/Sunday is a hard stop. No endpoint calls and no synthetic rows.
    if now_dhaka.weekday() >= 5:
        result.update({"ok": True, "market_closed": True, "reason": "weekend Asia/Dhaka; no paper calls attempted"})
        result["journal_after"] = journal_count()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    status, health = http_json(args.base_url, "GET", "/health")
    if status != 200 or not isinstance(health, dict) or health.get("ok") is not True:
        result.update({"ok": False, "market_closed": False, "reason": "desk API health check failed", "health_status": status, "health": health})
        result["journal_after"] = journal_count()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 2

    if not args.skip_auto_sync:
        sync_status, sync = http_json(args.base_url, "GET", "/portfolio?sync=true")
        result["auto_sync"] = {"status": sync_status, "response": sync}

    board_status, board = http_json(args.base_url, "GET", "/board")
    if board_status != 200 or not isinstance(board, dict):
        result.update({"ok": False, "market_closed": False, "reason": "board fetch failed", "board_status": board_status, "board": board})
        result["journal_after"] = journal_count()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 3

    rows = board.get("rows") if isinstance(board.get("rows"), list) else []
    candidates: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        ok, reason = eligible(raw, args.min_confidence)
        item = {"pair": raw.get("pair"), "signal": raw.get("signal"), "confidence": raw.get("confidence"), "eligible": ok, "reason": reason}
        result["candidates"].append(item)
        if ok:
            candidates.append(raw)

    port_status, portfolio = http_json(args.base_url, "GET", "/portfolio?sync=false")
    open_pairs = set()
    if port_status == 200 and isinstance(portfolio, dict):
        for pos in portfolio.get("open") or []:
            if isinstance(pos, dict) and pos.get("pair"):
                open_pairs.add(str(pos["pair"]).upper())

    for row in candidates:
        if len(result["attempts"]) >= args.max_opens:
            break
        pair = str(row.get("pair") or "").upper()
        side = str(row.get("signal") or "").upper()
        if pair in open_pairs:
            result["attempts"].append({"pair": pair, "side": side, "status": "skipped", "reason": "one_position_already_open"})
            continue
        order_status, order = http_json(args.base_url, "POST", "/paper/order", {"pair": pair, "side": side, "interval": row.get("interval") or "1h"})
        attempt = {"pair": pair, "side": side, "status_code": order_status, "response": order}
        if order_status == 200 and isinstance(order, dict) and order.get("ok") is True:
            attempt["status"] = "filled_paper"
            open_pairs.add(pair)
            result["fills_added"] += 1
        else:
            attempt["status"] = "not_filled"
        result["attempts"].append(attempt)

    result["journal_after"] = journal_count()
    result["journal_delta"] = result["journal_after"] - result["journal_before"]
    result["ok"] = True
    result["market_closed"] = False
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
