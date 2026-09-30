"""Patch forex_lab/history.py + cli for Jetta primary history fetch."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(r"C:\AI\forex-lab")
hist = (ROOT / "forex_lab" / "history.py").read_text(encoding="utf-8")
cli = (ROOT / "forex_lab" / "cli.py").read_text(encoding="utf-8")

if "SOURCE_JETTA" in hist:
    print("history.py already patched")
else:
    hist = hist.replace(
        'SOURCE_DUKA = "dukascopy"\nSOURCE_HIST = "histdata"\n',
        'SOURCE_DUKA = "dukascopy"\nSOURCE_HIST = "histdata"\nSOURCE_JETTA = "jetta"\n'
        "\n"
        "# Median (Jetta Volume / Dukascopy tick Volume) on same-timestamp EURUSD overlap\n"
        "# (n=7110, 2022-02..2024-09); see _JETTA_VOLUME_SCALE_20260930.txt.\n"
        "JETTA_TO_DUKA_VOLUME_FACTOR = 2.812045180934712\n"
        '_JETTA_BASE = "https://jetta.dukascopy.com/v1/candles/trade/hour"\n',
    )

    # Update module docstring slightly
    hist = hist.replace(
        "Primary source is the public Dukascopy tick feed (bi5).",
        "Primary source is Jetta REST hour candles (public, no login);\n"
        "classic Dukascopy bi5 ticks are the optional fallback when reachable.",
    )

    jetta_block = r'''
def jetta_instrument(pair: str) -> str:
    """Map EURUSD -> EUR-USD for the public Jetta REST path."""
    p = str(pair).upper().replace("/", "").replace("-", "")
    if len(p) != 6:
        raise HistoryError(f"Jetta needs a 6-letter FX pair (got {pair!r})")
    return f"{p[:3]}-{p[3:]}"


def decode_jetta_side(payload: dict[str, Any]) -> dict[pd.Timestamp, tuple[float, float, float, float, float]]:
    """Decode one Jetta candle side.

    Later bars' o/h/l/c are deltas from the **previous close** times ``multiplier``.
    The first bar uses the top-level open/high/low/close absolutes. Wrong bases
    (e.g. previous open) produce Ask drift and High<Low — keep this decode.
    """
    if not isinstance(payload, dict):
        raise HistoryError("Jetta payload is not a JSON object", reason="decode")
    try:
        mult = float(payload["multiplier"])
        shift = int(payload.get("shift") or 60_000)
        t0 = int(payload["timestamp"])
        times = payload["times"]
        opens = payload["opens"]
        highs = payload["highs"]
        lows = payload["lows"]
        closes = payload["closes"]
        volumes = payload["volumes"]
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoryError(f"Jetta payload missing fields ({exc})", reason="decode") from exc
    n = len(times)
    if n <= 0:
        return {}
    out: dict[pd.Timestamp, tuple[float, float, float, float, float]] = {}
    t = t0
    prev: float | None = None
    for i in range(n):
        t = t0 + int(times[i]) * shift if i == 0 else t + int(times[i]) * shift
        if i == 0:
            o = float(payload["open"])
            h = float(payload["high"])
            low = float(payload["low"])
            c = float(payload["close"])
        else:
            assert prev is not None
            o = prev + float(opens[i]) * mult
            h = prev + float(highs[i]) * mult
            low = prev + float(lows[i]) * mult
            c = prev + float(closes[i]) * mult
        hi = max(h, o, c)
        lo = min(low, o, c)
        ts = pd.Timestamp(datetime.fromtimestamp(t / 1000.0, tz=timezone.utc)).tz_convert(None)
        ts = ts.replace(minute=0, second=0, microsecond=0)
        out[ts] = (float(o), float(hi), float(lo), float(c), float(volumes[i]))
        prev = float(c)
    return out


def jetta_month_to_bars(
    bid_payload: dict[str, Any],
    ask_payload: dict[str, Any],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    scale_volume: bool = True,
) -> pd.DataFrame:
    """Combine BID+ASK Jetta month payloads into mid+bid/ask H1 bars."""
    bid = decode_jetta_side(bid_payload)
    ask = decode_jetta_side(ask_payload)
    rows: list[dict[str, Any]] = []
    start_ts = pd.Timestamp(start) if start is not None else None
    end_ts = pd.Timestamp(end) if end is not None else None
    vol_div = float(JETTA_TO_DUKA_VOLUME_FACTOR) if scale_volume else 1.0
    for ts in sorted(set(bid) & set(ask)):
        if start_ts is not None and ts < start_ts:
            continue
        if end_ts is not None and ts > end_ts:
            continue
        bo, bh, bl, bc, bv = bid[ts]
        ao, ah, al, ac, av = ask[ts]
        if ac < bc:
            ao, ah, al, ac, bo, bh, bl, bc = bo, bh, bl, bc, ao, ah, al, ac
        vol = (float(bv) + float(av)) / 2.0 / vol_div
        rows.append(
            {
                "Datetime": ts,
                "Open": (bo + ao) / 2.0,
                "High": (bh + ah) / 2.0,
                "Low": (bl + al) / 2.0,
                "Close": (bc + ac) / 2.0,
                "Volume": float(max(0.0, round(vol))),
                "BidOpen": bo,
                "BidHigh": bh,
                "BidLow": bl,
                "BidClose": bc,
                "AskOpen": ao,
                "AskHigh": ah,
                "AskLow": al,
                "AskClose": ac,
                "Spread": float(max(0.0, ac - bc)),
            }
        )
    if not rows:
        return _empty_bars()
    frame = pd.DataFrame(rows).set_index("Datetime").sort_index()
    frame.index.name = "Datetime"
    return frame


def _download_jetta_json(url: str) -> dict[str, Any]:
    last: BaseException | None = None
    for attempt in range(4):
        try:
            res = _http_client().get(url)
            if res.status_code == 400:
                # Incomplete / future month — treat as empty, not hard fail.
                return {}
            if res.status_code == 404:
                return {}
            if res.status_code in (429, 503):
                raise HistoryError(f"Jetta HTTP {res.status_code} for {url}", reason="download")
            if res.status_code == 200 and res.content:
                data = res.json()
                if isinstance(data, dict):
                    return data
                raise HistoryError(f"Jetta non-object JSON for {url}", reason="decode")
            last = HistoryError(f"Jetta HTTP {res.status_code} for {url}", reason="download")
        except HistoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.35 * (attempt + 1))
    if last is not None:
        detail = " ".join(str(last).split()).strip() or last.__class__.__name__
        raise HistoryError(f"Jetta unreachable for {url}: {detail}", reason="download")
    return {}


def fetch_jetta_month(
    pair: str,
    year: int,
    month: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    scale_volume: bool = True,
    fetch_json: Callable[[str], dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Pull one calendar month of H1 BID+ASK from public Jetta REST."""
    instr = jetta_instrument(pair)
    base = f"{_JETTA_BASE}/{instr}"
    getter = fetch_json or _download_jetta_json
    bid = getter(f"{base}/BID/{year}/{month}")
    ask = getter(f"{base}/ASK/{year}/{month}")
    if not bid or not ask:
        return _empty_bars()
    return jetta_month_to_bars(bid, ask, start=start, end=end, scale_volume=scale_volume)


