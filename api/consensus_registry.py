"""Pluggable forecaster registry.

Tier A sources are a public JSON API or a stable feed. Tier B sources parse a
known HTML page. A source that is blocked by robots, ToS, or geography is
listed and returned as SKIPPED with the reason. It is not fetched.

Directions and prices are copied from what the source publishes. A missing
heading stays MISSING. Pivot levels are never turned into a Buy or Sell.
"""
from __future__ import annotations

import html as html_lib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from forex_lab.ui.watchlist import WatchlistError, normalize_pair

from api.consensus_fetch import (
    _bias_word,
    _now_iso,
    _range,
    _row,
    cached_http_get,
    fetch_dailyforex,
    fetch_fxstreet,
    fetch_investing,
)

HORIZONS = ("hourly", "daily")
FetchFn = Callable[[str, datetime | None], dict[str, Any]]


@dataclass(frozen=True)
class SourceSpec:
    name: str
    tier: str
    kind: str
    provides_range: bool
    live: bool
    skip_reason: str
    notes: str
    fetch: FetchFn | None = None


def pair_forms(raw: str) -> dict[str, str]:
    """USDJPY, USD/JPY, USD-JPY, and USDJPY=X share one 6-letter symbol.

    Source-specific spellings (slash, slug) are derived from that symbol.
    """
    pair = normalize_pair(raw)
    base, quote = pair[:3], pair[3:]
    return {
        "pair": pair,
        "base": base,
        "quote": quote,
        "slash": f"{base}/{quote}",
        "slug": f"{base.lower()}-{quote.lower()}",
        "lower": pair.lower(),
    }


def gap_s() -> float:
    raw = os.environ.get("FORX_CONSENSUS_GAP_S", "0.35")
    try:
        gap = float(raw)
    except (TypeError, ValueError):
        gap = 0.35
    return max(0.0, gap)


def _blocked_reason(status: int, body: str, err: str) -> str | None:
    sample = body[:1500] if body else ""
    if status in {401, 403, 429} or "Just a moment" in sample or "cf-browser-verification" in sample:
        return "blocked by bot check"
    if status == 0:
        return err or "network error"
    if status != 200 or not body:
        return err or f"HTTP {status or 'fail'}"
    return None


