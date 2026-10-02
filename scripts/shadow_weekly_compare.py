"""Weekly shadow expectancy stub vs Replay pin (Stage-2).

Reads data/paper_shadow/journal.jsonl open/close pairs and prints a tiny
expectancy memo. Replay-pin diff is intentionally stubbed until N weeks of
journaled fills exist - do not invent a pin comparison.

Run:
  .venv\\Scripts\\python.exe scripts\\shadow_weekly_compare.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.paper_shadow import journal_path

DHAKA = ZoneInfo("Asia/Dhaka")
WINDOW_DAYS = 7


def _load_events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _parse_utc(raw: object) -> datetime | None:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def main() -> None:
    path = journal_path()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WINDOW_DAYS)
    now_dhaka = now.astimezone(DHAKA).strftime("%Y-%m-%d %H:%M Asia/Dhaka")
    events = _load_events(path)
    week = []
    for e in events:
        ts = _parse_utc(e.get("ts_utc"))
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            week.append(e)

    opens = [e for e in week if e.get("event") == "open"]
    closes = [e for e in week if e.get("event") == "close"]
    desk_calls = [e for e in week if e.get("event") == "desk_call"]

    by_pair: dict[str, dict[str, int]] = defaultdict(lambda: {"open": 0, "close": 0})
    for e in opens:
        by_pair[str(e.get("pair") or "?")]["open"] += 1
    for e in closes:
        by_pair[str(e.get("pair") or "?")]["close"] += 1

    lines = [
        f"# Shadow weekly compare stub - {now_dhaka}",
        f"Journal: `{path}`",
        f"Window: last {WINDOW_DAYS}d",
        "",
        f"- opens={len(opens)} closes={len(closes)} desk_calls={len(desk_calls)}",
        f"- missing timestamps dropped upstream (parse failures ignored)",
        "",
        "## By pair (shadow counts only)",
    ]
    if not by_pair:
        lines.append("(no shadow fills in window yet)")
    else:
        lines.append("| pair | opens | closes |")
        lines.append("|---|---:|---:|")
        for pair in sorted(by_pair):
            c = by_pair[pair]
            lines.append(f"| {pair} | {c['open']} | {c['close']} |")

    lines += [
        "",
        "## Shadow expectancy",
        "(stub) close events do not yet carry realized R on the JSONL row;",
        "join to data/paper_broker.json closed[] by position_id when computing PF.",
        "",
        "## vs Replay pin",
        "(stub) intentionally empty - wire when Stage-2 has N>=4 weeks of",
        "timestamped fills. Do not invent a pin delta.",
        "",
        "Standing: desk remains a decision aid until Stages 2-4 clear.",
    ]
    text = "\n".join(lines) + "\n"
    out = Path("_SHADOW_WEEKLY_COMPARE_STUB.txt")
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
