"""Stage-3 cost honesty helpers (assumed vs tip/history bid-ask).

No YAML cost flip. No Replay. No Jetta remesh.
Samples tip OHLCV (usually mid-only) and data/history Dukascopy BA when present.
"""
from __future__ import annotations

import csv
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from forex_lab.config_loader import load_config, pip_size_for_pair
from forex_lab.paths import project_root, resolve_under_root
from forex_lab.ui.watchlist import active_pair, load_watchlist

DHAKA = ZoneInfo("Asia/Dhaka")
DEFAULT_SAMPLE_N = 200
ASSUMED_SPREAD_PIPS = 1.0
ASSUMED_SLIPPAGE_PIPS = 0.2


def assumed_from_cfg(cfg: dict[str, Any] | None = None) -> dict[str, float]:
    """Research assumed costs. Read-only; never writes YAML."""
    cfg = cfg or {}
    spread = float(cfg.get("spread_pips", ASSUMED_SPREAD_PIPS) or ASSUMED_SPREAD_PIPS)
    replay = dict(cfg.get("replay") or {})
    slip = float(replay.get("slippage_pips", ASSUMED_SLIPPAGE_PIPS) or ASSUMED_SLIPPAGE_PIPS)
    # Mid-only RT: spread_pips (RT) + slippage each side on entry+exit.
    rt = spread + (2.0 * slip if bool(replay.get("exit_slippage", True)) else slip)
    return {
        "assumed_spread_pips": spread,
        "assumed_slippage_pips": slip,
        "assumed_cost_pips_rt": round(rt, 6),
        "assumed_cost_pips_one_way": round(spread / 2.0 + slip, 6),
    }


