"""Logical desk QA — contracts a trader can rely on, not pytest-for-its-own-sake.

STALE never paper-opens. Advice never auto-submits. MTF n/a fail-softs.
Clocks are Asia/Dhaka. Awareness STALE is not OK. Digest/retrain dry-run
do not call BrokerPort. Workspace apply does not wipe the paper journal.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
import yaml

from forex_lab.advise import suggest_actions
from forex_lab.broker import BrokerPort, PaperBroker
from forex_lab.calendar import CalendarEvent
from forex_lab.clock import fmt_display
from forex_lab.config_loader import load_config
from forex_lab.digest import build_digest, format_digest_text
from forex_lab.gates import evaluate_open_gates
from forex_lab.mtf import MTF_NA, assess_mtf
from forex_lab.retrain import champion_meta_path, load_champion, run_retrain_gate
from forex_lab.score import score_from_ohlcv
from forex_lab.ui.board import (
    BoardRow,
    paper_actions_label,
    paper_submit_allowed,
    paper_submit_block_reason,
    validity_token,
)
from forex_lab.ui.health import awareness_status_html, awareness_table_html, status_token
from forex_lab.ui.theme import BUY, WARN, WARN_BG, scan_strip_html
from forex_lab.ui.watchlist import WatchItem, Watchlist, load_watchlist, save_watchlist
from forex_lab.ui.workspace import apply_workspace, overlay_config, workspace_from_mapping, workspace_paths


def _imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _no_broker(path: Path) -> None:
    assert not any("broker" in n.split(".") for n in _imports_of(path))


def _ohlc(start: datetime, rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(rows), freq="h")
    data = [{"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1} for o, h, l, c in rows]
    df = pd.DataFrame(data, index=idx)
    df.index.name = "Datetime"
    return df


def _row(**over) -> BoardRow:
    row = BoardRow(
        pair="EURUSD",
        timeframe="1h",
        buy_sell="BUY",
        target="n/a",
        signal_details="conf=0.55",
        status="ready",
        raw_signal="BUY",
        confidence=0.55,
        validity="OK",
    )
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _fn_attrs(path: Path, name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    return [node.attr for node in ast.walk(fn) if isinstance(node, ast.Attribute)]


def test_stale_never_records_a_paper_fill(tmp_path):
    """UI contract: STALE/MISSING/ERROR cannot open. CLOSE is not this path."""
    store = tmp_path / "paper_broker.json"
    broker = PaperBroker(store, cfg={"broker": {"store": str(store), "default_size": 1}})
    stale = _row(validity="STALE", buy_sell="—")
    assert paper_submit_allowed(stale.validity, stale) is False
    assert "STALE" in paper_submit_block_reason(stale.validity, stale)
    assert paper_actions_label(stale).startswith("disabled")
    # Suffixed validity (Awareness-style) still blocks — never treat it as OK.
    assert paper_submit_allowed("STALE · data stale — refresh required", stale) is False
    assert validity_token("STALE · data stale — refresh required") == "STALE"
    # The desk returns before BrokerPort.submit.
    if paper_submit_allowed(stale.validity, stale):
        broker.submit("BUY", "EURUSD", price=1.10, validity="STALE")
    assert broker.list_positions() == []
    assert broker.list_fills() == []

    missing = _row(validity="MISSING", buy_sell="—")
    assert paper_submit_allowed(missing.validity, missing) is False
    error = _row(validity="ERROR", buy_sell="—")
    assert paper_submit_allowed(error.validity, error) is False

    ok = _row(validity="OK", buy_sell="BUY")
    assert paper_submit_allowed(ok.validity, ok) is True
    broker.submit("BUY", "EURUSD", price=1.10, validity="OK")
    assert len(broker.list_positions()) == 1
    assert broker.list_positions()[0]["validity_at_entry"] == "OK"


def test_paper_journal_scores_tp_right_and_sl_wrong(tmp_path):
    start = datetime(2026, 9, 21, 8, 0, 0)
    tp_bars = _ohlc(
        start,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1210, 1.0995, 1.1200),
        ],
    )
    store = tmp_path / "paper_broker.json"
    broker = PaperBroker(store, cfg={"horizon": 8, "broker": {"store": str(store)}})
    broker.submit(
        "BUY",
        "EURUSD",
        sl=1.0900,
        tp=1.1200,
        price=1.1000,
        horizon=8,
        entry_bar_time=str(tp_bars.index[0]),
    )
    events = broker.refresh_from_ohlcv("EURUSD", tp_bars, {"horizon": 8})
    assert events and events[0]["reason"] == "tp"
    closed = broker.list_closed()
    assert closed and closed[0]["outcome"] == "RIGHT"
    scored = score_from_ohlcv(closed[0], tp_bars, {"horizon": 8})
    assert scored.outcome == "RIGHT"

    sl_bars = _ohlc(
        start,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1010, 1.0890, 1.0900),
        ],
    )
    broker2 = PaperBroker(tmp_path / "paper2.json", cfg={"horizon": 8})
    broker2.submit(
        "BUY",
        "EURUSD",
        sl=1.0900,
        tp=1.1200,
        price=1.1000,
        horizon=8,
        entry_bar_time=str(sl_bars.index[0]),
    )
    broker2.refresh_from_ohlcv("EURUSD", sl_bars, {"horizon": 8})
    assert broker2.list_closed()[0]["outcome"] == "WRONG"


def test_calendar_advice_never_auto_submits_or_imports_broker():
    _no_broker(Path("forex_lab/advise.py"))
    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    event = CalendarEvent(
        title="Non-Farm Employment Change",
        currency="USD",
        when="2026-09-21T16:30:00Z",
        impact="High",
        highlight=True,
    )
    cards = suggest_actions(
        pair="EURUSD",
        signal="BUY",
        validity="OK",
        position=None,
        events=[event],
        cfg={"advice": {"enabled": True, "before_minutes": 60}},
        now=now,
    )
    assert cards
    assert all(c.auto_submit is False for c in cards)
    assert all(
        "never auto-submitted" in c.disclaimer.lower() or "not an order" in c.disclaimer.lower()
        for c in cards
    )
    stale_cards = suggest_actions(
        pair="EURUSD",
        signal="BUY",
        validity="STALE",
        position=None,
        events=[event],
        cfg={"advice": {"enabled": True}},
        now=now,
    )
    assert stale_cards and stale_cards[0].action == "none"
    assert stale_cards[0].auto_submit is False
    # Desk renderer must never call BrokerPort.submit / close from an advice card.
    attrs = _fn_attrs(Path("streamlit_app.py"), "_render_advice_card")
    assert "submit" not in attrs
    assert "close" not in attrs


def test_mtf_na_fail_soft_does_not_block_when_gates_on():
    cfg = {
        "gates": {
            "enabled": True,
            "require_mtf_agree": True,
            "no_new_opens_in_event_window": True,
            "fail_soft": True,
        }
    }
    d = evaluate_open_gates(
        pair="EURUSD",
        signal="BUY",
        confidence=0.7,
        mtf=None,
        events=None,
        cfg=cfg,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert d.allowed is True
    assert any("MTF n/a" in n for n in d.fail_soft_notes)
    na = assess_mtf(None, cfg, "BUY", validity="OK")
    assert na.status == MTF_NA
    stale_mtf = assess_mtf(pd.DataFrame(), cfg, "BUY", validity="STALE")
    assert stale_mtf.status == MTF_NA


def test_desk_clocks_are_asia_dhaka():
    cfg = load_config()
    assert (cfg.get("ui") or {}).get("timezone") == "Asia/Dhaka"
    utc = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    text = fmt_display(utc, cfg, seconds=True)
    assert text == "2026-09-21 16:00:00 Asia/Dhaka"
    payload = build_digest(cfg, health_rows=[], board_rows=[], alerts=[], closed_paper=[], open_paper=[])
    assert payload["timezone"] == "Asia/Dhaka"
    assert "Asia/Dhaka" in (payload.get("timezone_tag") or payload["timezone"])
    body = format_digest_text(payload)
    assert "Asia/Dhaka" in body


def test_awareness_stale_is_not_ok_green():
    rows = [
        {
            "Source": "EURUSD 1h OHLCV",
            "Observing": "price",
            "Cadence": "manual",
            "Last OK": "2026-09-21 15:00:00 Asia/Dhaka",
            "Status": "STALE · data stale — refresh required",
        },
        {
            "Source": "EURUSD news RSS",
            "Observing": "headlines",
            "Cadence": "rss",
            "Last OK": "2026-09-21 15:05:00 Asia/Dhaka",
            "Status": "OK",
        },
    ]
    assert status_token(rows[0]) == "STALE"
    assert status_token(rows[1]) == "OK"
    html = awareness_table_html(rows)
    # STALE cell uses warn amber, not BUY green.
    assert WARN_BG in html and WARN in html
    stale_bit = html.split("STALE")[0][-180:]
    assert BUY not in stale_bit or WARN_BG in stale_bit
    bar = awareness_status_html(rows)
    assert 'class="warn"' in bar
    assert "STALE" in bar
    assert "1 STALE" in bar
    assert 'class="ok"' not in bar


def test_scan_strip_does_not_label_missing_as_stale():
    html = scan_strip_html(
        session="LONDON",
        refreshed="2026-09-21 20:00:00",
        tz="Asia/Dhaka",
        counts={"BUY": 0, "SELL": 0, "HOLD": 0, "STALE": 1, "MISSING": 2, "ERROR": 0},
    )
    assert "Asia/Dhaka" in html
    compact = html.replace("\n", "")
    assert ">STALE <b>1</b>" in compact
    assert "MISSING" in html
    assert "fx-count missing" in html
    assert ">STALE <b>3</b>" not in compact
    assert "fx-count stale on\">MISSING" not in compact


def test_digest_and_retrain_dry_run_skip_brokerport(tmp_path):
    _no_broker(Path("forex_lab/digest.py"))
    _no_broker(Path("forex_lab/retrain.py"))
    cfg = {
        "ui": {"timezone": "Asia/Dhaka"},
        "retrain": {
            "store": str(tmp_path / "champion"),
            "train_on_promote": True,
            "fail_soft": True,
            "seed_from_metrics": True,
        },
        "model": {"type": "xgboost"},
        "paths": {"models_dir": str(tmp_path / "models"), "reports_dir": str(tmp_path / "reports")},
    }
    metrics = {
        "pair": "EURUSD",
        "model": {
            "n_trades": 10,
            "profit_factor": 0.978,
            "total_return": -0.02,
            "max_drawdown": -0.07,
            "win_rate": 0.5,
        },
    }
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "latest_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    preview = run_retrain_gate("EURUSD", cfg, dry_run=True, persist=True)
    assert preview["dry_run"] is True
    assert preview["champion_written"] is False
    assert preview["trained"] is False
    assert load_champion("EURUSD", cfg) is None
    assert not champion_meta_path("EURUSD", cfg).exists()
    models = tmp_path / "models"
    assert not list(models.glob("*.joblib")) if models.exists() else True
    assert sorted(BrokerPort.__abstractmethods__) == sorted(
        {"submit", "close", "list_positions", "list_fills"}
    )


def test_workspace_apply_does_not_wipe_paper_journal(tmp_path):
    paths = workspace_paths(builtin_dir=tmp_path / "builtin", user_dir=tmp_path / "user")
    paths.builtin_dir.mkdir()
    paths.user_dir.mkdir()
    (paths.builtin_dir / "scalp.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "scalp",
                "pairs": ["EURUSD"],
                "interval": "15m",
                "refresh_seconds": 60,
                "min_confidence": 0.45,
            }
        ),
        encoding="utf-8",
    )
    paper = tmp_path / "paper_broker.json"
    broker = PaperBroker(paper, cfg={"broker": {"store": str(paper)}})
    broker.submit("BUY", "GBPUSD", price=1.25, validity="OK", note="keep-me")
    snapshot = paper.read_text(encoding="utf-8")
    watch = tmp_path / "watchlist.yaml"
    save_watchlist(Watchlist(pairs=[WatchItem("GBPUSD")], refresh_seconds=90), watch)
    cfg = {
        "broker": {"backend": "paper", "store": str(paper)},
        "interval": "1h",
        "pairs": {"EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X"},
    }
    ws = workspace_from_mapping(
        {"name": "scalp", "pairs": ["EURUSD"], "interval": "15m", "refresh_seconds": 60}
    )
    apply_workspace(ws, watchlist_file=watch, paths=paths, cfg=cfg)
    assert paper.read_text(encoding="utf-8") == snapshot
    reloaded = PaperBroker(paper, cfg=cfg)
    assert reloaded.list_positions()[0]["pair"] == "GBPUSD"
    assert reloaded.list_positions()[0]["note"] == "keep-me"
    assert load_watchlist(watch).pair_symbols() == ["EURUSD"]
    over = overlay_config(cfg, ws)
    assert over["broker"]["store"] == str(paper)


def _fill_count(text: str | None) -> int:
    if not text:
        return 0
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return 0
    return len(data.get("fills") or []) if isinstance(data, dict) else 0


def test_streamlit_stale_row_disables_buy_sell_and_keeps_dhaka(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from forex_lab.calendar import CalendarBundle
    from forex_lab.freshness import VALIDITY_STALE
    from forex_lab.news import NewsBundle
    from forex_lab.paths import project_root
    from forex_lab.ui.board import build_board_row as real_build

    def _stub_news(pair, cfg=None, **_kwargs):
        return NewsBundle(pair=str(pair).upper(), bias="unclear", headlines=[], fetched_at="2026-09-21 00:00 UTC")

    def _stub_cal(cfg=None, **_kwargs):
        return CalendarBundle(events=[], source="fixture", fetched_at="2026-09-21 00:00 UTC")

    def _stale(*a, **k):
        row = real_build(*a, **k)
        row.validity = VALIDITY_STALE
        row.buy_sell = "—"
        return row

    monkeypatch.setattr("forex_lab.news.fetch_pair_news", _stub_news)
    monkeypatch.setattr("forex_lab.calendar.fetch_calendar", _stub_cal)
    monkeypatch.setattr("forex_lab.ui.board.build_board_row", _stale)

    paper = project_root() / "data" / "paper_broker.json"
    before = paper.read_text(encoding="utf-8") if paper.exists() else None
    at = AppTest.from_file(str(project_root() / "streamlit_app.py"), default_timeout=60)
    at.run()
    assert not at.exception, f"Streamlit render failed: {at.exception}"
    blobs = []
    for attr in ("markdown", "warning", "caption", "text"):
        block = getattr(at, attr, None)
        if block is None:
            continue
        for el in block:
            val = getattr(el, "value", None) or getattr(el, "body", None)
            if val:
                blobs.append(str(val))
    joined = "\n".join(blobs)
    assert "Asia/Dhaka" in joined
    assert "fx-valid-loud" in joined or "STALE" in joined
    assert "disabled" in joined.lower() and "stale" in joined.lower()
    buy_sell = [
        b
        for b in at.button
        if str(getattr(b, "label", "")).upper() in {"BUY", "SELL", "PAPER BUY", "PAPER SELL"}
    ]
    assert buy_sell
    flags = [getattr(b, "disabled", None) for b in buy_sell]
    if any(f is not None for f in flags):
        assert all(bool(f) for f in flags)
    # Disabled BUY/SELL must not be clickable (AppTestError). Do not click them here.
    after = paper.read_text(encoding="utf-8") if paper.exists() else None
    assert _fill_count(after) == _fill_count(before)
