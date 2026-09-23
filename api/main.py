"""ForX Decision API.

    python -m uvicorn api.main:app --host 127.0.0.1 --port 8000

Streamlit (`streamlit run streamlit_app.py`, port 8501) is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from forex_lab import __version__ as lab_version
from forex_lab.clock import fmt_display, timezone_name
from forex_lab.ui.watchlist import WatchlistError, normalize_pair

from api import __version__ as api_version
from forex_lab.broker import BrokerError

from api.consensus import ConsensusError, ensure_consensus, read_consensus
from api.deskdata import (
    app_config,
    assets_payload,
    board_payload,
    build_brief,
    calendar_payload,
    mutate_watchlist,
    ohlcv_payload,
    refresh_one,
    refresh_watchlist,
    run_pipeline_pair,
    watchlist_json,
)
from api.learnings import MAX_LIMIT, learnings_payload
from api.paperdesk import PaperBlocked, paper_order

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
            payload = refresh_watchlist(pairs)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(status_code=200, content=payload)

    @app.post("/refresh/{pair}")
    def post_refresh(pair: str, interval: str | None = Query(default=None)) -> JSONResponse:
        try:
            payload = refresh_one(pair, interval=interval)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if payload.get("rate_limited"):
            cached = payload.get("row")
            payload = {**payload, "board": {"rows": [cached], "from_cache": True}}
        return JSONResponse(status_code=200, content=payload)

    @app.post("/paper/order")
    def post_paper_order(body: PaperOrderBody) -> dict:
        try:
            return paper_order(body.pair, body.side, size=body.size, interval=body.interval)
        except PaperBlocked as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (BrokerError, WatchlistError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/pipeline/{pair}")
    def post_pipeline(pair: str, fetch: bool = Query(default=False)) -> dict:
        try:
            return run_pipeline_pair(pair, fetch=fetch)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()
