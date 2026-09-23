"""ForX Decision API.

    python -m uvicorn api.main:app --host 127.0.0.1 --port 8000

Streamlit (`streamlit run streamlit_app.py`, port 8501) is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from forex_lab import __version__ as lab_version
from forex_lab.clock import fmt_display, timezone_name
from forex_lab.ui.watchlist import WatchlistError, normalize_pair

from api import __version__ as api_version
from forex_lab.broker import MAX_OPENS_PER_HOUR, BrokerError

from api.consensus import ConsensusError, ensure_consensus, read_consensus
from api.deskdata import (
    app_config,
    assets_payload,
    board_payload,
    build_brief,
    calendar_payload,
    mutate_watchlist,
    ohlcv_payload,
    refresh_active_pair,
    refresh_watchlist,
    run_pipeline_pair,
    watchlist_json,
)
from api.learnings import MAX_LIMIT, learnings_payload
from api.paperdesk import PaperBlocked, paper_order, portfolio_payload, set_auto_settings
from api.replayjob import ReplayBusy, ReplayJobError, get_job, job_file, start_pull, start_replay
from forex_lab.ui.model_build import model_build_status
from forex_lab.ui.pipeline import run_retrain

_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
]


class AddPairBody(BaseModel):
    pair: str = Field(..., min_length=1)
    interval: str | None = None


class PaperOrderBody(BaseModel):
    pair: str = Field(..., min_length=1)
    side: str = Field(..., min_length=1)
    size: float | None = None
    interval: str | None = None


class RefreshTarget(BaseModel):
    pair: str = Field(..., min_length=1)
    interval: str | None = None


class RefreshBody(BaseModel):
    pairs: list[RefreshTarget] | None = None
    active: str | None = None


def _one_active_pair(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        raise ValueError("Historic pull and replay take one Active pair, not the watchlist.")
    return str(value)


class HistoryPullBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pair: str = Field(..., min_length=1)
    interval: str | None = "1h"
    start: str | None = "2015-01-01"
    end: str | None = None
    source: str | None = None

    @field_validator("pair", mode="before")
    @classmethod
    def single_pair(cls, value: object) -> str:
        return _one_active_pair(value)


class ReplayTrainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pair: str = Field(..., min_length=1)
    interval: str | None = None
    start: str | None = "2015-01-01"
    end: str | None = None
    pull: bool = True

    @field_validator("pair", mode="before")
    @classmethod
    def single_pair(cls, value: object) -> str:
        return _one_active_pair(value)


class PaperAutoBody(BaseModel):
    enabled: bool | None = None
    max_opens_per_hour: int | None = Field(default=None, ge=0, le=MAX_OPENS_PER_HOUR)


def create_app() -> FastAPI:
    app = FastAPI(
        title="ForX Decision API",
        version=api_version,
        description="JSON for the Decision desk. Research only — no live orders.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        cfg = app_config()
        return {
            "ok": True,
            "service": "forx-decision-api",
            "api_version": api_version,
            "forex_lab": lab_version,
            "timezone": timezone_name(cfg),
            "now_dhaka": fmt_display(datetime.now(timezone.utc), cfg, seconds=True),
            "streamlit": "Lab remains on http://127.0.0.1:8501 until cutover",
        }

    @app.get("/assets")
    def get_assets() -> dict:
        return assets_payload()

    @app.get("/watchlist")
    def get_watchlist() -> dict:
        return watchlist_json()

    @app.post("/watchlist")
    def post_watchlist(body: AddPairBody) -> dict:
        try:
            return mutate_watchlist(body.pair, interval=body.interval, remove=False)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/watchlist/{pair}")
    def delete_watchlist(pair: str) -> dict:
        try:
            return mutate_watchlist(pair, remove=True)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/board")
    def get_board() -> dict:
        return board_payload()

    @app.get("/calendar")
    def get_calendar(
        pairs: str | None = Query(default=None),
        force: bool = Query(default=False),
    ) -> dict:
        """Weekly high-impact calendar. Cached; ``force`` retries the live feed."""
        wanted: list[str] | None = None
        if pairs:
            wanted = []
            for token in pairs.split(","):
                text = token.strip()
                if not text:
                    continue
                try:
                    symbol = normalize_pair(text)
                except WatchlistError:
                    continue
                if symbol not in wanted:
                    wanted.append(symbol)
        return calendar_payload(pairs=wanted, force=force)

    @app.get("/learnings")
    def get_learnings(limit: int = Query(default=50, ge=1, le=MAX_LIMIT)) -> dict:
        return learnings_payload(limit=limit)

    @app.get("/model/status/{pair}")
    def get_model_status(pair: str) -> dict:
        """Core AI joblib status for one pair. Does not train or retrain."""
        try:
            return model_build_status(pair)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/model/retrain/{pair}")
    def post_model_retrain(pair: str, dry_run: bool = Query(default=False)) -> dict:
        """Explicit champion/challenger gate. Not called on a timer.

        Walk-forward can take several minutes. Research only — not a live edge.
        """
        try:
            symbol = normalize_pair(pair)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        cfg = app_config()
        rc, log = run_retrain(symbol, dry_run=dry_run, cfg=cfg)
        text = str(log or "")
        if len(text) > 4000:
            text = text[-4000:]
        return {
            "ok": rc == 0,
            "pair": symbol,
            "dry_run": bool(dry_run),
            "log": text,
            "model_build": model_build_status(symbol, cfg),
        }

    @app.get("/brief/{pair}")
    def get_brief(pair: str, tf: str | None = Query(default=None)) -> dict:
        try:
            return build_brief(pair, tf)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/consensus/{pair}")
    def get_consensus(pair: str, horizon: str = Query(default="hourly")) -> dict:
        try:
            symbol = normalize_pair(pair)
            cfg = app_config()
            ensure_consensus(symbol, cfg)
            return read_consensus(symbol, horizon, cfg)
        except (ConsensusError, WatchlistError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/ohlcv/{pair}")
    def get_ohlcv(
        pair: str,
        interval: str | None = Query(default=None),
        bars: int = Query(default=180, ge=20, le=500),
    ) -> dict:
        try:
            return ohlcv_payload(pair, interval=interval, bars=bars)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/refresh")
    def post_refresh_watchlist(body: RefreshBody | None = None) -> JSONResponse:
        """Watchlist market data. Does not run train / backtest / signals."""
        try:
            pairs = None if body is None or body.pairs is None else [(item.pair, item.interval) for item in body.pairs]
            active = None if body is None else body.active
            payload = refresh_watchlist(pairs, active=active)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(status_code=200, content=payload)

    @app.post("/refresh/{pair}")
    def post_refresh(pair: str, interval: str | None = Query(default=None)) -> JSONResponse:
        try:
            payload = refresh_active_pair(pair, interval=interval)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if payload.get("rate_limited"):
            cached = payload.get("row")
            payload = {**payload, "board": {"rows": [cached], "from_cache": True}}
        return JSONResponse(status_code=200, content=payload)

    @app.get("/portfolio")
    def get_portfolio(sync: bool = Query(default=True)) -> dict:
        return portfolio_payload(sync=sync)

    @app.post("/portfolio/auto")
    def post_portfolio_auto(body: PaperAutoBody) -> dict:
        if body.enabled is None and body.max_opens_per_hour is None:
            raise HTTPException(status_code=400, detail="enabled or max_opens_per_hour is required")
        set_auto_settings(enabled=body.enabled, max_opens_per_hour=body.max_opens_per_hour)
        # Pausing freezes the book. A cap-only change still runs the auto step.
        return portfolio_payload(sync=body.enabled is not False)

    @app.post("/paper/order")
    def post_paper_order(body: PaperOrderBody) -> dict:
        try:
            return paper_order(body.pair, body.side, size=body.size, interval=body.interval)
        except PaperBlocked as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (BrokerError, WatchlistError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/history/pull")
    def post_history_pull(body: HistoryPullBody) -> dict:
        """Download Dukascopy (HistData fallback) OHLC into data/history. No synthetic prices."""
        try:
            return start_pull(
                app_config(),
                pair=body.pair,
                interval=body.interval,
                start=body.start,
                end=body.end,
                source=body.source,
            )
        except ReplayBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ReplayJobError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/history/pull/{job_id}")
    def get_history_pull(job_id: str) -> dict:
        return _job_or_404(job_id)

    @app.post("/replay/train")
    def post_replay_train(body: ReplayTrainBody) -> dict:
        """Walk-forward replay for the one Active pair. Paper books only; live journal untouched."""
        try:
            return start_replay(
                app_config(),
                pair=body.pair,
                interval=body.interval,
                start=body.start,
                end=body.end,
                pull=body.pull,
            )
        except ReplayBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ReplayJobError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/replay/jobs/{job_id}")
    def get_replay_job(job_id: str) -> dict:
        return _job_or_404(job_id)

    @app.get("/replay/jobs/{job_id}/scoreboard")
    def get_replay_scoreboard(job_id: str, format: str = Query(default="csv")) -> FileResponse:
        kind = str(format or "csv").lower()
        name = "scoreboard.xlsx" if kind in {"xlsx", "excel"} else "scoreboard.csv"
        media = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if name.endswith("xlsx")
            else "text/csv"
        )
        return _job_download(job_id, name, media)

    @app.get("/replay/jobs/{job_id}/equity")
    def get_replay_equity(job_id: str) -> FileResponse:
        return _job_download(job_id, "equity.png", "image/png")

    @app.get("/replay/jobs/{job_id}/report")
    def get_replay_report(job_id: str) -> FileResponse:
        return _job_download(job_id, "report.md", "text/markdown")

    @app.post("/pipeline/{pair}")
    def post_pipeline(pair: str, fetch: bool = Query(default=False)) -> dict:
        try:
            return run_pipeline_pair(pair, fetch=fetch)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


def _job_or_404(job_id: str) -> dict:
    try:
        return get_job(job_id, app_config())
    except ReplayJobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _job_download(job_id: str, name: str, media: str) -> FileResponse:
    try:
        path = job_file(job_id, name, app_config())
    except ReplayJobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type=media, filename=path.name)


app = create_app()
