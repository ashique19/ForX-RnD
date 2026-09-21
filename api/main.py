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
from api.consensus import ConsensusError, read_consensus
from api.deskdata import (
    app_config,
    board_payload,
    build_brief,
    mutate_watchlist,
    ohlcv_payload,
    refresh_pair,
    run_pipeline_pair,
    watchlist_json,
)
from api.limiter import allow

_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
]


class AddPairBody(BaseModel):
    pair: str = Field(..., min_length=1)
    interval: str | None = None


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

    @app.get("/brief/{pair}")
    def get_brief(pair: str, tf: str | None = Query(default=None)) -> dict:
        try:
            return build_brief(pair, tf)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/consensus/{pair}")
    def get_consensus(pair: str, horizon: str = Query(default="hourly")) -> dict:
        try:
            return read_consensus(normalize_pair(pair), horizon, app_config())
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

    @app.post("/refresh/{pair}")
    def post_refresh(pair: str, interval: str | None = Query(default=None)) -> JSONResponse:
        allowed, retry = allow(f"{pair}:{interval or ''}")
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "ok": False,
                    "error": "rate_limited",
                    "retry_after_s": round(retry, 1),
                    "detail": "Refresh is rate-limited. Cached bars stay in place; nothing was invented.",
                },
            )
        try:
            payload = refresh_pair(pair, interval=interval)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(status_code=200, content=payload)

    @app.post("/pipeline/{pair}")
    def post_pipeline(pair: str, fetch: bool = Query(default=False)) -> dict:
        try:
            return run_pipeline_pair(pair, fetch=fetch)
        except WatchlistError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()
