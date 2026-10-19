"""Refresh desk 1h data when the latest CLOSED hour bar is missing.

Monitor helper. Reads GET /board, and for every pair whose last 1h bar is
older than the latest closed hour (gap >= 1 closed hour) calls the desk
refresh endpoint POST /refresh (market data only; never /pipeline/* or
/model/train/*). No YAML changes. Prints JSON.

Partial-bar heal (2026-10-19): a pair is also refreshed when its CSV tip bar is
suspect even though no closed hour is missing: the tip bar's hour has not
closed yet (forming bar on disk), or the CSV was last written before the tip
bar's hour closed (bar saved mid-hour, e.g. one tick at hh:01). The refresh
itself re-fetches and overwrites the last 3 closed bars from MT5 and drops any
forming bar (forex_lab.data.try_mt5_refresh). ``--no-heal`` disables this.

Run:
  .venv\\Scripts\\python.exe scripts\\refresh_if_gap.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DHAKA = ZoneInfo("Asia/Dhaka")
ROOT = Path(__file__).resolve().parents[1]


def fx_open(now: datetime) -> bool:
    now = now.astimezone(timezone.utc)
    wd, h = now.weekday(), now.hour
    if wd == 5 or (wd == 4 and h >= 21) or (wd == 6 and h < 21):
        return False
    return True


def latest_closed_bar_open(now: datetime) -> datetime:
    """Open time (UTC) of the most recent fully closed 1h bar."""
    return now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)


def parse_bar(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).replace(" Asia/Dhaka", "").strip()
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=DHAKA)
    return t.astimezone(timezone.utc)


def gap_pairs(rows: list[dict], now: datetime) -> list[dict]:
    want = latest_closed_bar_open(now)
    out = []
    for r in rows:
        pair = str(r.get("pair") or "").upper()
        if not pair:
            continue
        last = parse_bar(r.get("last_bar_dhaka"))
        gap = None if last is None else int((want - last).total_seconds() // 3600)
        if last is None or gap >= 1:
            out.append({"pair": pair, "last_bar_utc": last.strftime("%Y-%m-%dT%H:%MZ") if last else None,
                        "closed_hours_missing": gap})
    return out


def csv_tip_state(path: Path) -> tuple[datetime | None, datetime | None]:
    """(last bar open UTC, file mtime UTC) of a cache CSV; (None, None) when unreadable."""
    try:
        st = path.stat()
        with path.open("rb") as fh:
            fh.seek(max(0, st.st_size - 4096))
            lines = [ln for ln in fh.read().decode("utf-8", "replace").splitlines() if ln.strip()]
    except OSError:
        return None, None
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
    if not lines:
        return None, mtime
    first = lines[-1].split(",", 1)[0].strip()
    try:
        last = datetime.fromisoformat(first)
    except ValueError:
        return None, mtime
    last = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last.astimezone(timezone.utc)
    return last, mtime


def partial_suspects(states: dict[str, tuple[datetime | None, datetime | None]], now: datetime) -> list[dict]:
    """Pairs whose tip bar is forming or was written before its hour closed."""
    now = now.astimezone(timezone.utc)
    out = []
    for pair, (last, mtime) in sorted(states.items()):
        if last is None:
            continue
        close = last + timedelta(hours=1)
        reason = None
        if close > now:
            reason = "forming_bar"
        elif mtime is not None and mtime < close:
            reason = "written_before_close"
        if reason:
            out.append({"pair": pair, "tip_bar_utc": last.strftime("%Y-%m-%dT%H:%MZ"), "reason": reason,
                        "csv_written_utc": mtime.strftime("%Y-%m-%dT%H:%M:%SZ") if mtime else None})
    return out


def _http(base: str, method: str, path: str, body: dict | None = None):
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    data = json.dumps(body).encode() if body is not None else None
    req = Request(base.rstrip("/") + path, data=data, method=method,
                  headers={"Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=300) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        return int(exc.code), None
    except (OSError, URLError, ValueError) as exc:
        return 0, {"error": str(exc)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-heal", action="store_true", help="only refresh missing closed hours")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    res: dict = {"now_dhaka": now.astimezone(DHAKA).strftime("%Y-%m-%d %H:%M Asia/Dhaka"),
                 "latest_closed_bar_utc": latest_closed_bar_open(now).strftime("%Y-%m-%dT%H:%MZ")}
    if not fx_open(now):
        res.update({"ok": True, "fx_closed": True, "refreshed": []})
        print(json.dumps(res, indent=2)); return 0
    st, board = _http(a.base_url, "GET", "/board")
    if st != 200 or not isinstance(board, dict):
        res.update({"ok": False, "reason": "board fetch failed", "status": st})
        print(json.dumps(res, indent=2)); return 2
    rows = [r for r in board.get("rows") or [] if isinstance(r, dict)]
    gaps = gap_pairs(rows, now)
    res["gaps"] = gaps
    heal: list[dict] = []
    if not a.no_heal:
        have = {g["pair"] for g in gaps}
        states = {}
        for r in rows:
            pair = str(r.get("pair") or "").upper()
            if pair and pair not in have:
                states[pair] = csv_tip_state(Path(a.data_dir) / f"{pair}_1h.csv")
        heal = partial_suspects(states, now)
    res["heal"] = heal
    todo = [g["pair"] for g in gaps] + [h["pair"] for h in heal]
    if todo and not a.dry_run:
        st, payload = _http(a.base_url, "POST", "/refresh", {"pairs": [{"pair": p, "interval": "1h"} for p in todo]})
        res["refresh_status"] = st
        res["refresh_ok"] = st == 200
    res["ok"] = True
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
