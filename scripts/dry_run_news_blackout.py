"""Dry-run news blackout against desk calendar cache + synthetic USD/EUR events.

Does NOT start a Replay job. Prints coverage honesty and blackout bar counts.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.calendar import CalendarEvent, cache_path
from forex_lab.config_loader import load_config
from forex_lab.news_blackout import (
    apply_replay_news_blackout,
    build_blackout_mask,
    events_cover_range,
    events_span,
    filter_events_for_blackout,
    is_in_news_blackout,
    load_calendar_cache_events,
    news_blackout_minutes,
    news_blackout_report_line,
)


def _dhaka(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Dhaka")
    ).strftime("%Y-%m-%d %H:%M Asia/Dhaka")


def main() -> int:
    cfg = load_config()
    # Ensure knob present even if yaml not yet patched in odd checkouts
    rc = dict(cfg.get("replay") or {})
    nb = dict(rc.get("news_blackout") or {})
    nb.setdefault("enabled", True)
    nb.setdefault("before_minutes", 60)
    nb.setdefault("during_minutes", 15)
    nb.setdefault("after_minutes", 30)
    nb.setdefault("require_historic_coverage", True)
    rc["news_blackout"] = nb
    cfg["replay"] = rc

    before, during, after = news_blackout_minutes(cfg)
    print("=== SERIAL step 6 dry-run: news blackout ===")
    print(f"now: {_dhaka(datetime.now(timezone.utc))}")
    print(f"minutes: before={before} during={during} after={after}")
    print(f"cache: {cache_path(cfg)}")

    cached = load_calendar_cache_events(cfg)
    lo, hi = events_span(cached)
    print(f"cache events: {len(cached)} span={lo} → {hi}")
    for e in cached:
        print(f"  - {e.when} {e.currency} {e.impact} {e.title[:50]}")

    # Short / full Coverage check (honest: week dump fails)
    short_start = datetime(2024, 9, 24, tzinfo=timezone.utc)
    short_end = datetime(2026, 9, 25, tzinfo=timezone.utc)
    full_start = datetime(2015, 1, 1, tzinfo=timezone.utc)
    print(
        f"covers short 2024-09-24→tip? {events_cover_range(cached, short_start, short_end)}"
    )
    print(
        f"covers full 2015→tip? {events_cover_range(cached, full_start, short_end)}"
    )

    # Resolve as Replay would for a short-window index
    import pandas as pd

    idx_short = pd.date_range(short_start, periods=24, freq="h", tz="UTC")
    cfg_short = json.loads(json.dumps({k: v for k, v in cfg.items() if not str(k).startswith("_")}, default=str))
    # reload properly
    cfg2 = load_config()
    cfg2.setdefault("replay", {})["news_blackout"] = dict(nb)
    apply_replay_news_blackout(cfg2, pair="EURUSD", index=idx_short)
    print(news_blackout_report_line(cfg2))

    # EURUSD pair filter on real cache (often zero USD/EUR this week)
    eurusd = filter_events_for_blackout(cached, pair="EURUSD", cfg=cfg2)
    print(f"EURUSD-affecting high-impact in cache: {len(eurusd)}")

    # Synthetic proof that the mask works when events exist
    nfp_when = datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc)
    synth = [
        CalendarEvent(
            title="Non-Farm Employment Change",
            currency="USD",
            when=nfp_when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            impact="High",
            highlight=True,
        ),
        CalendarEvent(
            title="CPI Flash Estimate y/y",
            currency="EUR",
            when=(nfp_when - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            impact="High",
            highlight=True,
        ),
    ]
    idx = pd.date_range("2026-09-25 08:00", periods=12, freq="h", tz="UTC")
    mask = build_blackout_mask(idx, synth, pair="EURUSD", cfg=cfg2)
    print(f"synthetic EURUSD blackout bars: {int(mask.sum())}/{len(mask)}")
    for ts, flag in zip(idx, mask):
        blocked, ev, win = is_in_news_blackout(ts, synth, pair="EURUSD", cfg=cfg2)
        mark = "BLOCK" if flag else "allow"
        title = (ev.title[:28] if ev else "-")
        print(
            f"  {ts.strftime('%Y-%m-%d %H:%M')}Z  ({_dhaka(ts.to_pydatetime())})  {mark:5}  {win:6}  {title}"
        )

    print("=== done (no Replay job started) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
