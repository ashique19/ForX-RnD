"""Daily lens library for Phase 1.

Definitions are frozen in research/phase1/PREREG_20261112.md (commit 218e3e5).
Every lens on a completed daily or weekly bar is causal: it uses only that bar and earlier completed bars.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_HOURS_PER_DAY = 12
MIN_DAYS_PER_WEEK = 3
H = pd.Timedelta(hours=1)

DIRECTIONAL = [
    "SMA_STACK",
    "ADX",
    "DONCHIAN",
    "SWING",
    "RSI_REGIME",
    "SMA200",
    "WEEKLY",
]


def daily_ohlc(h1: pd.DataFrame) -> pd.DataFrame:
    """NY 17:00 days, same Close and end_utc as daily_bars() in daily_trend_flip_wf_20261020.py, plus OHLC."""
    idx = pd.DatetimeIndex(h1.index)
    ny = idx.tz_localize("UTC").tz_convert("America/New_York")
    key = (ny + pd.Timedelta(hours=7)).floor("D").tz_localize(None)
    g = h1.groupby(key)
    d = g.agg(Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"), Close=("Close", "last"))
    d["hours"] = g.size()
    end_ny = pd.DatetimeIndex(d.index).tz_localize("America/New_York") + pd.Timedelta(hours=17)
    d["end_utc"] = end_ny.tz_convert("UTC").tz_localize(None)
    d = d[d["hours"] >= MIN_HOURS_PER_DAY].copy()
    return d


def weekly_ohlc(daily: pd.DataFrame) -> pd.DataFrame:
    """Monday-labelled weeks of NY daily bars.

    A week is complete only at Friday 17:00 New York of that week (even if Friday is a holiday
    and the last daily close is Thursday). A mid-week snapshot is never treated as a completed week.
    Drop weeks with fewer than 3 daily bars.
    """
    idx = pd.DatetimeIndex(daily.index)
    monday = pd.DatetimeIndex(idx - pd.to_timedelta(idx.dayofweek, unit="D"))
    g = daily.groupby(monday)
    w = pd.DataFrame(
        {
            "Open": g["Open"].first(),
            "High": g["High"].max(),
            "Low": g["Low"].min(),
            "Close": g["Close"].last(),
            "n": g.size(),
        }
    )
    w = w[w["n"] >= MIN_DAYS_PER_WEEK].copy()
    friday = pd.DatetimeIndex(w.index) + pd.Timedelta(days=4)
    end_ny = friday.tz_localize("America/New_York") + pd.Timedelta(hours=17)
    w["end_utc"] = end_ny.tz_convert("UTC").tz_localize(None)
    return w


def wilder_smooth(s: pd.Series, n: int) -> pd.Series:
    """Wilder smoothing: first value is the SMA of the first n finite values, then prev - prev/n + x/n."""
    x = s.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    finite = np.isfinite(x)
    run = 0
    first = None
    for i in range(len(x)):
        if finite[i]:
            run += 1
            if run == n:
                first = i
                break
        else:
            run = 0
    if first is None:
        return pd.Series(out, index=s.index)
    out[first] = float(np.mean(x[first - n + 1 : first + 1]))
    for j in range(first + 1, len(x)):
        if np.isfinite(x[j]) and np.isfinite(out[j - 1]):
            out[j] = out[j - 1] - out[j - 1] / n + x[j] / n
    return pd.Series(out, index=s.index)


def wilder_atr(daily: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = daily["Close"].shift(1)
    tr = pd.concat(
        [
            daily["High"] - daily["Low"],
            (daily["High"] - prev).abs(),
            (daily["Low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return wilder_smooth(tr, n)


def wilder_rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = wilder_smooth(gain, n)
    avg_loss = wilder_smooth(loss, n)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.where(avg_loss != 0.0, 100.0)
    rsi = rsi.where(avg_gain.notna() & avg_loss.notna())
    return rsi


def wilder_adx(daily: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    high, low, close = daily["High"], daily["Low"], daily["Close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=daily.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=daily.index)
    plus_dm.iloc[0] = np.nan
    minus_dm.iloc[0] = np.nan
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = wilder_smooth(tr, n)
    sm_plus = wilder_smooth(plus_dm, n)
    sm_minus = wilder_smooth(minus_dm, n)
    plus_di = 100.0 * sm_plus / atr
    minus_di = 100.0 * sm_minus / atr
    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    adx = wilder_smooth(dx, n)
    return pd.DataFrame({"adx": adx, "plus_di": plus_di, "minus_di": minus_di}, index=daily.index)


def swing_state(b: pd.DataFrame, k: int = 2) -> pd.Series:
    """+1 HH&HL, -1 LH&LL, 0 otherwise, NaN until two confirmed swings of each kind. Pivot i confirmed when i+k has completed."""
    hi, lo, n = b["High"].to_numpy(), b["Low"].to_numpy(), len(b)
    sh, sl = np.zeros(n, bool), np.zeros(n, bool)
    for i in range(k, n - k):
        sh[i] = hi[i] > hi[i - k : i].max() and hi[i] > hi[i + 1 : i + k + 1].max()
        sl[i] = lo[i] < lo[i - k : i].min() and lo[i] < lo[i + 1 : i + k + 1].min()
    out = np.full(n, np.nan)
    highs, lows = [], []
    for j in range(n):
        i = j - k
        if i >= k:
            if sh[i]:
                highs.append(hi[i])
            if sl[i]:
                lows.append(lo[i])
        if len(highs) >= 2 and len(lows) >= 2:
            up = highs[-1] > highs[-2] and lows[-1] > lows[-2]
            dn = highs[-1] < highs[-2] and lows[-1] < lows[-2]
            out[j] = 1.0 if up else (-1.0 if dn else 0.0)
    return pd.Series(out, index=b.index)


def lens_sma_stack(daily: pd.DataFrame) -> pd.Series:
    c = daily["Close"]
    sma20 = c.rolling(20, min_periods=20).mean()
    sma50 = c.rolling(50, min_periods=50).mean()
    sma200 = c.rolling(200, min_periods=200).mean()
    slope = sma50 - sma50.shift(5)
    up = (sma20 > sma50) & (sma50 > sma200) & (slope > 0)
    dn = (sma20 < sma50) & (sma50 < sma200) & (slope < 0)
    out = pd.Series(np.where(up, 1.0, np.where(dn, -1.0, 0.0)), index=daily.index, dtype=float)
    return out.where(sma200.notna() & slope.notna())


def lens_adx(daily: pd.DataFrame, n: int = 14, thresh: float = 25.0) -> pd.Series:
    a = wilder_adx(daily, n)
    up = (a["adx"] >= thresh) & (a["plus_di"] > a["minus_di"])
    dn = (a["adx"] >= thresh) & (a["minus_di"] > a["plus_di"])
    out = pd.Series(np.where(up, 1.0, np.where(dn, -1.0, 0.0)), index=daily.index, dtype=float)
    return out.where(a["adx"].notna() & a["plus_di"].notna() & a["minus_di"].notna())


def lens_donchian(daily: pd.DataFrame, n: int = 20) -> pd.Series:
    high_n = daily["High"].rolling(n, min_periods=n).max()
    low_n = daily["Low"].rolling(n, min_periods=n).min()
    width = high_n - low_n
    pos = (daily["Close"] - low_n) / width.replace(0.0, np.nan)
    out = pd.Series(np.where(pos >= 2.0 / 3.0, 1.0, np.where(pos <= 1.0 / 3.0, -1.0, 0.0)), index=daily.index, dtype=float)
    out = out.where(pos.notna(), 0.0)
    return out.where(high_n.notna())


def lens_swing(daily: pd.DataFrame, k: int = 2) -> pd.Series:
    return swing_state(daily, k)


def lens_rsi_regime(daily: pd.DataFrame, n: int = 14) -> pd.Series:
    rsi = wilder_rsi(daily["Close"], n)
    out = pd.Series(np.where(rsi > 50.0, 1.0, np.where(rsi < 50.0, -1.0, 0.0)), index=daily.index, dtype=float)
    return out.where(rsi.notna())


def rsi_value(daily: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder_rsi(daily["Close"], n)


def lens_sma200(daily: pd.DataFrame, n: int = 200) -> pd.Series:
    sma = daily["Close"].rolling(n, min_periods=n).mean()
    out = pd.Series(np.sign(daily["Close"] - sma), index=daily.index, dtype=float)
    return out.where(sma.notna())


def sma200_distance_atr(daily: pd.DataFrame) -> pd.Series:
    sma = daily["Close"].rolling(200, min_periods=200).mean()
    atr = wilder_atr(daily, 14)
    return (daily["Close"] - sma) / atr.replace(0.0, np.nan)


def lens_weekly(weekly: pd.DataFrame, n: int = 10) -> pd.Series:
    sma = weekly["Close"].rolling(n, min_periods=n).mean()
    out = pd.Series(np.sign(weekly["Close"] - sma), index=weekly.index, dtype=float)
    return out.where(sma.notna())


def lens_atr_vol(daily: pd.DataFrame, n: int = 14, med_win: int = 100) -> pd.Series:
    atr = wilder_atr(daily, n)
    med = atr.rolling(med_win, min_periods=med_win).median()
    reg = pd.Series(np.where(atr > med, "high", "low"), index=daily.index, dtype=object)
    return reg.where(med.notna(), "unknown")


def htf_at(bar_end: pd.DatetimeIndex, values: np.ndarray, decide: pd.DatetimeIndex):
    """Latest higher-timeframe bar whose END is at or before each decision time."""
    ends = np.asarray(pd.DatetimeIndex(bar_end), dtype="datetime64[ns]")
    dec = np.asarray(pd.DatetimeIndex(decide), dtype="datetime64[ns]")
    pos = np.searchsorted(ends, dec, side="right") - 1
    ok = pos >= 0
    p = np.clip(pos, 0, None)
    vals = np.asarray(values)
    if len(vals) and (vals.dtype == object or isinstance(vals.flat[0] if len(vals) else None, str)):
        picked = np.array([vals[i] if flag else "unknown" for i, flag in zip(p, ok)], dtype=object)
    else:
        picked = np.where(ok, np.asarray(vals, dtype=float)[p], np.nan)
    used = pd.DatetimeIndex(np.where(ok, ends[p], np.datetime64("NaT")))
    return picked, used


def compute_daily_lenses(daily: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=daily.index)
    out["SMA_STACK"] = lens_sma_stack(daily)
    out["ADX"] = lens_adx(daily)
    out["DONCHIAN"] = lens_donchian(daily)
    out["SWING"] = lens_swing(daily)
    out["RSI_REGIME"] = lens_rsi_regime(daily)
    out["SMA200"] = lens_sma200(daily)
    out["ATR_VOL"] = lens_atr_vol(daily)
    out["RSI"] = rsi_value(daily)
    out["SMA200_DIST_ATR"] = sma200_distance_atr(daily)
    out["end_utc"] = daily["end_utc"]
    return out


def lenses_at_decisions(h1: pd.DataFrame, sig_index: pd.DatetimeIndex) -> pd.DataFrame:
    """All library values known at each 1h signal bar t (decision at t + 1h)."""
    decide = pd.DatetimeIndex(sig_index) + H
    daily = daily_ohlc(h1)
    weekly = weekly_ohlc(daily)
    dlen = compute_daily_lenses(daily)
    wtrend = lens_weekly(weekly)
    rows = {}
    used = {}
    ends_d = pd.DatetimeIndex(dlen["end_utc"])
    for name in ["SMA_STACK", "ADX", "DONCHIAN", "SWING", "RSI_REGIME", "SMA200", "RSI", "SMA200_DIST_ATR"]:
        rows[name], used[name] = htf_at(ends_d, dlen[name].to_numpy(), decide)
    vol, used_vol = htf_at(ends_d, dlen["ATR_VOL"].to_numpy(), decide)
    rows["ATR_VOL"] = vol
    used["ATR_VOL"] = used_vol
    rows["WEEKLY"], used["WEEKLY"] = htf_at(pd.DatetimeIndex(weekly["end_utc"]), wtrend.to_numpy(), decide)
    out = pd.DataFrame(rows, index=sig_index)
    out["endD"] = used["SMA200"]
    out["endW"] = used["WEEKLY"]
    out["decide"] = decide
    return out


def agrees(pred: pd.Series, trend: pd.Series, buy: int, sell: int) -> pd.Series:
    tr = pd.to_numeric(trend.reindex(pred.index), errors="coerce")
    p = pred.reindex(tr.index)
    return ((p == buy) & (tr > 0)) | ((p == sell) & (tr < 0))