def enrich_assumed_cost_fields(
    *,
    bid: float | None,
    ask: float | None,
    pair: str = "",
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail-soft enrichment for shadow journal rows.

    When Bid+Ask present: ba_available=True and measured_spread_pips set;
    assumed_cost_pips left null (BBO is the spread per research policy).
    When BA missing: stamp assumed_* fields used by mid-only PaperBroker fills.
    """
    cfg = cfg or {}
    assumed = assumed_from_cfg(cfg)
    ba_ok = (
        bid is not None
        and ask is not None
        and math.isfinite(bid)
        and math.isfinite(ask)
        and ask >= bid
    )
    out: dict[str, Any] = {
        "ba_available": bool(ba_ok),
        "assumed_spread_pips": assumed["assumed_spread_pips"],
        "assumed_slippage_pips": assumed["assumed_slippage_pips"],
        "assumed_cost_pips": None,
        "measured_spread_pips": None,
    }
    if ba_ok:
        pip = pip_size_for_pair(pair or "EURUSD", cfg)
        if pip > 0:
            out["measured_spread_pips"] = round((float(ask) - float(bid)) / pip, 6)
        # With BBO, research policy: do NOT also charge spread_pips; slip still applies.
        out["assumed_cost_pips"] = None
        out["notes_cost"] = "ba_present_bbo_is_spread"
    else:
        out["assumed_cost_pips"] = assumed["assumed_cost_pips_rt"]
        out["notes_cost"] = "ba_missing_mid_only_assumed_rt"
    return out


def _f(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _read_csv_tail(path: Path, n: int) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        return [], []
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if n > 0 and len(rows) > n:
        rows = rows[-n:]
    return fieldnames, rows


def _ba_from_row(row: dict[str, str]) -> tuple[float | None, float | None]:
    # Prefer explicit Bid/Ask; else BidClose/AskClose (Duka history).
    bid = _f(row.get("Bid"))
    ask = _f(row.get("Ask"))
    if bid is None:
        bid = _f(row.get("BidClose"))
    if ask is None:
        ask = _f(row.get("AskClose"))
    if bid is not None and ask is not None and ask >= bid:
        return bid, ask
    return None, None


@dataclass
class PairCostSample:
    pair: str
    role: str  # active | watchlist
    tip_path: str
    hist_path: str
    tip_exists: bool
    hist_exists: bool
    tip_bars_sampled: int
    tip_ba_bars: int
    tip_mid_only: bool
    tip_last_dt: str | None
    hist_bars_sampled: int
    hist_ba_bars: int
    hist_spread_pips_median: float | None
    hist_spread_pips_mean: float | None
    hist_spread_pips_p10: float | None
    hist_spread_pips_p90: float | None
    hist_spread_unique_rounded: int | None
    hist_spread_looks_constant_stamp: bool
    hist_last_dt: str | None
    pre_stamp_bars: int
    pre_stamp_median_pips: float | None
    assumed_spread_pips: float
    assumed_slippage_pips: float
    assumed_cost_pips_rt: float
    vs_assumed_one_way: str | None  # optimistic | pessimistic | mixed | n/a
    blunt: str


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def _spreads_pips(rows: list[dict[str, str]], pip: float) -> list[float]:
    out: list[float] = []
    if pip <= 0:
        return out
    for row in rows:
        bid, ask = _ba_from_row(row)
        if bid is None or ask is None:
            continue
        out.append((ask - bid) / pip)
    return out


def _verdict(median: float | None, assumed_one_way: float) -> str | None:
    if median is None:
        return None
    # measured one-way vs assumed half-spread + slip is a different metric;
    # compare measured one-way to assumed_spread/2 (the mid-fill half RT).
    half = assumed_one_way  # caller passes assumed_spread/2
    if median < half * 0.85:
        return "assumed_pessimistic_vs_tip"  # tip tighter than assumed
    if median > half * 1.15:
        return "assumed_optimistic_vs_tip"  # tip wider than assumed
    return "assumed_roughly_in_line"


def sample_pair(
    pair: str,
    *,
    role: str,
    cfg: dict[str, Any],
    sample_n: int = DEFAULT_SAMPLE_N,
    root: Path | None = None,
) -> PairCostSample:
    root = root or project_root()
    pair = str(pair).upper()
    assumed = assumed_from_cfg(cfg)
    pip = pip_size_for_pair(pair, cfg)
    tip_path = root / "data" / f"{pair}_1h.csv"
    hist_path = root / "data" / "history" / f"{pair}_1h.csv"

    tip_fields, tip_rows = _read_csv_tail(tip_path, sample_n)
    tip_spreads = _spreads_pips(tip_rows, pip)
    tip_ba = len(tip_spreads)
    tip_last = tip_rows[-1].get("Datetime") if tip_rows else None

    hist_fields, hist_rows = _read_csv_tail(hist_path, sample_n)
    hist_spreads = _spreads_pips(hist_rows, pip)
    hist_last = hist_rows[-1].get("Datetime") if hist_rows else None

    med = statistics.median(hist_spreads) if hist_spreads else None
    mean = statistics.mean(hist_spreads) if hist_spreads else None
    hist_sorted = sorted(hist_spreads)
    p10 = _percentile(hist_sorted, 0.10)
    p90 = _percentile(hist_sorted, 0.90)
    rounded = {round(x, 4) for x in hist_spreads} if hist_spreads else set()
    constant = bool(hist_spreads) and len(rounded) <= 2

    # Pre-stamp window: bars before 2026-09 (Jetta/tip-vol constant stamp era).
    pre_med = None
    pre_n = 0
    if hist_path.is_file():
        _, all_hist = _read_csv_tail(hist_path, 0)
        pre_rows = [
            r
            for r in all_hist
            if str(r.get("Datetime") or "") < "2026-09-01"
        ][-sample_n:]
        pre_spreads = _spreads_pips(pre_rows, pip)
        pre_n = len(pre_spreads)
        if pre_spreads:
            pre_med = statistics.median(pre_spreads)

    half_assumed = assumed["assumed_spread_pips"] / 2.0
    # Prefer pre-stamp median for honesty verdict when recent looks stamped.
    compare_med = pre_med if (constant and pre_med is not None) else med
    verdict = _verdict(compare_med, half_assumed)

    blunt_bits = []
    if not tip_path.is_file():
        blunt_bits.append("tip CSV missing")
    elif tip_ba == 0:
        blunt_bits.append("tip mid-only (Bid/Ask absent)")
    else:
        blunt_bits.append(f"tip BA on {tip_ba}/{len(tip_rows)} bars")
    if not hist_path.is_file():
        blunt_bits.append("history BA missing")
    elif not hist_spreads:
        blunt_bits.append("history has no usable Bid/Ask")
    else:
        blunt_bits.append(
            f"hist one-way med={med:.4f}p"
            + (" CONSTANT-STAMP" if constant else f" (uniq={len(rounded)})")
        )
        if pre_med is not None:
            blunt_bits.append(f"pre-2026-09 med={pre_med:.4f}p")
    if verdict:
        blunt_bits.append(verdict)

    return PairCostSample(
        pair=pair,
        role=role,
        tip_path=str(tip_path.relative_to(root)).replace("\\", "/"),
        hist_path=str(hist_path.relative_to(root)).replace("\\", "/")
        if hist_path.exists()
        else str((Path("data") / "history" / f"{pair}_1h.csv")).replace("\\", "/"),
        tip_exists=tip_path.is_file(),
        hist_exists=hist_path.is_file(),
        tip_bars_sampled=len(tip_rows),
        tip_ba_bars=tip_ba,
        tip_mid_only=(tip_ba == 0 and len(tip_rows) > 0),
        tip_last_dt=str(tip_last) if tip_last else None,
        hist_bars_sampled=len(hist_rows),
        hist_ba_bars=len(hist_spreads),
        hist_spread_pips_median=round(med, 6) if med is not None else None,
        hist_spread_pips_mean=round(mean, 6) if mean is not None else None,
        hist_spread_pips_p10=round(p10, 6) if p10 is not None else None,
        hist_spread_pips_p90=round(p90, 6) if p90 is not None else None,
        hist_spread_unique_rounded=len(rounded) if hist_spreads else None,
        hist_spread_looks_constant_stamp=constant,
        hist_last_dt=str(hist_last) if hist_last else None,
        pre_stamp_bars=pre_n,
        pre_stamp_median_pips=round(pre_med, 6) if pre_med is not None else None,
        assumed_spread_pips=assumed["assumed_spread_pips"],
        assumed_slippage_pips=assumed["assumed_slippage_pips"],
        assumed_cost_pips_rt=assumed["assumed_cost_pips_rt"],
        vs_assumed_one_way=verdict,
        blunt="; ".join(blunt_bits),
    )


def run_cost_probe(
    *,
    sample_n: int = DEFAULT_SAMPLE_N,
    cfg: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    root = root or project_root()
    cfg = cfg or load_config()
    assumed = assumed_from_cfg(cfg)
    wl = load_watchlist()
    active = active_pair(wl).upper() if active_pair(wl) else ""
    symbols = []
    for s in wl.pair_symbols():
        key = str(s).upper()
        if key and key not in symbols:
            symbols.append(key)
    if active and active not in symbols:
        symbols.insert(0, active)

    pairs: list[dict[str, Any]] = []
    for sym in symbols:
        role = "active" if sym == active else "watchlist"
        sample = sample_pair(sym, role=role, cfg=cfg, sample_n=sample_n, root=root)
        pairs.append(asdict(sample))

    tip_mid_only = sum(1 for p in pairs if p.get("tip_mid_only"))
    hist_ba = sum(1 for p in pairs if (p.get("hist_ba_bars") or 0) > 0)
    stamped = sum(1 for p in pairs if p.get("hist_spread_looks_constant_stamp"))
    optimistic = sum(1 for p in pairs if p.get("vs_assumed_one_way") == "assumed_optimistic_vs_tip")
    pessimistic = sum(
        1 for p in pairs if p.get("vs_assumed_one_way") == "assumed_pessimistic_vs_tip"
    )

    now = datetime.now(timezone.utc)
    dhaka = now.astimezone(DHAKA)
    summary = {
        "pairs_sampled": len(pairs),
        "active": active or None,
        "tip_mid_only_pairs": tip_mid_only,
        "history_ba_pairs": hist_ba,
        "history_constant_stamp_pairs": stamped,
        "verdict_assumed_optimistic_count": optimistic,
        "verdict_assumed_pessimistic_count": pessimistic,
        "blunt_headline": (
            "Tip path is mid-only for every sampled Active/watchlist pair; "
            "shadow journal BA stays null on the live tip path. "
            f"Vs assumed half-RT (0.5p): pessimistic={pessimistic} "
            f"optimistic={optimistic} (pair-level, prefer pre-2026-09 median when "
            "recent history looks constant-stamped). "
            "EURUSD/USDJPY: assumed slightly pessimistic (pre-stamp ~0.4p < 0.5p). "
            "Most crosses/minors: assumed OPTIMISTIC (pre-stamp one-way often 0.6-1.5p). "
            f"Recent history constant-stamp pairs={stamped}/{hist_ba} - not live BBO. "
            "Assumed RT mid-only charge is 1.4p (1.0 + 2*0.2). No YAML cost flip."
        ),
    }

    return {
        "schema": "cost_honesty_probe_v1",
        "ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_dhaka": dhaka.strftime("%Y-%m-%d %H:%M:%S Asia/Dhaka"),
        "sample_n": sample_n,
        "assumed": assumed,
        "summary": summary,
        "pairs": pairs,
        "non_goals_honored": [
            "no_jetta_remesh",
            "no_yaml_cost_edit",
            "no_replay",
            "no_promote_gate_min_conf_session_portfolio_exit_hold",
        ],
    }


def render_result_text(report: dict[str, Any]) -> str:
    assumed = report.get("assumed") or {}
    summary = report.get("summary") or {}
    lines = [
        f"# Cost honesty result (Stage-3) - {report.get('ts_dhaka')}",
        f"Schema: {report.get('schema')}",
        "",
        "## Assumed research costs (read-only; YAML untouched)",
        f"- spread_pips (RT): {assumed.get('assumed_spread_pips')}",
        f"- slippage_pips / side: {assumed.get('assumed_slippage_pips')}",
        f"- assumed RT mid-only (spread + 2*slip): {assumed.get('assumed_cost_pips_rt')}",
        f"- assumed one-way half-spread: {float(assumed.get('assumed_spread_pips') or 0)/2:.3f}",
        "",
        "## Headline",
        str(summary.get("blunt_headline") or ""),
        "",
        f"Active={summary.get('active')} | pairs={summary.get('pairs_sampled')} | "
        f"tip_mid_only={summary.get('tip_mid_only_pairs')} | "
        f"hist_BA={summary.get('history_ba_pairs')} | "
        f"constant_stamp={summary.get('history_constant_stamp_pairs')} | "
        f"assumed_optimistic={summary.get('verdict_assumed_optimistic_count')} | "
        f"assumed_pessimistic={summary.get('verdict_assumed_pessimistic_count')}",
        "",
        "## Pair table (one-way pips)",
        "| pair | role | tip BA | hist med | pre-09 med | stamp? | vs assumed half-RT | blunt |",
        "|---|---|---:|---:|---:|---|---|---|",
    ]
    half = float(assumed.get("assumed_spread_pips") or 1.0) / 2.0
    for p in report.get("pairs") or []:
        tip_ba = p.get("tip_ba_bars") or 0
        tip_n = p.get("tip_bars_sampled") or 0
        med = p.get("hist_spread_pips_median")
        pre = p.get("pre_stamp_median_pips")
        med_s = f"{med:.4f}" if isinstance(med, (int, float)) else "n/a"
        pre_s = f"{pre:.4f}" if isinstance(pre, (int, float)) else "n/a"
        stamp = "YES" if p.get("hist_spread_looks_constant_stamp") else "no"
        lines.append(
            f"| {p.get('pair')} | {p.get('role')} | {tip_ba}/{tip_n} | {med_s} | {pre_s} | "
            f"{stamp} | {p.get('vs_assumed_one_way') or 'n/a'} | {p.get('blunt')} |"
        )
    lines += [
        "",
        f"Assumed half-RT reference = {half:.3f} pip one-way (spread_pips/2). "
        "Slippage 0.2/side is extra on mid-only fills and still applies with BBO.",
        "",
        "## Interpretation (blunt)",
        "1) Live tip CSVs under data/*_1h.csv have NO Bid/Ask - shadow journal BA stays null.",
        "2) data/history/*_1h.csv carries Duka BidClose/AskClose; recent months often look",
        "   CONSTANT-STAMPED after tip-vol/Jetta work - not trustworthy as live BBO.",
        "3) Pre-2026-09 last-N medians: EURUSD/USDJPY ~0.4p one-way => assumed 0.5p half-RT",
        "   is slightly PESSIMISTIC (good caution). Older deep history often ~0.2-0.3p.",
        "4) Most crosses/minors (AUD/NZD/CAD/CHF/JPY crosses, GBPUSD) pre-stamp one-way",
        "   0.6-1.5p => assumed 1.0 RT is OPTIMISTIC vs tip/history BBO - Replay PF may",
        "   overstate live edge until Stage-3 restates after-cost book with measured cost.",
        "5) No YAML cost flip, no Replay re-pin, no Jetta remesh from this probe.",
        "",
        "Standing: desk remains a decision aid until Stages 2-4 clear.",
        "",
    ]
    return "\n".join(lines)


def write_probe_artifacts(
    report: dict[str, Any] | None = None,
    *,
    sample_n: int = DEFAULT_SAMPLE_N,
) -> tuple[Path, Path]:
    import json

    report = report or run_cost_probe(sample_n=sample_n)
    day = str(report.get("ts_dhaka") or "")[:10].replace("-", "")
    if len(day) != 8:
        day = datetime.now(DHAKA).strftime("%Y%m%d")
    out_dir = resolve_under_root(Path("data") / "paper_shadow")
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"cost_probe_{day}.json"
    txt_path = project_root() / f"_COST_HONESTY_RESULT_{day}.txt"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    txt_path.write_text(render_result_text(report), encoding="utf-8")
    return json_path, txt_path


__all__ = [
    "ASSUMED_SLIPPAGE_PIPS",
    "ASSUMED_SPREAD_PIPS",
    "assumed_from_cfg",
    "enrich_assumed_cost_fields",
    "render_result_text",
    "run_cost_probe",
    "sample_pair",
    "write_probe_artifacts",
]