def _failed(name: str, reason: str, stamp: str, *, url: str = "", with_range: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for hz in HORIZONS:
        block: dict[str, Any] = {"forecaster": _row(name, None, "ERROR", reason, url, stamp)}
        if with_range:
            block["range"] = _range(name, None, None, "", "ERROR", reason, stamp)
        out[hz] = block
    return out


def _both(
    name: str,
    direction: str | None,
    status: str,
    reason: str,
    url: str,
    stamp: str,
    *,
    with_range: bool = False,
    low: float | None = None,
    high: float | None = None,
    window: str = "",
    range_status: str | None = None,
    range_reason: str = "",
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for hz in HORIZONS:
        block: dict[str, Any] = {"forecaster": _row(name, direction, status, reason, url, stamp)}
        if with_range:
            block["range"] = _range(
                name,
                low,
                high,
                window,
                range_status or status,
                range_reason,
                stamp,
            )
        out[hz] = block
    return out


def parse_fxempire_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Published rating plus classic S1-R1. Other pivot methods are ignored."""
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    direction = _bias_word(str(summary.get("rating") or ""))
    classic = None
    for row in payload.get("pivots") or []:
        if isinstance(row, dict) and str(row.get("method") or "").lower() == "classic":
            classic = row
            break
    low = high = None
    if isinstance(classic, dict):
        try:
            low = float(classic["s1"]) if classic.get("s1") is not None else None
            high = float(classic["r1"]) if classic.get("r1") is not None else None
        except (TypeError, ValueError):
            low = high = None
    return {"direction": direction, "low": low, "high": high}


def fetch_fxempire(pair: str, now: datetime | None = None) -> dict[str, Any]:
    """FXEmpire technical-analysis JSON. Rating and classic pivots are published fields."""
    stamp = _now_iso(now)
    forms = pair_forms(pair)
    name = "FXEmpire"
    out: dict[str, Any] = {}
    for hz, frame in (("hourly", "1H"), ("daily", "1D")):
        url = f"https://www.fxempire.com/api/v1/en/ta/forex/{forms['slug']}?timeframe={frame}"
        status, body, err = cached_http_get(url, timeout=12)
        blocked = _blocked_reason(status, body, err)
        if blocked:
            out[hz] = {
                "forecaster": _row(name, None, "ERROR", blocked, url, stamp),
                "range": _range(name, None, None, "", "ERROR", blocked, stamp),
            }
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict) or not payload.get("summary"):
            why = "technical summary not in response"
            out[hz] = {
                "forecaster": _row(name, None, "MISSING", why, url, stamp),
                "range": _range(name, None, None, "", "MISSING", why, stamp),
            }
            continue
        parsed = parse_fxempire_payload(payload)
        direction = parsed["direction"]
        low, high = parsed["low"], parsed["high"]
        range_ok = low is not None and high is not None and high >= low
        if direction is None:
            why = "rating missing or not Buy/Sell/Neutral"
            out[hz] = {
                "forecaster": _row(name, None, "MISSING", why, url, stamp),
                "range": _range(
                    name,
                    low if range_ok else None,
                    high if range_ok else None,
                    "classic S1-R1" if range_ok else "",
                    "OK" if range_ok else "MISSING",
                    "" if range_ok else "classic pivots missing",
                    stamp,
                ),
            }
            continue
        out[hz] = {
            "forecaster": _row(name, direction, "OK", "", url, stamp),
            "range": _range(
                name,
                low if range_ok else None,
                high if range_ok else None,
                "classic S1-R1" if range_ok else "",
                "OK" if range_ok else "MISSING",
                "" if range_ok else "classic pivots missing",
                stamp,
            ),
        }
    return out


def stocktwits_lean(messages: list[dict[str, Any]]) -> tuple[str | None, str]:
    """Majority of explicit Bullish/Bearish tags. Untagged messages are ignored.

    Fewer than three tags, or no side at 60% of the tagged set, does not
    become a Buy or Sell. A split at or above that line is Neutral.
    """
    bull = bear = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        entities = message.get("entities") if isinstance(message.get("entities"), dict) else {}
        sentiment = entities.get("sentiment") if isinstance(entities.get("sentiment"), dict) else {}
        basic = str(sentiment.get("basic") or "")
        if basic == "Bullish":
            bull += 1
        elif basic == "Bearish":
            bear += 1
    tagged = bull + bear
    detail = f"{bull} bullish / {bear} bearish tags"
    if tagged < 3:
        return None, f"only {tagged} tagged messages (need 3)"
    if bull / tagged >= 0.6 and bull > bear:
        return "Buy", detail
    if bear / tagged >= 0.6 and bear > bull:
        return "Sell", detail
    return "Neutral", f"{detail} — no 60% side"


def fetch_stocktwits(pair: str, now: datetime | None = None) -> dict[str, Any]:
    """StockTwits public stream. Official API, no key. Recent tags are an hourly read."""
    stamp = _now_iso(now)
    forms = pair_forms(pair)
    name = "StockTwits"
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{forms['pair']}.json"
    status, body, err = cached_http_get(url, timeout=12)
    blocked = _blocked_reason(status, body, err)
    if blocked or status == 404:
        reason = "symbol not on StockTwits" if status == 404 else (blocked or "request failed")
        state = "MISSING" if status == 404 else "ERROR"
        return _both(name, None, state, reason, url, stamp)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {}
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(messages, list):
        return _both(name, None, "MISSING", "no message stream", url, stamp)
    direction, detail = stocktwits_lean(messages)
    if direction is None:
        hourly = {"forecaster": _row(name, None, "MISSING", detail, url, stamp)}
    else:
        hourly = {"forecaster": _row(name, direction, "OK", detail, url, stamp)}
    daily = {
        "forecaster": _row(name, None, "MISSING", "recent message tags, not a daily call", url, stamp),
    }
    return {"hourly": hourly, "daily": daily}


_ACTION_BIAS = re.compile(
    r"intraday bias in (?P<pair>[A-Z]{3}/[A-Z]{3}) (?:remains|stays) "
    r"(?P<bias>neutral|bullish|bearish|on the upside|on the downside)",
    re.I,
)


def parse_actionforex_feed(xml: str, slash: str) -> dict[str, Any]:
    """First explicit intraday-bias sentence for this pair. Article body is not kept."""
    wanted = slash.upper()
    for item in re.findall(r"<item\b[^>]*>(.*?)</item>", xml, flags=re.S | re.I):
        title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, flags=re.S | re.I)
        link = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", item, flags=re.S | re.I)
        plain = html_lib.unescape(re.sub(r"<[^>]+>", " ", item))
        found = _ACTION_BIAS.search(plain)
        if not found or found.group("pair").upper() != wanted:
            continue
        word = found.group("bias").lower()
        if word in {"neutral"}:
            direction = "Neutral"
        elif word in {"bullish", "on the upside"}:
            direction = "Buy"
        elif word in {"bearish", "on the downside"}:
            direction = "Sell"
        else:
            continue
        label = re.sub(r"\s+", " ", title.group(1)).strip() if title else ""
        href = (link.group(1).strip() if link else "")
        window = "intraday bias"
        if "weekly" in label.lower():
            window = "weekly outlook · intraday bias"
        elif "daily" in label.lower():
            window = "daily outlook · intraday bias"
        return {"direction": direction, "url": href, "window": window, "title": label}
    return {"direction": None, "url": "", "window": "", "title": ""}


def fetch_actionforex(pair: str, now: datetime | None = None) -> dict[str, Any]:
    """ActionForex category RSS. The bias sentence is their published lean."""
    stamp = _now_iso(now)
    forms = pair_forms(pair)
    name = "ActionForex"
    url = f"https://www.actionforex.com/category/action-insight/{forms['lower']}-outlook/feed/"
    status, body, err = cached_http_get(url, timeout=12)
    blocked = _blocked_reason(status, body, err)
    if blocked:
        return _both(name, None, "ERROR", blocked, url, stamp)
    parsed = parse_actionforex_feed(body, forms["slash"])
    if not parsed["direction"]:
        why = "no intraday bias sentence for this pair"
        return _both(name, None, "MISSING", why, url, stamp)
    page = str(parsed["url"] or url)
    window = str(parsed["window"] or "intraday bias")
    # The sentence is an intraday call. A daily/weekly outlook that contains it
    # is also their latest note for that article, labeled with the article type.
    hourly = {"forecaster": _row(name, parsed["direction"], "OK", window, page, stamp)}
    if "daily" in window or "weekly" in window:
        daily = {"forecaster": _row(name, parsed["direction"], "OK", window, page, stamp)}
    else:
        daily = {"forecaster": _row(name, None, "MISSING", "intraday sentence only, no daily outlook", page, stamp)}
    return {"hourly": hourly, "daily": daily}


def parse_fxssi_signals(html: str) -> dict[str, str]:
    """Symbol to the signal class FXSSI prints (buy / sell / neutral)."""
    found = re.findall(
        r'class="symbol">([A-Z0-9]{6})</div>[\s\S]*?class="signal ([a-z]+)"',
        html,
    )
    out: dict[str, str] = {}
    for symbol, signal in found:
        mapped = {"buy": "Buy", "sell": "Sell", "neutral": "Neutral"}.get(signal)
        if mapped and symbol not in out:
            out[symbol] = mapped
    return out


def fetch_fxssi(pair: str, now: datetime | None = None) -> dict[str, Any]:
    """FXSSI current-ratio board. Direction is their signal class, not the bar widths."""
    stamp = _now_iso(now)
    forms = pair_forms(pair)
    name = "FXSSI"
    url = "https://fxssi.com/tools/current-ratio"
    status, body, err = cached_http_get(url, timeout=18)
    blocked = _blocked_reason(status, body, err)
    if blocked:
        return _both(name, None, "ERROR", blocked, url, stamp)
    signals = parse_fxssi_signals(body)
    direction = signals.get(forms["pair"])
    if direction is None:
        return _both(name, None, "MISSING", "pair not on the current-ratio board", url, stamp)
    reason = "current-ratio signal (not timeframe-specific)"
    return _both(name, direction, "OK", reason, url, stamp)


_IG_SENTENCE = re.compile(
    r"(\d{1,3})%\s+of client accounts are\s+(long|short)",
    re.I,
)


def parse_ig_sentiment(html: str) -> dict[str, Any]:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    found = _IG_SENTENCE.search(text)
    if not found:
        return {"direction": None, "reason": "no client-account sentence"}
    pct = int(found.group(1))
    side = found.group(2).lower()
    direction = "Buy" if side == "long" else "Sell"
    return {
        "direction": direction,
        "reason": f"{pct}% of client accounts are {side}",
    }


def fetch_ig(pair: str, now: datetime | None = None) -> dict[str, Any]:
    """IG client-account sentence. The long/short word is theirs. It is not flipped."""
    stamp = _now_iso(now)
    forms = pair_forms(pair)
    name = "IG"
    url = f"https://www.ig.com/uk/forex/markets-forex/{forms['slug']}"
    status, body, err = cached_http_get(url, timeout=15)
    blocked = _blocked_reason(status, body, err)
    if blocked:
        return _both(name, None, "ERROR", blocked, url, stamp)
    parsed = parse_ig_sentiment(body)
    if not parsed["direction"]:
        return _both(name, None, "MISSING", str(parsed["reason"]), url, stamp)
    reason = str(parsed["reason"]) + " (not timeframe-specific)"
    return _both(name, parsed["direction"], "OK", reason, url, stamp)


def parse_mataf_pivots(html: str) -> dict[str, dict[str, float]]:
    """Classic table: R3 R2 R1 Pivot S1 S2 S3. Header order is fixed on the page."""
    out: dict[str, dict[str, float]] = {}
    for match in re.finditer(r"<th>\s*([A-Z0-9]{6})\s*</th>(.{0,900}?)</tr>", html, flags=re.I | re.S):
        symbol = match.group(1).upper()
        nums = re.findall(r"<td[^>]*>\s*([0-9]+(?:\.[0-9]+)?)\s*</td>", match.group(2), flags=re.I)
        if len(nums) < 7:
            continue
        r3, r2, r1, pivot, s1, s2, s3 = (float(num) for num in nums[:7])
        out[symbol] = {"r3": r3, "r2": r2, "r1": r1, "pivot": pivot, "s1": s1, "s2": s2, "s3": s3}
    return out


def fetch_mataf(pair: str, now: datetime | None = None) -> dict[str, Any]:
    """Mataf classic daily pivots. A range only — no direction is inferred."""
    stamp = _now_iso(now)
    forms = pair_forms(pair)
    name = "Mataf"
    url = "https://www.mataf.net/en/forex/tools/pivot-points"
    status, body, err = cached_http_get(url, timeout=15)
    blocked = _blocked_reason(status, body, err)
    if blocked:
        return _failed(name, blocked, stamp, url=url, with_range=True)
    table = parse_mataf_pivots(body)
    levels = table.get(forms["pair"])
    if not levels:
        why = "pair not on the pivot table"
        block = _both(name, None, "MISSING", why, url, stamp, with_range=True, range_status="MISSING", range_reason=why)
        for hz in block:
            block[hz]["forecaster"]["status"] = "RANGE"
        return block
    low, high = levels["s1"], levels["r1"]
    range_ok = high >= low
    why = "classic daily pivots only — no Buy/Sell published"
    hourly = {
        "forecaster": _row(name, None, "RANGE", why, url, stamp),
        "range": _range(name, None, None, "", "MISSING", "daily classic pivots only", stamp),
    }
    daily = {
        "forecaster": _row(name, None, "RANGE", why, url, stamp),
        "range": _range(
            name,
            low if range_ok else None,
            high if range_ok else None,
            "daily classic S1-R1" if range_ok else "",
            "OK" if range_ok else "MISSING",
            "" if range_ok else "S1-R1 invalid",
            stamp,
        ),
    }
    return {"hourly": hourly, "daily": daily}


def _wrap_legacy(fn: FetchFn, name: str, *, with_range: bool) -> FetchFn:
    def _run(pair: str, now: datetime | None = None) -> dict[str, Any]:
        try:
            pair_forms(pair)
        except WatchlistError as exc:
            return _failed(name, str(exc), _now_iso(now), with_range=with_range)
        return fn(pair, now=now)

    return _run


REGISTRY: tuple[SourceSpec, ...] = (
    SourceSpec(
        "FXEmpire",
        "A",
        "api",
        True,
        True,
        "",
        "Public JSON https://www.fxempire.com/api/v1/en/ta/forex/{base}-{quote}?timeframe=1H|1D — rating plus classic pivots.",
        fetch_fxempire,
    ),
    SourceSpec(
        "StockTwits",
        "A",
        "api",
        False,
        True,
        "",
        "Official stream https://api.stocktwits.com/api/2/streams/symbol/{PAIR}.json — explicit Bullish/Bearish tags only.",
        fetch_stocktwits,
    ),
    SourceSpec(
        "ActionForex",
        "A",
        "feed",
        False,
        True,
        "",
        "Category RSS. Direction is the 'intraday bias … remains' sentence.",
        fetch_actionforex,
    ),
    SourceSpec(
        "Investing.com",
        "B",
        "html",
        True,
        True,
        "",
        "Technical page __NEXT_DATA__ summary and pivot S1-R1. robots.txt allows the currencies technical path.",
        _wrap_legacy(fetch_investing, "Investing.com", with_range=True),
    ),
    SourceSpec(
        "DailyForex",
        "B",
        "html",
        True,
        True,
        "",
        "Currency page, article list API, then the outlook heading or an explicit title bias. Levels only when stop and target are both printed.",
        _wrap_legacy(fetch_dailyforex, "DailyForex", with_range=True),
    ),
    SourceSpec(
        "FXSSI",
        "B",
        "html",
        False,
        True,
        "",
        "Current-ratio board signal class (buy/sell/neutral). Bar widths are not remapped.",
        fetch_fxssi,
    ),
    SourceSpec(
        "IG",
        "B",
        "html",
        False,
        True,
        "",
        "Client-account sentence on the pair overview. Long stays Buy, short stays Sell — not a contrarian flip.",
        fetch_ig,
    ),
    SourceSpec(
        "Mataf",
        "B",
        "html",
        True,
        True,
        "",
        "Daily classic pivot table. S1-R1 is a range. No direction is invented from the pivot.",
        fetch_mataf,
    ),
    SourceSpec(
        "FXStreet",
        "B",
        "html",
        False,
        True,
        "",
        "Pair page. Often a bot check; that is stored as ERROR. The documented Market Tools API needs a key, so it is not called.",
        _wrap_legacy(fetch_fxstreet, "FXStreet", with_range=False),
    ),
    SourceSpec(
        "TradingView",
        "A",
        "api",
        True,
        False,
        "scanner.tradingview.com robots.txt disallows / (only /global/scan is allowed). Not called.",
        "Would have been the scanner recommendation. Skipped for robots.txt.",
    ),
    SourceSpec(
        "Myfxbook",
        "B",
        "html",
        False,
        False,
        "Community outlook sits behind a bot check and has no keyless JSON. Not called.",
        "Outlook page returns a Cloudflare challenge from this environment.",
    ),
    SourceSpec(
        "Dukascopy SWFX",
        "A",
        "api",
        False,
        False,
        "freeserv.dukascopy.com sentiment index returns 403/204. Not called.",
        "SWFX long/short JSON is not reachable without a session.",
    ),
    SourceSpec(
        "DailyFX",
        "B",
        "html",
        False,
        False,
        "dailyfx.com timed out (no bytes). No stable public JSON found. Not called.",
        "IG client sentiment on dailyfx.com did not answer.",
    ),
    SourceSpec(
        "FX Blue",
        "B",
        "html",
        False,
        False,
        "Sentiment figures load inside a JS widget with no documented public JSON. Not called.",
        "The tools page does not include the pair ratios in the HTML.",
    ),
)


def live_sources() -> tuple[SourceSpec, ...]:
    return tuple(spec for spec in REGISTRY if spec.live)


def range_sources() -> tuple[SourceSpec, ...]:
    return tuple(spec for spec in REGISTRY if spec.live and spec.provides_range)


def fetch_registered(pair: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Both horizons. One live source failing stays on that source.

    A short pause sits between sources. Shared pages are cached inside the process.
    """
    stamp = _now_iso(now)
    forms = pair_forms(pair)
    symbol = forms["pair"]
    collected: list[tuple[SourceSpec, dict[str, Any]]] = []
    pause = gap_s()
    for index, spec in enumerate(live_sources()):
        if index and pause:
            time.sleep(pause)
        assert spec.fetch is not None
        try:
            block = spec.fetch(symbol, now)
        except Exception as exc:
            block = _failed(spec.name, f"{spec.name} fetch failed: {exc.__class__.__name__}", stamp, with_range=spec.provides_range)
        if not isinstance(block, dict):
            block = _failed(spec.name, f"{spec.name} fetch failed", stamp, with_range=spec.provides_range)
        collected.append((spec, block))
    out: dict[str, Any] = {}
    for hz in HORIZONS:
        forecasters: list[dict[str, Any]] = []
        ranges: list[dict[str, Any]] = []
        for spec, block in collected:
            node = block.get(hz) if isinstance(block.get(hz), dict) else {}
            row = node.get("forecaster")
            if isinstance(row, dict):
                forecasters.append(row)
            else:
                forecasters.append(_row(spec.name, None, "ERROR", f"{spec.name} returned no row", "", stamp))
            if spec.provides_range:
                rng = node.get("range")
                if isinstance(rng, dict):
                    ranges.append(rng)
                else:
                    ranges.append(_range(spec.name, None, None, "", "MISSING", "no range published", stamp))
        out[hz] = {"fetched_at": stamp, "forecasters": forecasters, "ranges": ranges}
    return out


def aggregate_consensus(forecasters: list[dict[str, Any]], ranges: list[dict[str, Any]]) -> dict[str, Any]:
    """Side counts and an agreement score. No price is created when a range is missing.

    Confidence is the leading side's share of Buy+Sell votes, and only when at
    least two sources published a Buy or a Sell and one side is strictly ahead.
    Neutral is counted and can be the top side when nobody published a direction.
    A single source does not get a confidence number.
    """
    counts = {"Buy": 0, "Sell": 0, "Neutral": 0}
    missing = errors = skipped = 0
    for row in forecasters:
        status = str(row.get("status") or "").upper()
        direction = row.get("direction")
        if status == "OK" and direction in counts:
            counts[str(direction)] += 1
        elif status == "SKIPPED":
            skipped += 1
        elif status == "ERROR":
            errors += 1
        elif status == "RANGE":
            continue
        else:
            missing += 1
    buys, sells, neutrals = counts["Buy"], counts["Sell"], counts["Neutral"]
    directional = buys + sells
    top: str | None = None
    confidence: float | None = None
    if buys > sells and directional >= 1:
        top = "Buy"
    elif sells > buys and directional >= 1:
        top = "Sell"
    elif directional == 0 and neutrals >= 1:
        top = "Neutral"
    if top in {"Buy", "Sell"} and directional >= 2:
        confidence = round(counts[top] / directional, 4)
    elif top == "Neutral" and neutrals >= 2 and directional == 0:
        confidence = 1.0
    lows: list[float] = []
    highs: list[float] = []
    for row in ranges:
        if str(row.get("status") or "").upper() != "OK":
            continue
        try:
            low = float(row["low"])
            high = float(row["high"])
        except (TypeError, ValueError, KeyError):
            continue
        if high < low:
            continue
        lows.append(low)
        highs.append(high)
    span = None
    if lows and highs:
        span = {"low": min(lows), "high": max(highs), "count": len(lows)}
    return {
        "counts": counts,
        "ok": buys + sells + neutrals,
        "missing": missing,
        "errors": errors,
        "skipped": skipped,
        "top_side": top,
        "confidence": confidence,
        "range_span": span,
    }