def _pull_jetta(
    pair: str,
    grain: str,
    start: datetime,
    end: datetime,
    existing: pd.DataFrame,
    cfg: dict[str, Any] | None,
    *,
    progress: ProgressFn | None,
    fetch_json: Callable[[str], dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Fill missing H1 bars via Jetta monthly candles. Grain must be 1h."""
    if grain != GRAIN_1H:
        raise HistoryError(
            f"Jetta history supports 1h grain only (got {grain}); 15m stays on bi5/HistData",
            reason="download",
        )
    months = _iter_months(start, end)
    if not months:
        raise HistoryError("Jetta range is empty", reason="insufficient_bars")
    have: set[pd.Timestamp] = set()
    if existing is not None and not existing.empty:
        have = set(pd.DatetimeIndex(existing.index).floor("h"))
    saved = existing if existing is not None and not existing.empty else _empty_bars()
    _emit(
        progress,
        phase="pull",
        fraction=0.0,
        message=f"Jetta {pair} {len(months)} months",
        rows=len(saved),
    )
    errors = 0
    causes: list[BaseException] = []
    for i, (year, month) in enumerate(months, start=1):
        try:
            fresh = fetch_jetta_month(
                pair,
                year,
                month,
                start=start,
                end=end,
                scale_volume=True,
                fetch_json=fetch_json,
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            if len(causes) < 3:
                causes.append(exc)
            _emit(
                progress,
                phase="pull",
                fraction=i / len(months),
                message=f"Jetta {pair} {year}-{month:02d} failed: {exc}",
                rows=len(saved),
            )
            continue
        if fresh is not None and not fresh.empty:
            if have:
                fresh = fresh.loc[~fresh.index.floor("h").isin(have)]
            if not fresh.empty:
                saved = _merge(saved, fresh)
                have |= set(pd.DatetimeIndex(fresh.index).floor("h"))
                _save_bars(pair, grain, saved, SOURCE_JETTA, cfg)
        _emit(
            progress,
            phase="pull",
            fraction=i / len(months),
            message=f"Jetta {pair} {year}-{month:02d} ({i}/{len(months)}) bars={len(saved)}",
            rows=len(saved),
        )
        time.sleep(0.15)
    if saved.empty and errors:
        first = causes[0] if causes else None
        detail = ""
        if first is not None:
            detail = " ".join(str(first).split()).strip() or first.__class__.__name__
        raise HistoryError(
            f"Jetta {pair}: {detail} ({errors} months failed, 0 bars)".strip(),
            reason="download",
        )
    if not saved.empty:
        _save_bars(pair, grain, saved, SOURCE_JETTA, cfg)
    return saved


'''

    anchor = "\ndef _pull_dukascopy(\n"
    if anchor not in hist:
        raise SystemExit("anchor _pull_dukascopy not found")
    hist = hist.replace(anchor, "\n" + jetta_block + anchor, 1)

    # Rewrite pull_history source handling
    old_which = '''    which = str(source or rc.get("source") or "auto").strip().lower()
    if which not in {"auto", SOURCE_DUKA, SOURCE_HIST}:
        raise HistoryError(f"unknown history source {which!r}")

    existing = load_history_csv(history_path(pair_u, grain, cfg))
    if covers_range(existing, start_dt, end_dt) and which != SOURCE_HIST:
        frame = _finalize(existing, iv, pair_u, str(load_meta(pair_u, grain, cfg).get("source") or SOURCE_DUKA), cfg)
        _emit(progress, phase="pull", fraction=1.0, message=f"{pair_u} {iv} cache already covers the range", rows=len(frame))
        return _result(pair_u, iv, frame, str(load_meta(pair_u, grain, cfg).get("source") or SOURCE_DUKA), cached=True, cfg=cfg)

    used = SOURCE_DUKA
    grain_df = existing
    if which in {"auto", SOURCE_DUKA}:
        try:
            grain_df = _pull_dukascopy(
                pair_u,
                grain,
                start_dt,
                end_dt,
                existing,
                cfg,
                workers=int(rc.get("workers") or 8),
                progress=progress,
                fetch_hour=fetch_hour,
            )
        except HistoryError:
            if which == SOURCE_DUKA:
                raise
            grain_df = existing
        if grain_df is not None and not grain_df.empty and (covers_range(grain_df, start_dt, end_dt) or which == SOURCE_DUKA):
            used = SOURCE_DUKA
        elif which == SOURCE_DUKA:
            raise HistoryError(
                f"Dukascopy returned no {pair_u} bars for {start_dt.date()} -> {end_dt.date()}",
                reason="insufficient_bars",
            )
        elif grain_df is None or grain_df.empty or not covers_range(grain_df, start_dt, end_dt):
            _emit(progress, phase="pull", message=f"Dukascopy incomplete for {pair_u}; trying HistData", fraction=0.0)
            grain_df = _pull_histdata(
                pair_u,
                grain,
                start_dt,
                end_dt,
                cfg,
                progress=progress,
                fetch_month=fetch_histdata_month,
            )
            used = SOURCE_HIST
    else:
        grain_df = _pull_histdata(
            pair_u,
            grain,
            start_dt,
            end_dt,
            cfg,
            progress=progress,
            fetch_month=fetch_histdata_month,
        )
        used = SOURCE_HIST
'''

    new_which = '''    which = str(source or rc.get("source") or "auto").strip().lower()
    if which not in {"auto", SOURCE_DUKA, SOURCE_HIST, SOURCE_JETTA}:
        raise HistoryError(f"unknown history source {which!r}")

    existing = load_history_csv(history_path(pair_u, grain, cfg))
    if covers_range(existing, start_dt, end_dt) and which not in {SOURCE_HIST}:
        frame = _finalize(existing, iv, pair_u, str(load_meta(pair_u, grain, cfg).get("source") or SOURCE_DUKA), cfg)
        _emit(progress, phase="pull", fraction=1.0, message=f"{pair_u} {iv} cache already covers the range", rows=len(frame))
        return _result(pair_u, iv, frame, str(load_meta(pair_u, grain, cfg).get("source") or SOURCE_DUKA), cached=True, cfg=cfg)

    used = SOURCE_DUKA
    grain_df = existing

    def _try_jetta(base: pd.DataFrame) -> pd.DataFrame:
        if grain != GRAIN_1H:
            raise HistoryError("Jetta requires 1h grain", reason="download")
        return _pull_jetta(
            pair_u,
            grain,
            start_dt,
            end_dt,
            base,
            cfg,
            progress=progress,
            fetch_json=None,
        )

    def _try_duka(base: pd.DataFrame) -> pd.DataFrame:
        return _pull_dukascopy(
            pair_u,
            grain,
            start_dt,
            end_dt,
            base,
            cfg,
            workers=int(rc.get("workers") or 8),
            progress=progress,
            fetch_hour=fetch_hour,
        )

    if which == SOURCE_JETTA:
        grain_df = _try_jetta(existing)
        used = SOURCE_JETTA
        if grain_df is None or grain_df.empty:
            raise HistoryError(
                f"Jetta returned no {pair_u} bars for {start_dt.date()} -> {end_dt.date()}",
                reason="insufficient_bars",
            )
    elif which == SOURCE_DUKA:
        grain_df = _try_duka(existing)
        used = SOURCE_DUKA
        if grain_df is None or grain_df.empty:
            raise HistoryError(
                f"Dukascopy returned no {pair_u} bars for {start_dt.date()} -> {end_dt.date()}",
                reason="insufficient_bars",
            )
    elif which == SOURCE_HIST:
        grain_df = _pull_histdata(
            pair_u,
            grain,
            start_dt,
            end_dt,
            cfg,
            progress=progress,
            fetch_month=fetch_histdata_month,
        )
        used = SOURCE_HIST
    else:
        # auto: Jetta first for 1h grain (PC CDN to classic bi5 is often dead),
        # then classic Dukascopy bi5, then HistData.
        jetta_ok = False
        if grain == GRAIN_1H:
            try:
                grain_df = _try_jetta(existing)
                if grain_df is not None and not grain_df.empty and covers_range(grain_df, start_dt, end_dt):
                    used = SOURCE_JETTA
                    jetta_ok = True
                elif grain_df is not None and not grain_df.empty:
                    used = SOURCE_JETTA
                    # keep partial; try bi5 to fill remaining holes
                    existing = grain_df
            except HistoryError as exc:
                _emit(progress, phase="pull", message=f"Jetta failed for {pair_u}: {exc}; trying Dukascopy bi5", fraction=0.0)
                grain_df = existing
        if not jetta_ok:
            try:
                grain_df = _try_duka(existing)
            except HistoryError as exc:
                _emit(progress, phase="pull", message=f"Dukascopy bi5 failed for {pair_u}: {exc}", fraction=0.0)
                grain_df = existing
            if grain_df is not None and not grain_df.empty and covers_range(grain_df, start_dt, end_dt):
                if used != SOURCE_JETTA:
                    used = SOURCE_DUKA
            elif grain_df is not None and not grain_df.empty and used == SOURCE_JETTA:
                pass  # keep jetta partial
            elif grain_df is not None and not grain_df.empty:
                used = SOURCE_DUKA
            else:
                _emit(progress, phase="pull", message=f"Dukascopy incomplete for {pair_u}; trying HistData", fraction=0.0)
                grain_df = _pull_histdata(
                    pair_u,
                    grain,
                    start_dt,
                    end_dt,
                    cfg,
                    progress=progress,
                    fetch_month=fetch_histdata_month,
                )
                used = SOURCE_HIST
'''

    if old_which not in hist:
        raise SystemExit("old pull_history block not found")
    hist = hist.replace(old_which, new_which, 1)

    # Docstring for pull_history
    hist = hist.replace(
        "``fetch_hour`` / ``fetch_histdata_month`` are test seams. Production uses\n"
        "    Dukascopy, then HistData when ``source`` is ``auto`` and Dukascopy is empty.",
        "``fetch_hour`` / ``fetch_histdata_month`` are test seams. Production ``auto``\n"
        "    prefers Jetta H1 candles, then classic Dukascopy bi5, then HistData.",
    )

    bak = ROOT / "forex_lab" / "history.py.bak_pre_jetta_20261001"
    bak.write_text(Path(ROOT / "forex_lab" / "history.py").read_text(encoding="utf-8"), encoding="utf-8")
    (ROOT / "forex_lab" / "history.py").write_text(hist, encoding="utf-8")
    print("patched history.py bytes", len(hist))

# CLI source choices
if '"jetta"' not in cli:
    cli2 = cli.replace(
        'h.add_argument("--source", default="auto", choices=["auto", "dukascopy", "histdata"])',
        'h.add_argument("--source", default="auto", choices=["auto", "jetta", "dukascopy", "histdata"])',
    )
    cli2 = cli2.replace(
        'rp.add_argument("--source", default="auto", choices=["auto", "dukascopy", "histdata"])',
        'rp.add_argument("--source", default="auto", choices=["auto", "jetta", "dukascopy", "histdata"])',
    )
    cli2 = cli2.replace(
        "Pull Dukascopy (or HistData) OHLC into data/history. Does not write synthetic prices.",
        "Pull Jetta / Dukascopy / HistData OHLC into data/history. Does not write synthetic prices.",
    )
    cli2 = cli2.replace(
        "help=\"Pull one pair of Dukascopy tick->OHLC (HistData fallback) into data/history (gitignored)\"",
        "help=\"Pull one pair of Jetta/Dukascopy OHLC (HistData fallback) into data/history (gitignored)\"",
    )
    (ROOT / "forex_lab" / "cli.py").write_text(cli2, encoding="utf-8")
    print("patched cli.py")
else:
    print("cli already has jetta")

print("OK")
