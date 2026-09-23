"""Fetch public forecaster pages and keep only bias, levels, URL, and time.

Article prose is not stored. A missing heading or a blocked page becomes
MISSING or ERROR. Directions are never guessed from pivots or page chrome.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SOURCES = ("DailyForex", "Investing.com", "FXStreet")
RANGE_SOURCES = ("DailyForex", "Investing.com")

_UA = "ForX-RnD research desk"


def _now_iso(now: datetime | None = None) -> str:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_page_cache: dict[str, tuple[float, int, str]] = {}
_page_lock = threading.Lock()
_PAGE_TTL_S = 15 * 60


def http_get(url: str, *, timeout: float = 20.0) -> tuple[int, str, str]:
    """Return (status, body, error). Prefers a browser TLS impersonation."""
    cffi_err = ""
    try:
        from curl_cffi import requests as creq

        res = creq.get(url, impersonate="chrome", timeout=timeout)
        return int(res.status_code), res.text or "", ""
    except Exception as exc:  # pragma: no cover - fallback path
        cffi_err = str(exc)
    try:
        req = Request(url, headers={"User-Agent": _UA, "Accept": "text/html,application/json"})
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return int(resp.status), raw.decode(charset, "replace"), ""
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return int(exc.code), body, cffi_err
    except (URLError, TimeoutError, OSError) as exc:
        reason = str(exc)
        if cffi_err:
            reason = f"{cffi_err}; {reason}"
        return 0, "", reason


def cached_http_get(url: str, *, timeout: float = 15.0) -> tuple[int, str, str]:
    """Reuse a successful page for a few minutes so a watchlist does not refetch it per pair."""
    now = time.time()
    with _page_lock:
        hit = _page_cache.get(url)
        if hit is not None and now - hit[0] < _PAGE_TTL_S:
            return hit[1], hit[2], ""
    status, body, err = http_get(url, timeout=timeout)
    if status == 200 and body:
        with _page_lock:
            _page_cache[url] = (time.time(), status, body)
    return status, body, err


def _strip(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _bias_word(text: str) -> str | None:
    low = text.lower().replace("-", " ").replace("_", " ")
    if "strong sell" in low or low.strip() in {"sell", "strong sell", "bearish"}:
        return "Sell"
    if "strong buy" in low or low.strip() in {"buy", "strong buy", "bullish"}:
        return "Buy"
    if low.strip() in {"neutral", "hold"} or "neutral" in low:
        return "Neutral"
    if "bearish" in low:
        return "Sell"
    if "bullish" in low:
        return "Buy"
    if re.search(r"\bsell\b", low) and "strong sell" not in low:
        return "Sell"
    if re.search(r"\bbuy\b", low):
        return "Buy"
    return None


def _labeled_price(text: str, label: str) -> float | None:
    match = re.search(label + r"[^0-9]{0,40}(\d+\.\d{2,5})", text, re.I)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def parse_dailyforex_article(html: str) -> dict[str, Any]:
    """First outlook heading plus numeric levels in that section only."""
    headings = list(re.finditer(r"<h2[^>]*>(.*?)</h2>", html, flags=re.I | re.S))
    direction = None
    start = 0
    end = 0
    for index, match in enumerate(headings):
        label = _strip(match.group(1))
        bias = None
        low = label.lower()
        if "bearish" in low:
            bias = "Sell"
        elif "bullish" in low:
            bias = "Buy"
        elif "neutral" in low:
            bias = "Neutral"
        if bias is None:
            continue
        direction = bias
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else start + 1800
        break
    if direction is None:
        titled = _title_bias(html)
        if titled is None:
            return {"direction": None, "entry": None, "low": None, "high": None, "window": "", "reason": "no outlook heading"}
        # Title states a bias. Do not scrape prices out of page chrome.
        return {"direction": titled, "entry": None, "low": None, "high": None, "window": "", "reason": ""}
    section = _strip(html[start:end])
    timeline = ""
    found = re.search(r"Timeline:\s*([^.]{1,40})", section, re.I)
    if found:
        timeline = found.group(1).strip()
    stop = _labeled_price(section, r"stop-loss")
    target = _labeled_price(section, r"take-profit")
    entry = _labeled_price(section, r"entry")
    low = high = None
    if stop is not None and target is not None:
        low, high = (stop, target) if stop <= target else (target, stop)
    return {
        "direction": direction,
        "entry": entry,
        "low": low,
        "high": high,
        "window": timeline,
        "reason": "",
    }


def _title_bias(html: str) -> str | None:
    """Explicit bias phrase in the article title only. Not the body."""
    chunks = re.findall(r"<h1[^>]*>(.*?)</h1>", html, flags=re.I | re.S)
    titled = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if titled:
        chunks.append(titled.group(1))
    text = " ".join(_strip(chunk) for chunk in chunks).lower()
    if not text:
        return None
    if "downside bias" in text or "bearish" in text:
        return "Sell"
    if "upside bias" in text or "bullish" in text:
        return "Buy"
    if "neutral bias" in text or re.search(r"\bneutral\b", text):
        return "Neutral"
    return None


def classify_dailyforex_horizon(url: str, window: str) -> str:
    text = f"{window} {url}".lower()
    if re.search(r"hour|intraday|session", text):
        return "hourly"
    if re.search(r"day|week", window.lower()):
        return "daily"
    if "forecast" in url.lower():
        return "daily"
    if "signal" in url.lower():
        return "hourly"
    return "daily"


def _df_pair_url(pair: str) -> str:
    base, quote = pair[:3].lower(), pair[3:].lower()
    return f"https://www.dailyforex.com/currencies/{base}/{quote}"


def _article_hrefs(payload: dict[str, Any], pair: str) -> list[str]:
    slug = pair.lower()
    found: list[str] = []
    for item in payload.get("items") or []:
        if not isinstance(item, str):
            continue
        for href in re.findall(r'href="(/forex-technical-analysis/[^"]+)"', item):
            if slug not in href.lower():
                continue
            if href not in found:
                found.append(href)
    return found


def fetch_dailyforex(pair: str, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """One DailyForex row per horizon. Levels only, no article body."""
    stamp = _now_iso(now)
    page_url = _df_pair_url(pair)
    status, body, err = cached_http_get(page_url)
    empty = {
        hz: {
            "forecaster": _row("DailyForex", None, "ERROR", err or f"HTTP {status}", page_url, stamp),
            "range": _range("DailyForex", None, None, "", "ERROR", err or f"HTTP {status}", stamp),
        }
        for hz in ("hourly", "daily")
    }
    if status != 200 or not body or "Just a moment" in body[:800]:
        reason = err or ("blocked by bot check" if body and "Just a moment" in body else f"HTTP {status or 'fail'}")
        for hz in empty:
            empty[hz]["forecaster"]["reason"] = reason
            empty[hz]["range"]["reason"] = reason
        return empty
    ident = re.search(r"pairId\s*=\s*(\d+)", body)
    if not ident:
        for hz in empty:
            empty[hz]["forecaster"]["status"] = "MISSING"
            empty[hz]["forecaster"]["reason"] = "pair id not on currency page"
            empty[hz]["range"]["status"] = "MISSING"
            empty[hz]["range"]["reason"] = "pair id not on currency page"
        return empty
    api = f"https://www.dailyforex.com/api/articles/currency-pairs/1/{ident.group(1)}/1"
    st_api, raw, api_err = cached_http_get(api)
    try:
        payload = json.loads(raw) if st_api == 200 and raw else {}
    except json.JSONDecodeError:
        payload = {}
    hrefs = _article_hrefs(payload, pair) if isinstance(payload, dict) else []
    if not hrefs:
        reason = api_err or "no pair articles in list"
        for hz in empty:
            empty[hz]["forecaster"]["status"] = "MISSING"
            empty[hz]["forecaster"]["reason"] = reason
            empty[hz]["range"]["status"] = "MISSING"
            empty[hz]["range"]["reason"] = reason
        return empty

    picked: dict[str, dict[str, Any]] = {}
    for href in hrefs[:8]:
        url = "https://www.dailyforex.com" + href
        st_art, html, _art_err = cached_http_get(url)
        if st_art != 200 or not html:
            continue
        parsed = parse_dailyforex_article(html)
        if not parsed.get("direction"):
            continue
        horizon = classify_dailyforex_horizon(url, str(parsed.get("window") or ""))
        if horizon in picked:
            continue
        low, high = parsed.get("low"), parsed.get("high")
        window = str(parsed.get("window") or "")
        range_status = "OK" if low is not None and high is not None and high >= low else "MISSING"
        range_reason = "" if range_status == "OK" else "no stop/target pair on outlook"
        picked[horizon] = {
            "forecaster": _row("DailyForex", parsed["direction"], "OK", "", url, stamp, entry=parsed.get("entry")),
            "range": _range("DailyForex", low, high, window, range_status, range_reason, stamp),
        }
        if "hourly" in picked and "daily" in picked:
            break
    out: dict[str, dict[str, Any]] = {}
    for hz in ("hourly", "daily"):
        if hz in picked:
            out[hz] = picked[hz]
        else:
            why = f"no {hz} outlook on latest page"
            out[hz] = {
                "forecaster": _row("DailyForex", None, "MISSING", why, page_url, stamp),
                "range": _range("DailyForex", None, None, "", "MISSING", why, stamp),
            }
    return out


def parse_investing_technical(html: str) -> dict[str, Any]:
    """Read timeframe summaries embedded in the technical page. No article text."""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, flags=re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    node = (
        data.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("technicalStore", {})
        .get("analysisDetails")
    )
    return node if isinstance(node, dict) else {}


def _investing_horizon(node: dict[str, Any], key: str, stamp: str, url: str) -> dict[str, Any]:
    block = node.get(key)
    if not isinstance(block, dict):
        why = "technical summary not in page"
        return {
            "forecaster": _row("Investing.com", None, "MISSING", why, url, stamp),
            "range": _range("Investing.com", None, None, "", "MISSING", why, stamp),
        }
    summary = block.get("summary")
    label = summary if isinstance(summary, str) else ""
    if isinstance(summary, dict):
        verdict = summary.get("verdict") or {}
        label = str(verdict.get("translated_label") or verdict.get("define") or "")
    direction = _bias_word(str(label))
    pivots = block.get("pivotPoints") if isinstance(block.get("pivotPoints"), dict) else {}
    try:
        low = float(pivots["s1"]) if pivots.get("s1") not in (None, "") else None
        high = float(pivots["r1"]) if pivots.get("r1") not in (None, "") else None
    except (TypeError, ValueError):
        low = high = None
    if direction is None:
        why = "technical summary not in page"
        return {
            "forecaster": _row("Investing.com", None, "MISSING", why, url, stamp),
            "range": _range("Investing.com", low, high, "pivot S1-R1", "MISSING", why, stamp),
        }
    range_ok = low is not None and high is not None and high >= low
    return {
        "forecaster": _row("Investing.com", direction, "OK", "", url, stamp),
        "range": _range(
            "Investing.com",
            low if range_ok else None,
            high if range_ok else None,
            "pivot S1-R1" if range_ok else "",
            "OK" if range_ok else "MISSING",
            "" if range_ok else "pivot levels missing",
            stamp,
        ),
    }


def fetch_investing(pair: str, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    stamp = _now_iso(now)
    slug = f"{pair[:3].lower()}-{pair[3:].lower()}"
    url = f"https://www.investing.com/currencies/{slug}-technical"
    status, body, err = cached_http_get(url)
    blocked = (not body) or status in {0, 401, 403, 429} or "Just a moment" in body[:800]
    if blocked:
        reason = "Investing.com blocked by bot check" if status in {401, 403} or "Just a moment" in (body or "") else (err or f"HTTP {status or 'fail'}")
        return {
            hz: {
                "forecaster": _row("Investing.com", None, "ERROR", reason, url, stamp),
                "range": _range("Investing.com", None, None, "", "ERROR", reason, stamp),
            }
            for hz in ("hourly", "daily")
        }
    node = parse_investing_technical(body)
    if not node:
        why = "technical summary not in page"
        return {
            hz: {
                "forecaster": _row("Investing.com", None, "MISSING", why, url, stamp),
                "range": _range("Investing.com", None, None, "", "MISSING", why, stamp),
            }
            for hz in ("hourly", "daily")
        }
    return {
        "hourly": _investing_horizon(node, "1h", stamp, url),
        "daily": _investing_horizon(node, "1d", stamp, url),
    }


def fetch_fxstreet(pair: str, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """FXStreet is often behind a bot check. Record that; do not invent a bias."""
    stamp = _now_iso(now)
    url = f"https://www.fxstreet.com/currencies/{pair.lower()}"
    status, body, err = cached_http_get(url)
    blocked = status in {0, 401, 403, 429} or "Just a moment" in (body or "")[:1200] or "cf-browser-verification" in (body or "")
    if blocked or status != 200:
        reason = "FXStreet blocked by bot check" if blocked else (err or f"HTTP {status}")
        row = _row("FXStreet", None, "ERROR", reason, url, stamp)
        return {"hourly": {"forecaster": row}, "daily": {"forecaster": dict(row)}}
    # A 200 page still is not a bias unless a single explicit token is present.
    token = re.search(r'"forecastBias"\s*:\s*"(Buy|Sell|Neutral|Hold)"', body)
    if not token:
        row = _row("FXStreet", None, "MISSING", "no forecast bias in page", url, stamp)
        return {"hourly": {"forecaster": row}, "daily": {"forecaster": dict(row)}}
    direction = _bias_word(token.group(1))
    row = _row("FXStreet", direction, "OK" if direction else "MISSING", "" if direction else "no forecast bias in page", url, stamp)
    return {"hourly": {"forecaster": row}, "daily": {"forecaster": dict(row)}}


def _row(
    source: str,
    direction: str | None,
    status: str,
    reason: str,
    url: str,
    fetched_at: str,
    entry: float | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "direction": direction,
        "status": status,
        "reason": reason,
        "url": url,
        "entry": entry,
        "fetched_at": fetched_at,
    }


def _range(
    source: str,
    low: float | None,
    high: float | None,
    window: str,
    status: str,
    reason: str,
    fetched_at: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "low": low,
        "high": high,
        "window": window,
        "status": status,
        "reason": reason,
        "fetched_at": fetched_at,
    }


def fetch_pair_consensus(pair: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Both horizons for one pair. Each registered source is isolated."""
    from api.consensus_registry import fetch_registered

    return fetch_registered(pair, now=now)


def _safe(fn, source: str, stamp: str, *, with_range: bool) -> dict[str, dict[str, Any]]:
    try:
        return fn()
    except Exception:
        reason = f"{source} fetch failed"
        block = {
            hz: {"forecaster": _row(source, None, "ERROR", reason, "", stamp)}
            for hz in ("hourly", "daily")
        }
        if with_range:
            for hz in block:
                block[hz]["range"] = _range(source, None, None, "", "ERROR", reason, stamp)
        return block
