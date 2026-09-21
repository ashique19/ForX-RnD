"""Watchlist alert strip: flip detection, STALE/MISSING, rate-limit, events."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from forex_lab.calendar import CalendarBundle, CalendarEvent
from forex_lab.clock import fmt_display
from forex_lab.freshness import VALIDITY_CLOSED, VALIDITY_MISSING, VALIDITY_OK, VALIDITY_STALE
from forex_lab.ui.alerts import (
    KIND_EVENT,
    KIND_FLIP,
    KIND_MISSING,
    KIND_STALE,
    AlertState,
    alerts_cfg,
    beep_wav,
    collect_event_alerts,
    collect_signal_alerts,
    dismiss_alert,
    filter_rate_limited,
    format_alert_time,
    ingest_watch,
    live_signal_class,
    load_state,
    process_watch,
    save_state,
    snapshot_from_row,
    visible_alerts,
)


def _row(pair="EURUSD", tf="1h", buy="BUY", validity=VALIDITY_OK, raw=None):
    return SimpleNamespace(
        pair=pair,
        timeframe=tf,
        buy_sell=buy,
        validity=validity,
        raw_signal=raw,
    )


def _cfg(tmp_path=None, **alert_over):
    alerts = {
        "enabled": True,
        "sound": False,
        "cooldown_s": 300,
        "max_visible": 6,
        "event_warning": True,
        "event_minutes": 60,
    }
    alerts.update(alert_over)
    out = {
        "ui": {"timezone": "Asia/Dhaka", "timezone_tag": "Asia/Dhaka"},
        "board": {"alerts": alerts},
        "calendar": {"before_minutes": 60, "during_minutes": 15},
    }
    if tmp_path is not None:
        out["board"]["alerts"]["persist_file"] = str(tmp_path / "alert_state.json")
    return out


NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


def test_sound_defaults_off():
    acfg = alerts_cfg({})
    assert acfg["sound"] is False
    assert acfg["enabled"] is True
    assert live_signal_class(_row(buy="—", raw="SELL")) == "SELL"
    assert live_signal_class(_row(buy="HOLD")) == "HOLD"
    assert live_signal_class(_row(buy="—", raw=None)) == ""


def test_first_snapshot_is_silent_then_flip_alerts():
    state = AlertState()
    state, fresh = ingest_watch([_row(buy="BUY")], now=NOW, state=state)
    assert fresh == []
    assert snapshot_from_row(_row(buy="BUY")).signal == "BUY"

    later = NOW + timedelta(minutes=1)
    state, fresh = ingest_watch([_row(buy="SELL")], now=later, state=state)
    assert len(fresh) == 1
    assert fresh[0].kind == KIND_FLIP
    assert fresh[0].from_value == "BUY"
    assert fresh[0].to_value == "SELL"
    assert "BUY → SELL" in fresh[0].message

    # Unchanged poll: no spam
    later2 = later + timedelta(minutes=1)
    state, fresh = ingest_watch([_row(buy="SELL")], now=later2, state=state)
    assert fresh == []

    later3 = later2 + timedelta(minutes=1)
    state, fresh = ingest_watch([_row(buy="HOLD")], now=later3, state=state)
    assert len(fresh) == 1
    assert fresh[0].from_value == "SELL"
    assert fresh[0].to_value == "HOLD"


def test_buy_hold_and_hold_buy_are_flips():
    prev = {snapshot_from_row(_row(buy="BUY")).key(): snapshot_from_row(_row(buy="BUY"))}
    alerts = collect_signal_alerts(prev, [snapshot_from_row(_row(buy="HOLD"))], now=NOW)
    assert [a.kind for a in alerts] == [KIND_FLIP]
    prev2 = {snapshot_from_row(_row(buy="HOLD")).key(): snapshot_from_row(_row(buy="HOLD"))}
    back = collect_signal_alerts(prev2, [snapshot_from_row(_row(buy="BUY"))], now=NOW)
    assert back[0].from_value == "HOLD"
    assert back[0].to_value == "BUY"


def test_stale_transition_alerts_once_closed_does_not():
    state = AlertState()
    state, _ = ingest_watch([_row(buy="BUY", validity=VALIDITY_OK)], now=NOW, state=state)

    t1 = NOW + timedelta(minutes=1)
    state, fresh = ingest_watch(
        [_row(buy="—", validity=VALIDITY_STALE, raw="BUY")],
        now=t1,
        state=state,
    )
    assert [a.kind for a in fresh] == [KIND_STALE]
    assert "STALE" in fresh[0].message
    # last model class is kept — not treated as BUY → HOLD
    assert all(a.kind != KIND_FLIP for a in fresh)

    t2 = t1 + timedelta(minutes=1)
    state, fresh = ingest_watch(
        [_row(buy="—", validity=VALIDITY_STALE, raw="BUY")],
        now=t2,
        state=state,
    )
    assert fresh == []

    # Weekend close is not a STALE panic
    t3 = t2 + timedelta(minutes=1)
    state, fresh = ingest_watch(
        [_row(buy="BUY", validity=VALIDITY_CLOSED)],
        now=t3,
        state=state,
    )
    assert all(a.kind != KIND_STALE for a in fresh)

    # Recovery then STALE again inside cooldown is rate-limited
    t4 = t3 + timedelta(minutes=1)
    state, _ = ingest_watch([_row(buy="BUY", validity=VALIDITY_OK)], now=t4, state=state)
    t5 = t4 + timedelta(seconds=30)
    state, fresh = ingest_watch(
        [_row(buy="—", validity=VALIDITY_STALE, raw="BUY")],
        now=t5,
        state=state,
        cfg=_cfg(cooldown_s=300),
    )
    assert fresh == []


def test_missing_alerts_and_ok_recovery_is_silent():
    state = AlertState()
    state, _ = ingest_watch([_row(buy="SELL", validity=VALIDITY_OK)], now=NOW, state=state)
    t1 = NOW + timedelta(minutes=2)
    state, fresh = ingest_watch(
        [_row(buy="—", validity=VALIDITY_MISSING, raw="SELL")],
        now=t1,
        state=state,
    )
    assert [a.kind for a in fresh] == [KIND_MISSING]
    t2 = t1 + timedelta(minutes=1)
    state, fresh = ingest_watch(
        [_row(buy="SELL", validity=VALIDITY_OK)],
        now=t2,
        state=state,
    )
    assert fresh == []


def test_rate_limit_same_flip_within_cooldown():
    cfg = _cfg(cooldown_s=300)
    state = AlertState()
    state, _ = ingest_watch([_row(buy="BUY")], now=NOW, state=state, cfg=cfg)
    t1 = NOW + timedelta(minutes=1)
    state, fresh = ingest_watch([_row(buy="SELL")], now=t1, state=state, cfg=cfg)
    assert len(fresh) == 1
    t2 = t1 + timedelta(minutes=1)
    state, fresh = ingest_watch([_row(buy="BUY")], now=t2, state=state, cfg=cfg)
    assert len(fresh) == 1  # different key SELL→BUY
    t3 = t2 + timedelta(seconds=30)
    state, fresh = ingest_watch([_row(buy="SELL")], now=t3, state=state, cfg=cfg)
    assert fresh == []  # BUY→SELL already fired this cooldown
    t4 = t3 + timedelta(seconds=301)
    state, fresh = ingest_watch([_row(buy="SELL")], now=t4, state=state, cfg=cfg)
    # still SELL — snapshot unchanged, no candidate even after cooldown
    assert fresh == []
    t5 = t4 + timedelta(seconds=1)
    state, _ = ingest_watch([_row(buy="BUY")], now=t5, state=state, cfg=cfg)
    t6 = t5 + timedelta(seconds=1)
    state, fresh = ingest_watch([_row(buy="SELL")], now=t6, state=state, cfg=cfg)
    assert len(fresh) == 1
    assert fresh[0].from_value == "BUY"


def test_filter_rate_limited_direct():
    from forex_lab.ui.alerts import Alert

    a = Alert(
        id="a1",
        kind=KIND_FLIP,
        pair="EURUSD",
        timeframe="1h",
        message="EURUSD BUY → SELL",
        created_at="2026-09-21T10:00:00Z",
        from_value="BUY",
        to_value="SELL",
    )
    fired = {a.rate_key(): NOW.timestamp()}
    kept = filter_rate_limited([a], fired, now_ts=NOW.timestamp() + 10, cooldown_s=300)
    assert kept == []
    kept2 = filter_rate_limited([a], fired, now_ts=NOW.timestamp() + 400, cooldown_s=300)
    assert kept2 == [a]


def test_event_within_60m_once_then_silent():
    nfp = CalendarEvent(
        title="Non-Farm Employment Change",
        currency="USD",
        when="2026-09-21T10:45:00Z",
        impact="High",
        highlight=True,
    )
    far = CalendarEvent(
        title="FOMC Statement",
        currency="USD",
        when="2026-09-21T18:00:00Z",
        impact="High",
        highlight=True,
    )
    bundle = CalendarBundle(events=[nfp, far])
    cfg = _cfg()
    state = AlertState()
    state, _ = ingest_watch([_row(buy="HOLD")], now=NOW, state=state, cfg=cfg)
    t1 = NOW + timedelta(seconds=5)
    state, fresh = ingest_watch(
        [_row(buy="HOLD")],
        calendar=bundle,
        now=t1,
        state=state,
        cfg=cfg,
    )
    events = [a for a in fresh if a.kind == KIND_EVENT]
    assert len(events) == 1
    assert "Non-Farm" in events[0].message
    assert "FOMC" not in events[0].message
    t2 = t1 + timedelta(minutes=2)
    state, fresh = ingest_watch(
        [_row(buy="HOLD")],
        calendar=bundle,
        now=t2,
        state=state,
        cfg=cfg,
    )
    assert [a for a in fresh if a.kind == KIND_EVENT] == []


def test_event_outside_window_and_unrelated_pair():
    nfp = CalendarEvent(
        title="Non-Farm Employment Change",
        currency="USD",
        when="2026-09-21T10:45:00Z",
        impact="High",
    )
    now = NOW
    none = collect_event_alerts([nfp], ["EURGBP"], now=now, minutes=60)
    assert none == []
    early = collect_event_alerts(
        [nfp],
        ["EURUSD"],
        now=datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc),
        minutes=60,
    )
    assert early == []


def test_persist_roundtrip_and_dismiss(tmp_path):
    cfg = _cfg(tmp_path)
    path = tmp_path / "alert_state.json"
    state = AlertState(sound=False)
    state, _ = ingest_watch([_row(buy="BUY")], now=NOW, state=state, cfg=cfg)
    t1 = NOW + timedelta(minutes=1)
    state, fresh = ingest_watch([_row(buy="SELL")], now=t1, state=state, cfg=cfg)
    assert fresh
    save_state(state, cfg, path)
    loaded = load_state(cfg, path)
    assert loaded.snapshots["EURUSD|1h"].signal == "SELL"
    assert loaded.alerts[0].kind == KIND_FLIP
    assert loaded.sound is False
    dismiss_alert(loaded, loaded.alerts[0].id)
    assert visible_alerts(loaded) == []
    save_state(loaded, cfg, path)

    # process_watch reloads disk — dismissed flip does not return on unchanged SELL
    again, fresh2 = process_watch([_row(buy="SELL")], cfg=cfg, now=t1 + timedelta(minutes=1), path=path)
    assert fresh2 == []
    assert visible_alerts(again) == []


def test_disabled_updates_snapshot_without_alerts():
    cfg = _cfg(enabled=False)
    state = AlertState()
    state, _ = ingest_watch([_row(buy="BUY")], now=NOW, state=state, cfg=cfg)
    t1 = NOW + timedelta(minutes=1)
    state, fresh = ingest_watch([_row(buy="SELL")], now=t1, state=state, cfg=cfg)
    assert fresh == []
    assert state.snapshots["EURUSD|1h"].signal == "SELL"


def test_alert_time_is_asia_dhaka():
    from forex_lab.ui.alerts import Alert

    alert = Alert(
        id="x",
        kind=KIND_FLIP,
        pair="EURUSD",
        timeframe="1h",
        message="EURUSD BUY → SELL",
        created_at="2026-09-21T10:00:00Z",
        from_value="BUY",
        to_value="SELL",
    )
    text = format_alert_time(alert, _cfg())
    assert "Asia/Dhaka" in text
    assert text.startswith("2026-09-21 16:00:00")
    assert fmt_display(alert.created_at, seconds=True).startswith("2026-09-21 16:00:00")


def test_beep_wav_is_riff_and_short():
    blob = beep_wav()
    assert blob[:4] == b"RIFF"
    assert b"WAVE" in blob[:16]
    assert 200 < len(blob) < 20_000
