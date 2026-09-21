"""Recent FX headlines (RSS) for decision-support context.

Free source: Google News RSS search (no API key). Headlines are copied from the
feed; the bias note is a keyword heuristic on those titles only — never invented
articles. Failures return an empty payload so the math board still renders.

Not a news wire, not a trade instruction, and not realtime.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from forex_lab.paths import resolve_under_root

DEFAULT_TIMEOUT_S = 6.0
DEFAULT_TTL_S = 300
DEFAULT_MAX_HEADLINES = 8
USER_AGENT = "ForX-RnD-research-lab/0.3 (decision-support; not a commercial scraper)"

BULL_WORDS = (
    "rally",
    "rallies",
    "surge",
    "surges",
    "soars",
    "gains",
    "jumps",
    "climbs",
    "rises",
    "rose",
    "hawkish",
    "upbeat",
    "beats",
    "stronger",
    "strength",
    "optimistic",
    "rebound",
)
BEAR_WORDS = (
    "falls",
    "fell",
    "drop",
    "drops",
    "plunge",
    "plunges",
    "slides",
    "slump",
    "weak",
    "weaker",
    "dovish",
    "misses",
    "miss",
    "fear",
    "risk-off",
    "selloff",
    "sell-off",
    "tumbles",
)
CURRENCY_NAMES = {
    "EUR": ("euro", "ecb", "eur "),
    "USD": ("dollar", "greenback", "fed ", "fomc", "usd "),
    "GBP": ("sterling", "pound", "boe", "gbp "),
    "JPY": ("yen", "boj", "jpy "),
    "AUD": ("aussie", "rba", "aud "),
    "CAD": ("loonie", "boc", "cad "),
    "CHF": ("franc", "snb", "chf "),
    "NZD": ("kiwi", "rbnz", "nzd "),
}


@dataclass
class Headline:
    title: str
    link: str
    published: str
    source: str = ""


@dataclass
class NewsBundle:
    pair: str
    bias: str  # bullish | bearish | mixed | unclear
    bullets: list[str] = field(default_factory=list)
    headlines: list[Headline] = field(default_factory=list)
    source: str = "google_news_rss"
    fetched_at: str | None = None
    error: str | None = None


def _news_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return dict((cfg or {}).get("news") or {})


def cache_path(cfg: dict[str, Any] | None = None):
    rel = _news_cfg(cfg).get("cache_file") or "data/news_cache.json"
    return resolve_under_root(rel)


def pair_search_query(pair: str) -> str:
    p = str(pair).upper().replace("/", "").replace("-", "")
    if len(p) == 6 and p.isalpha():
        a, b = p[:3], p[3:]
        return f'{p} OR "{a}/{b}" OR "{a} {b}" forex OR currency'
    return f"{p} forex"


def google_news_rss_url(pair: str) -> str:
    q = urllib.parse.quote_plus(pair_search_query(pair))
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _parse_rss(xml_text: str, limit: int) -> list[Headline]:
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items: list[Headline] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if not title:
            continue
        items.append(Headline(title=title, link=link, published=pub, source=source))
        if len(items) >= limit:
            break
    return items


def _count_hits(text: str, words: tuple[str, ...]) -> int:
    n = 0
    for w in words:
        if w in text:
            n += 1
    return n


def bias_from_headlines(pair: str, headlines: list[Headline]) -> tuple[str, list[str]]:
    """Keyword heuristic on *provided* titles only. Empty in → unclear, no invented copy."""
    if not headlines:
        return "unclear", ["No headlines available to score."]
    p = str(pair).upper().replace("/", "")
    base = p[:3] if len(p) >= 6 else ""
    quote = p[3:6] if len(p) >= 6 else ""
    bull = 0
    bear = 0
    for h in headlines:
        t = " " + h.title.lower() + " "
        b = _count_hits(t, BULL_WORDS)
        r = _count_hits(t, BEAR_WORDS)
        if base and any(n in t for n in CURRENCY_NAMES.get(base, ())):
            b += _count_hits(t, BULL_WORDS)
            r += _count_hits(t, BEAR_WORDS)
        if quote and any(n in t for n in CURRENCY_NAMES.get(quote, ())):
            b += _count_hits(t, BEAR_WORDS)
            r += _count_hits(t, BULL_WORDS)
        bull += b
        bear += r
    notes: list[str] = []
    if bull == 0 and bear == 0:
        bias = "unclear"
        notes.append("Headlines did not match the small bullish/bearish keyword list.")
    elif abs(bull - bear) <= 1:
        bias = "mixed"
        notes.append(f"Keyword hits roughly balanced (bullish-ish {bull}, bearish-ish {bear}).")
    elif bull > bear:
        bias = "bullish"
        notes.append(f"More bullish-leaning keywords than bearish ({bull} vs {bear}) in these titles.")
    else:
        bias = "bearish"
        notes.append(f"More bearish-leaning keywords than bullish ({bear} vs {bull}) in these titles.")
    for h in headlines[:2]:
        notes.append(f"Quoted: {h.title[:140]}")
    return bias, notes[:4]


def _load_disk_cache(path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_disk_cache(path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return


def _http_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_pair_news(
    pair: str,
    cfg: dict[str, Any] | None = None,
    *,
    force: bool = False,
    xml_text: str | None = None,
) -> NewsBundle:
    """Fetch or reuse cached headlines. ``xml_text`` is for tests (no network)."""
    pair = str(pair).upper().replace("/", "")
    ncfg = _news_cfg(cfg)
    if ncfg.get("enabled") is False:
        return NewsBundle(pair=pair, bias="unclear", bullets=["News lane disabled in config."], error="disabled")
    timeout = float(ncfg.get("timeout_s") or DEFAULT_TIMEOUT_S)
    ttl = int(ncfg.get("cache_ttl_s") or DEFAULT_TTL_S)
    limit = int(ncfg.get("max_headlines") or DEFAULT_MAX_HEADLINES)
    path = cache_path(cfg)
    disk = _load_disk_cache(path)
    now = time.time()
    cached = disk.get(pair) if isinstance(disk.get(pair), dict) else None
    if cached and not force and xml_text is None:
        age = now - float(cached.get("ts") or 0)
        if age < ttl:
            try:
                heads = [Headline(**h) for h in cached.get("headlines") or []]
                return NewsBundle(
                    pair=pair,
                    bias=str(cached.get("bias") or "unclear"),
                    bullets=list(cached.get("bullets") or []),
                    headlines=heads,
                    source=str(cached.get("source") or "google_news_rss"),
                    fetched_at=cached.get("fetched_at"),
                    error=cached.get("error"),
                )
            except Exception:
                pass

    error = None
    raw = xml_text
    if raw is None:
        url = str(ncfg.get("rss_url_template") or "").strip()
        if url:
            url = url.format(query=urllib.parse.quote_plus(pair_search_query(pair)), pair=pair)
        else:
            url = google_news_rss_url(pair)
        try:
            raw = _http_get(url, timeout)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            error = f"news fetch failed ({exc})"
            raw = ""

    headlines = _parse_rss(raw or "", limit)
    if not headlines and error is None:
        error = "news feed empty or unparseable"
    bias, bullets = bias_from_headlines(pair, headlines)
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bundle = NewsBundle(
        pair=pair,
        bias=bias if headlines else "unclear",
        bullets=bullets if headlines else [error or "No headlines."],
        headlines=headlines,
        fetched_at=fetched_at,
        error=error,
    )
    disk[pair] = {
        "ts": now,
        "bias": bundle.bias,
        "bullets": bundle.bullets,
        "headlines": [asdict(h) for h in headlines],
        "source": bundle.source,
        "fetched_at": fetched_at,
        "error": error,
    }
    _save_disk_cache(path, disk)
    return bundle


def fetch_watchlist_news(
    pairs: list[str],
    cfg: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> dict[str, NewsBundle]:
    out: dict[str, NewsBundle] = {}
    for p in pairs:
        key = str(p).upper()
        try:
            out[key] = fetch_pair_news(p, cfg, force=force)
        except Exception as exc:  # noqa: BLE001 — never break the math board
            out[key] = NewsBundle(
                pair=key,
                bias="unclear",
                bullets=["News unavailable."],
                error=str(exc),
            )
    return out
