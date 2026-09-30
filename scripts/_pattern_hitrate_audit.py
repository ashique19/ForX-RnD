"""Pattern hit-rate audit vs pin Replay after-cost trades (research overlay only).

Ports desk/src/patterns.ts candle + structure detectors (causal). Joins hits to
Replay trades.csv from a finished job. Does NOT touch Buy/Sell gates or promote.

  python scripts/_pattern_hitrate_audit.py
  python scripts/_pattern_hitrate_audit.py --job 2cee7e7163134266ad8c2b39234442d8
"""
from __future__ import annotations

import argparse
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PATTERN_IDS = (
    "engulfing",
    "doji",
    "hammer",
    "shooting_star",
    "inside_bar",
    "head_shoulders",
    "inv_head_shoulders",
    "double_top",
    "double_bottom",
    "triangle",
)


@dataclass(frozen=True)
class Hit:
    i: int
    time: pd.Timestamp
    pid: str
    side: str  # bull | bear | neutral


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _range(h: float, l: float) -> float:
    return max(h - l, 1e-12)


def _pivots(high: list[float], low: list[float], times: list[pd.Timestamp], left: int = 3, right: int = 3):
    highs: list[tuple[int, float, pd.Timestamp]] = []
    lows: list[tuple[int, float, pd.Timestamp]] = []
    n = len(high)
    for i in range(left, n - right):
        wh = high[i - left : i + right + 1]
        wl = low[i - left : i + right + 1]
        if high[i] >= max(wh) and high[i] > high[i - 1] and high[i] >= high[i + 1]:
            highs.append((i, high[i], times[i]))
        if low[i] <= min(wl) and low[i] < low[i - 1] and low[i] <= low[i + 1]:
            lows.append((i, low[i], times[i]))
    return highs, lows


def _near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _extreme_between(high: list[float], low: list[float], times: list[pd.Timestamp], a: int, b: int, mode: str):
    lo, hi = (a, b) if a <= b else (b, a)
    if hi - lo < 1:
        return None
    best_i = None
    best = None
    for i in range(lo + 1, hi):
        price = high[i] if mode == "high" else low[i]
        if best is None or (price > best if mode == "high" else price < best):
            best = price
            best_i = i
    if best_i is None:
        return None
    return best_i, best, times[best_i]


def detect_candles(o, h, l, c, times) -> list[Hit]:
    out: list[Hit] = []
    n = len(o)
    for i in range(n):
        rng = _range(h[i], l[i])
        bod = _body(o[i], c[i])
        up = h[i] - max(o[i], c[i])
        lo = min(o[i], c[i]) - l[i]
        bull = c[i] >= o[i]
        t = times[i]
        if bod <= rng * 0.1:
            out.append(Hit(i, t, "doji", "neutral"))
        if lo >= rng * 0.55 and up <= rng * 0.25 and bod <= rng * 0.35:
            out.append(Hit(i, t, "hammer", "bull"))
        if up >= rng * 0.55 and lo <= rng * 0.25 and bod <= rng * 0.35:
            out.append(Hit(i, t, "shooting_star", "bear"))
        if i > 0:
            prev_o, prev_c = o[i - 1], c[i - 1]
            prev_h, prev_l = h[i - 1], l[i - 1]
            prev_bod = _body(prev_o, prev_c)
            if h[i] <= prev_h and l[i] >= prev_l and (h[i] < prev_h or l[i] > prev_l):
                out.append(Hit(i, t, "inside_bar", "neutral"))
            prev_bull = prev_c >= prev_o
            engulfs = h[i] >= max(prev_o, prev_c) and l[i] <= min(prev_o, prev_c)
            if engulfs and bull and not prev_bull and bod > prev_bod:
                out.append(Hit(i, t, "engulfing", "bull"))
            elif engulfs and not bull and prev_bull and bod > prev_bod:
                out.append(Hit(i, t, "engulfing", "bear"))
    return out


