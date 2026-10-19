"""Fetch the free Forex Factory weekly calendar JSON into data/calendar/ (gitignored). Observe-only.

Tolerates the PC being offline: on any network/parse error it keeps the existing
cache, prints {"ok": false, ...} and exits 0. Never invents or edits events.
Run (scheduled task \\ForX-NewsCalendar, or by hand):
  .venv\\Scripts\\python.exe scripts\\fetch_news_calendar.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab import news_tag as nt  # noqa: E402


def fetch(url: str = nt.FF_URL, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ForX-research/1.0 (observe-only calendar cache)",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 -- fixed public https URL
        return r.read().decode("utf-8", "replace")


def main(fetcher=fetch) -> int:
    now = datetime.now(timezone.utc)
    log = nt.calendar_dir() / "fetch.log"
    try:
        raw = fetcher()
        path = nt.save_snapshot(raw, fetched_at=now)
        data = json.loads(raw)
        high = sum(1 for e in data if isinstance(e, dict) and str(e.get("impact", "")).lower() == "high")
        out = {"ok": True, "file": str(path), "events": len(data), "high_impact": high}
    except Exception as exc:  # noqa: BLE001 -- offline / rate-limited / bad payload: keep old cache
        out = {"ok": False, "offline_or_error": f"{type(exc).__name__}: {exc}", "kept_cache": True}
    out["fetched_at_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(out) + "\n")
    except OSError:
        pass
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
