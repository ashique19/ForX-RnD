"""Read-only MetaTrader 5 feed (OANDA demo). No order functions are imported or exposed.

Credentials come from the gitignored ``.env.mt5`` (MT5_LOGIN / MT5_PASSWORD / MT5_SERVER / MT5_PATH)
or the same names in the environment. They are never logged or printed.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.mt5"
PAIRS = ["AUDUSD", "EURGBP", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"]


class MT5Error(RuntimeError):
    pass


def _creds() -> dict[str, str]:
    vals: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_PATH"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    missing = [k for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER") if not vals.get(k)]
    if missing:
        raise MT5Error(f"missing MT5 settings: {missing}")
    return vals


def _mt5():
    import MetaTrader5 as mt5  # noqa: N813
    return mt5


def connect(require_demo: bool = True) -> dict[str, Any]:
    """Initialize + log in. Refuses (and shuts down) if the account is not DEMO when require_demo."""
    mt5 = _mt5(); c = _creds()
    kw = {"login": int(c["MT5_LOGIN"]), "password": c["MT5_PASSWORD"], "server": c["MT5_SERVER"], "timeout": 60000}
    ok = mt5.initialize(c["MT5_PATH"], **kw) if c.get("MT5_PATH") else mt5.initialize(**kw)
    if not ok:
        err = mt5.last_error(); mt5.shutdown()
        raise MT5Error(f"MT5 initialize/login failed: {err}")
    acc = mt5.account_info()
    if acc is None:
        mt5.shutdown(); raise MT5Error(f"account_info unavailable: {mt5.last_error()}")
    is_demo = acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    if require_demo and not is_demo:
        mt5.shutdown(); raise MT5Error(f"refusing: account trade_mode={acc.trade_mode} is not DEMO")
    return {"is_demo": is_demo, "trade_mode": int(acc.trade_mode), "server": acc.server, "currency": acc.currency,
            "balance": float(acc.balance), "equity": float(acc.equity), "leverage": int(acc.leverage)}


def shutdown() -> None:
    _mt5().shutdown()


def resolve_symbols(pairs: list[str] | None = None) -> dict[str, str]:
    """Map pair -> broker symbol (OANDA may add suffixes like '.sml' / '.pro')."""
    mt5 = _mt5(); out: dict[str, str] = {}
    allsym = mt5.symbols_get() or ()
    names = [s.name for s in allsym]
    for p in pairs or PAIRS:
        if p in names:
            out[p] = p; continue
        cand = sorted([n for n in names if n.replace("/", "").upper().startswith(p)], key=len)
        if cand:
            out[p] = cand[0]
    for sym in out.values():
        mt5.symbol_select(sym, True)
    import time
    for _ in range(20):  # wait for first ticks after adding to Market Watch
        if all((t := mt5.symbol_info_tick(s)) is not None and t.bid > 0 for s in out.values()):
            break
        time.sleep(0.5)
    return out


def quotes(symbols: dict[str, str]) -> pd.DataFrame:
    mt5 = _mt5(); rows = []
    for p, sym in symbols.items():
        info = mt5.symbol_info(sym); tick = mt5.symbol_info_tick(sym)
        if info is None or tick is None:
            rows.append({"pair": p, "symbol": sym, "ok": False}); continue
        pip = 0.01 if p.endswith("JPY") else 0.0001
        rows.append({"pair": p, "symbol": sym, "ok": True, "bid": tick.bid, "ask": tick.ask,
                     "spread_pips": round((tick.ask - tick.bid) / pip, 2), "digits": info.digits, "point": info.point,
                     "tick_time_server": datetime.fromtimestamp(tick.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                     "trade_mode": int(info.trade_mode)})
    return pd.DataFrame(rows)


def bars_1h(symbol: str, count: int = 500) -> pd.DataFrame:
    mt5 = _mt5(); r = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, count)
    if r is None:
        raise MT5Error(f"copy_rates failed for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")  # broker server time
    return df.rename(columns={"time": "Datetime_server", "open": "Open", "high": "High", "low": "Low", "close": "Close", "tick_volume": "Volume"}).set_index("Datetime_server")


def bars_1h_synced(symbol: str, count: int = 500, retries: int = 6, wait_s: float = 1.0) -> tuple[pd.DataFrame, int, bool]:
    """``bars_1h`` that waits for the terminal's H1 history to catch up with the live tick.

    Right after a symbol is (re)selected, copy_rates can return the terminal's stale local
    history: the newest bar lags the hour of the last tick and the bar before it is partial
    (seen 2026-10-19: GBPUSD/EURGBP 13:00 bar returned half-filled at 14:16 and 14:42 UTC).
    Re-request until the newest bar reaches the tick's hour. Returns (bars, retries_used, stale).
    """
    import time
    mt5 = _mt5(); df = pd.DataFrame()
    for attempt in range(max(0, int(retries)) + 1):
        df = bars_1h(symbol, count)
        t = mt5.symbol_info_tick(symbol)
        if t is None or not getattr(t, "time", 0) or df.empty:
            return df, attempt, False
        tick_hour = pd.Timestamp(int(t.time), unit="s").floor("h")  # both in server time
        if pd.Timestamp(df.index.max()) >= tick_hour:
            return df, attempt, False
        if attempt < retries:
            time.sleep(wait_s)
    return df, int(retries), True


def server_offset_hours(symbol: str) -> int:
    """Broker server clock minus UTC, in whole hours (OANDA demo is UTC+3 in summer). Tick/bar times are server time."""
    mt5 = _mt5(); t = mt5.symbol_info_tick(symbol)
    if t is None or not t.time:
        return 0
    return int(round((t.time - datetime.now(timezone.utc).timestamp()) / 3600.0))


def recent_ticks(symbol: str, minutes: int = 5) -> pd.DataFrame:
    """Ticks from the last ``minutes`` real minutes. MT5 stamps ticks in server time, so the request window is shifted."""
    mt5 = _mt5(); off = timedelta(hours=server_offset_hours(symbol))
    end = datetime.now(timezone.utc) + off + timedelta(seconds=5); start = end - timedelta(minutes=minutes, seconds=5)
    t = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_INFO)
    if t is None:
        raise MT5Error(f"copy_ticks failed for {symbol}: {mt5.last_error()}")
    df = pd.DataFrame(t)
    if len(df):
        df = df[(df.bid > 0) & (df.ask > 0)].copy()
        df["time_server"] = pd.to_datetime(df["time_msc"], unit="ms")
        df["time_utc"] = df["time_server"] - off
    return df