def detect_structures(o, h, l, c, times) -> list[Hit]:
    """All confirming structure hits (not 'latest in window only'). Causal on pivots."""
    n = len(o)
    if n < 20:
        return []
    highs, lows = _pivots(h, l, times)
    span = max(c) - min(c)
    tol = max(span * 0.015, _range(h[-1], l[-1]) * 0.5)
    out: list[Hit] = []
    seen: set[tuple[str, int]] = set()

    def add(hit: Hit) -> None:
        key = (hit.pid, hit.i)
        if key in seen:
            return
        seen.add(key)
        out.append(hit)

    if len(highs) >= 2:
        for a in range(len(highs) - 1):
            for b in range(a + 1, len(highs)):
                p1, p2 = highs[a], highs[b]
                if p2[0] - p1[0] < 5:
                    continue
                if not _near(p1[1], p2[1], tol):
                    continue
                mid = next((x for x in lows if p1[0] < x[0] < p2[0]), None)
                if not mid or mid[1] >= min(p1[1], p2[1]) - tol * 0.2:
                    continue
                add(Hit(p2[0], p2[2], "double_top", "bear"))

    if len(lows) >= 2:
        for a in range(len(lows) - 1):
            for b in range(a + 1, len(lows)):
                p1, p2 = lows[a], lows[b]
                if p2[0] - p1[0] < 5:
                    continue
                if not _near(p1[1], p2[1], tol):
                    continue
                mid = next((x for x in highs if p1[0] < x[0] < p2[0]), None)
                if not mid or mid[1] <= max(p1[1], p2[1]) + tol * 0.2:
                    continue
                add(Hit(p2[0], p2[2], "double_bottom", "bull"))

    if len(highs) >= 3:
        for i in range(len(highs) - 2):
            ls, hd, rs = highs[i], highs[i + 1], highs[i + 2]
            if not (hd[1] > ls[1] and hd[1] > rs[1]):
                continue
            if not _near(ls[1], rs[1], tol * 1.5):
                continue
            if hd[1] - max(ls[1], rs[1]) < tol * 0.5:
                continue
            n1 = _extreme_between(h, l, times, ls[0], hd[0], "low")
            n2 = _extreme_between(h, l, times, hd[0], rs[0], "low")
            if not n1 or not n2:
                continue
            add(Hit(rs[0], rs[2], "head_shoulders", "bear"))

    if len(lows) >= 3:
        for i in range(len(lows) - 2):
            ls, hd, rs = lows[i], lows[i + 1], lows[i + 2]
            if not (hd[1] < ls[1] and hd[1] < rs[1]):
                continue
            if not _near(ls[1], rs[1], tol * 1.5):
                continue
            if min(ls[1], rs[1]) - hd[1] < tol * 0.5:
                continue
            n1 = _extreme_between(h, l, times, ls[0], hd[0], "high")
            n2 = _extreme_between(h, l, times, hd[0], rs[0], "high")
            if not n1 or not n2:
                continue
            add(Hit(rs[0], rs[2], "inv_head_shoulders", "bull"))

    # Triangle: rolling trailing window like desk (last ~120 bars), emit at window end when converging
    W = 120
    for end in range(20, n):
        start = max(0, end - W + 1)
        # local pivots on window
        wh, wl = h[start : end + 1], l[start : end + 1]
        wt = times[start : end + 1]
        ph, pl = _pivots(wh, wl, wt)
        if len(ph) < 3 or len(pl) < 3:
            continue
        rh, rl = ph[-4:], pl[-4:]
        if len(rh) < 3 or len(rl) < 3:
            continue
        hi_slope = (rh[-1][1] - rh[0][1]) / max(1, rh[-1][0] - rh[0][0])
        lo_slope = (rl[-1][1] - rl[0][1]) / max(1, rl[-1][0] - rl[0][0])
        first_w = rh[0][1] - rl[0][1]
        last_w = rh[-1][1] - rl[-1][1]
        converging = last_w > 0 and first_w > 0 and last_w < first_w * 0.72
        opposing = hi_slope < 0 and lo_slope > 0
        if converging and (opposing or abs(hi_slope) + abs(lo_slope) > 0):
            add(Hit(end, times[end], "triangle", "neutral"))

    return out


def book_metrics(pnls: list[float]) -> dict[str, Any]:
    if not pnls:
        return {
            "n": 0,
            "win_rate": float("nan"),
            "expectancy": float("nan"),
            "net_pnl": 0.0,
            "profit_factor": float("nan"),
            "max_dd": float("nan"),
            "total_return": 0.0,
        }
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else float("nan"))
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = eq - peak
        max_dd = min(max_dd, dd)
    # total_return approx as compounded on unit notionals of net_pnl path is not 1:1 with replay;
    # report sum path return as eq / 1.0 starting - same additive book as expectancy*n
    return {
        "n": len(pnls),
        "win_rate": sum(1 for p in pnls if p > 0) / len(pnls),
        "expectancy": sum(pnls) / len(pnls),
        "net_pnl": sum(pnls),
        "profit_factor": pf,
        "max_dd": max_dd,
        "total_return": eq,  # additive equity end (pnl units), not replay % equity
    }


def fmt_m(m: dict[str, Any]) -> str:
    pf = m["profit_factor"]
    pf_s = "inf" if pf == float("inf") else (f"{pf:.4f}" if pf == pf else "nan")
    wr = m["win_rate"]
    wr_s = f"{wr*100:.1f}%" if wr == wr else "nan"
    exp = m["expectancy"]
    exp_s = f"{exp:.6f}" if exp == exp else "nan"
    dd = m["max_dd"]
    dd_s = f"{dd:.4f}" if dd == dd else "nan"
    return f"n={m['n']}  WR={wr_s}  PF={pf_s}  exp={exp_s}  net={m['net_pnl']:.4f}  DD_path={dd_s}"


def side_ok(trade_side: str, pat_side: str) -> bool:
    ts = trade_side.upper()
    if pat_side == "neutral":
        return True
    if pat_side == "bull":
        return ts == "BUY"
    if pat_side == "bear":
        return ts == "SELL"
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default="2cee7e7163134266ad8c2b39234442d8")
    ap.add_argument("--book", default="champion", help="champion|challenger|both")
    ap.add_argument("--lookback", type=int, default=0, help="extra bars before entry to count pattern")
    ap.add_argument("--out", default=str(ROOT / "_PATTERN_HITRATE_AUDIT_20260930.txt"))
    ap.add_argument("--skip-triangle", action="store_true", default=True)
    args = ap.parse_args()

    from forex_lab.config_loader import load_config
    from forex_lab.history import load_history

    cfg = load_config()
    hist = load_history("EURUSD", cfg, "1h")
    # mid OHLC
    o = hist["Open"].astype(float).tolist()
    h = hist["High"].astype(float).tolist()
    l = hist["Low"].astype(float).tolist()
    c = hist["Close"].astype(float).tolist()
    times = list(pd.DatetimeIndex(hist.index).tz_localize(None))
    time_to_i = {t: i for i, t in enumerate(times)}

    print(f"[audit] history bars={len(times)}  detecting patterns...", flush=True)
    candles = detect_candles(o, h, l, c, times)
    print(f"[audit] candle hits={len(candles)}  structures...", flush=True)
    # structures without full-span triangle (triangle rolling is expensive); optional
    # For speed: detect structures but skip triangle loop unless needed
    highs, lows = _pivots(h, l, times)
    # reuse detect_structures but patch triangle off via monkey by slicing function
    # Inline non-triangle structures:
    structs: list[Hit] = []
    span = max(c) - min(c)
    tol = max(span * 0.015, _range(h[-1], l[-1]) * 0.5)
    seen: set[tuple[str, int]] = set()

    def add(hit: Hit, confirm_lag: int = 3) -> None:
        # Pivot needs right=3 future bars; attribute to confirmation bar (causal).
        ci = min(hit.i + confirm_lag, len(times) - 1)
        hit = Hit(ci, times[ci], hit.pid, hit.side)
        key = (hit.pid, hit.i)
        if key in seen:
            return
        seen.add(key)
        structs.append(hit)

    for a in range(len(highs) - 1):
        for b in range(a + 1, len(highs)):
            p1, p2 = highs[a], highs[b]
            if p2[0] - p1[0] < 5:
                continue
            if not _near(p1[1], p2[1], tol):
                continue
            mid = next((x for x in lows if p1[0] < x[0] < p2[0]), None)
            if not mid or mid[1] >= min(p1[1], p2[1]) - tol * 0.2:
                continue
            add(Hit(p2[0], p2[2], "double_top", "bear"))
    for a in range(len(lows) - 1):
        for b in range(a + 1, len(lows)):
            p1, p2 = lows[a], lows[b]
            if p2[0] - p1[0] < 5:
                continue
            if not _near(p1[1], p2[1], tol):
                continue
            mid = next((x for x in highs if p1[0] < x[0] < p2[0]), None)
            if not mid or mid[1] <= max(p1[1], p2[1]) + tol * 0.2:
                continue
            add(Hit(p2[0], p2[2], "double_bottom", "bull"))
    for i in range(len(highs) - 2):
        ls, hd, rs = highs[i], highs[i + 1], highs[i + 2]
        if not (hd[1] > ls[1] and hd[1] > rs[1]):
            continue
        if not _near(ls[1], rs[1], tol * 1.5):
            continue
        if hd[1] - max(ls[1], rs[1]) < tol * 0.5:
            continue
        if not _extreme_between(h, l, times, ls[0], hd[0], "low"):
            continue
        if not _extreme_between(h, l, times, hd[0], rs[0], "low"):
            continue
        add(Hit(rs[0], rs[2], "head_shoulders", "bear"))
    for i in range(len(lows) - 2):
        ls, hd, rs = lows[i], lows[i + 1], lows[i + 2]
        if not (hd[1] < ls[1] and hd[1] < rs[1]):
            continue
        if not _near(ls[1], rs[1], tol * 1.5):
            continue
        if min(ls[1], rs[1]) - hd[1] < tol * 0.5:
            continue
        if not _extreme_between(h, l, times, ls[0], hd[0], "high"):
            continue
        if not _extreme_between(h, l, times, hd[0], rs[0], "high"):
            continue
        add(Hit(rs[0], rs[2], "inv_head_shoulders", "bull"))

    hits = candles + structs
    print(f"[audit] structure hits={len(structs)}  total={len(hits)}", flush=True)

    # index hits by bar
    by_bar: dict[int, list[Hit]] = defaultdict(list)
    for hit in hits:
        by_bar[hit.i].append(hit)

    trades_path = ROOT / "data" / "replay" / args.job / "trades.csv"
    tdf = pd.read_csv(trades_path)
    tdf["entry_ts"] = pd.to_datetime(tdf["entry_time_utc"].str.replace(" UTC", "", regex=False), utc=True).dt.tz_localize(None)

    books = ["champion", "challenger"] if args.book == "both" else [args.book]
    lines: list[str] = []
    lines.append("PATTERN HIT-RATE AUDIT (after-cost) - research overlay only")
    lines.append(f"generated: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')} (Asia/Dhaka)")
    lines.append(f"pair/interval: EURUSD / 1h")
    lines.append(f"history: {len(times)} bars through {times[-1]}")
    lines.append(f"replay job: {args.job}")
    lines.append(f"trades file: {trades_path}")
    lines.append("pattern source: port of desk/src/patterns.ts (candles + structures; triangle skipped for speed)")
    lines.append("NOTE: Patterns are Show-patterns research overlay - NOT in Buy/Sell gates. No promote. No gate changes.")
    lines.append(f"alignment: CAUSAL decision_bar=entry-1 (Replay next-bar fill); lookback={args.lookback}; structures confirm pivot+3; directional bull<->BUY bear<->SELL")
    lines.append("costs: using Replay trades.csv net_pnl (already after-cost; conf>=0.60 path from pin Replay)")
    lines.append("")

    # pattern frequency
    freq = defaultdict(int)
    for hit in hits:
        freq[hit.pid] += 1
    lines.append("## Pattern detection counts (full history)")
    for pid in PATTERN_IDS:
        lines.append(f"  {pid}: {freq.get(pid, 0)}")
    lines.append("")

    verdicts: list[tuple[str, bool, str]] = []

    for book in books:
        bdf = tdf[tdf["book"] == book].copy().sort_values("entry_ts")
        base_pnls = bdf["net_pnl"].astype(float).tolist()
        base = book_metrics(base_pnls)
        lines.append(f"## Book: {book}")
        lines.append(f"  BASELINE (all Replay trades): {fmt_m(base)}")

        # classify each trade
        aligned_dir_pnls: list[float] = []
        aligned_any_pnls: list[float] = []
        by_pat_dir: dict[str, list[float]] = defaultdict(list)
        by_pat_any: dict[str, list[float]] = defaultdict(list)
        n_miss_bar = 0

        for _, row in bdf.iterrows():
            et = row["entry_ts"]
            # normalize to naive timestamp matching hist
            if getattr(et, "tzinfo", None) is not None:
                et = et.tz_localize(None) if et.tzinfo is None else et.tz_convert(None).tz_localize(None)
            # hist keys are Timestamp
            ei = time_to_i.get(pd.Timestamp(et))
            if ei is None:
                # try floor to hour
                ei = time_to_i.get(pd.Timestamp(et).floor("h"))
            if ei is None:
                n_miss_bar += 1
                continue
            # Replay fills next-bar open AFTER decision bar (forex_lab/replay.py).
            di = ei - 1
            if di < 0:
                n_miss_bar += 1
                continue
            window = []
            for j in range(max(0, di - args.lookback), di + 1):
                window.extend(by_bar.get(j, []))
            if not window:
                continue
            aligned_any_pnls.append(float(row["net_pnl"]))
            for hit in window:
                by_pat_any[hit.pid].append(float(row["net_pnl"]))
            dir_hits = [hit for hit in window if side_ok(str(row["side"]), hit.side)]
            # directional only if at least one non-neutral agreeing OR neutral-only doesn't count for dir
            dir_hits_strict = [hit for hit in dir_hits if hit.side != "neutral"]
            if dir_hits_strict:
                aligned_dir_pnls.append(float(row["net_pnl"]))
                for hit in dir_hits_strict:
                    by_pat_dir[hit.pid].append(float(row["net_pnl"]))

        m_any = book_metrics(aligned_any_pnls)
        m_dir = book_metrics(aligned_dir_pnls)
        lines.append(f"  ANY pattern on entry:         {fmt_m(m_any)}")
        lines.append(f"  DIRECTIONAL pattern align:    {fmt_m(m_dir)}")
        lines.append(f"  entry bars unmatched: {n_miss_bar}")

        # per-pattern directional
        lines.append("  per-pattern DIRECTIONAL (bull/bear agree with side):")
        for pid in PATTERN_IDS:
            if pid not in by_pat_dir:
                continue
            lines.append(f"    {pid}: {fmt_m(book_metrics(by_pat_dir[pid]))}")
        lines.append("  per-pattern ANY (pattern present on entry, incl neutral):")
        for pid in PATTERN_IDS:
            if pid not in by_pat_any:
                continue
            lines.append(f"    {pid}: {fmt_m(book_metrics(by_pat_any[pid]))}")

        # verdict for this book: does directional alignment add edge?
        # YES if n>=30 and (PF higher AND expectancy higher) vs baseline
        yes = False
        reason = ""
        if m_dir["n"] < 30:
            reason = f"directional n={m_dir['n']} < 30 - insufficient sample"
        else:
            pf_ok = m_dir["profit_factor"] == m_dir["profit_factor"] and base["profit_factor"] == base["profit_factor"] and m_dir["profit_factor"] > base["profit_factor"]
            exp_ok = m_dir["expectancy"] > base["expectancy"]
            wr_ok = m_dir["win_rate"] >= base["win_rate"] - 1e-12
            if pf_ok and exp_ok:
                yes = True
                reason = (
                    f"directional PF {m_dir['profit_factor']:.4f} > baseline {base['profit_factor']:.4f} "
                    f"AND exp {m_dir['expectancy']:.6f} > {base['expectancy']:.6f} (n={m_dir['n']})"
                )
            else:
                reason = (
                    f"directional PF {m_dir['profit_factor']:.4f} vs base {base['profit_factor']:.4f}; "
                    f"exp {m_dir['expectancy']:.6f} vs {base['expectancy']:.6f}; "
                    f"WR {m_dir['win_rate']:.4f} vs {base['win_rate']:.4f} - no clear after-cost edge"
                )
        verdicts.append((book, yes, reason))
        lines.append(f"  VERDICT ({book}): {'YES' if yes else 'NO'} - {reason}")
        lines.append("")

    # overall blunt verdict: YES only if champion book says YES (production-like path)
    champ = next((v for v in verdicts if v[0] == "champion"), verdicts[0])
    overall = "YES" if champ[1] else "NO"
    lines.append("## BLUNT OVERALL VERDICT")
    lines.append(f"  Patterns add after-cost edge over pin Replay baseline? {overall}")
    lines.append(f"  (primary book={champ[0]}) {champ[2]}")
    lines.append("  Do NOT wire patterns into Buy/Sell gates based on this alone.")
    lines.append("")
    lines.append("## Method notes")
    lines.append("  - Desk Show-patterns overlay: desk/src/patterns.ts + ChartPanel detectPatterns(visible bars).")
    lines.append("  - No /replay pattern endpoint exists; this script is the smallest local audit.")
    lines.append("  - DD_path is cumulative net_pnl drawdown on the filtered trade subset (not identical to Replay equity %).")
    lines.append("  - Triangle detector skipped (rolling window cost); candle+H&S/double swings included.")
    lines.append("  - CAUSAL: patterns joined to decision bar (entry-1); structure hits delayed by pivot right=3.")
    lines.append("")

    out = Path(args.out)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(("\n".join(lines)).encode("ascii", "replace").decode("ascii"))
    print(f"\n[audit] wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
