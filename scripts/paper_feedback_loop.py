"""Lightweight live-paper -> gate-review feedback loop.

Habit (not a live trade instruction):
  1. Read data/paper_broker.json closed trades.
  2. Append any new closes into data/replay/paper_feedback_journal.jsonl (idempotent by id).
  3. Write data/replay/paper_feedback_latest.md snapshot (Asia/Dhaka times).
  4. If n_closed < MIN_CLOSES: document thin sample; DO NOT invent gates.
  5. If n_closed >= MIN_CLOSES: emit one candidate knob suggestion for a future
     SERIAL Replay (still requires before/after KEEP ON/OFF — never auto-apply).

Run:
  .venv\\Scripts\\python.exe scripts\\paper_feedback_loop.py
Optional Task Scheduler / weekly habit alongside weekly_replay_promote.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "data" / "paper_broker.json"
JOURNAL = ROOT / "data" / "replay" / "paper_feedback_journal.jsonl"
LATEST = ROOT / "data" / "replay" / "paper_feedback_latest.md"
CHECKLIST = ROOT / "data" / "replay" / "paper_gate_review_checklist.md"
MIN_CLOSES = 20
DHAKA = ZoneInfo("Asia/Dhaka")
UTC = ZoneInfo("UTC")


def _now_dhaka() -> str:
    return datetime.now(tz=DHAKA).strftime("%Y-%m-%d %H:%M Asia/Dhaka")


def _load_seen() -> set[str]:
    seen: set[str] = set()
    if JOURNAL.is_file():
        for line in JOURNAL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(str(json.loads(line).get("id") or ""))
            except json.JSONDecodeError:
                continue
    seen.discard("")
    return seen


def _append(rows: list[dict]) -> int:
    if not rows:
        return 0
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def _summarize(closed: list[dict]) -> dict:
    n = len(closed)
    rights = sum(1 for c in closed if str(c.get("outcome") or "").upper() == "RIGHT")
    pnls = [float(c.get("realized") or 0.0) for c in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    pf = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    confs = [float(c.get("confidence") or 0.0) for c in closed]
    by_reason: dict[str, int] = {}
    for c in closed:
        r = str(c.get("exit_reason") or "?")
        by_reason[r] = by_reason.get(r, 0) + 1
    return {
        "n": n,
        "wr": (rights / n) if n else 0.0,
        "sum_pnl": sum(pnls),
        "pf": pf,
        "mean_conf": (sum(confs) / n) if n else 0.0,
        "by_exit_reason": by_reason,
    }


def _candidate_knob(closed: list[dict]) -> str:
    """Heuristic suggestion only — never applied here."""
    if len(closed) < MIN_CLOSES:
        return "none (thin sample)"
    low = [c for c in closed if float(c.get("confidence") or 0) < 0.60]
    high = [c for c in closed if float(c.get("confidence") or 0) >= 0.60]
    def pf(rows: list[dict]) -> float:
        pnls = [float(c.get("realized") or 0) for c in rows]
        w = sum(p for p in pnls if p > 0)
        l = abs(sum(p for p in pnls if p < 0))
        return (w / l) if l else float("inf")
    if low and high and pf(high) > pf(low) + 0.15:
        return "consider SERIAL Replay: raise signals.min_confidence (evidence: live high-conf PF > low-conf)"
    # weekday weakness
    from collections import Counter
    bad = Counter()
    for c in closed:
        et = str(c.get("entry_time") or "")
        try:
            dt = datetime.strptime(et.replace(" UTC", "").strip()[:19], "%Y-%m-%d %H:%M:%S")
            if float(c.get("realized") or 0) < 0:
                bad[dt.strftime("%a")] += 1
        except Exception:
            pass
    if bad:
        top, n = bad.most_common(1)[0]
        if n >= max(3, len(closed) // 5):
            return f"consider SERIAL Replay: weekday_gate review ({top} heavy live losses) — do not auto-block"
    return "no clear single-knob pattern yet — keep logging; next SERIAL picks from slices"


def write_checklist() -> None:
    body = f"""# Paper → gate review checklist (habit)

Updated: {_now_dhaka()}

Use after each desk session or weekly (with `weekly_replay_promote`).

1. Run `python scripts/paper_feedback_loop.py`
2. Open `data/replay/paper_feedback_latest.md`
3. If **n_closed < {MIN_CLOSES}**: stop. Do **not** invent gates from a thin sample.
4. If **n_closed >= {MIN_CLOSES}**:
   - Read the suggested candidate knob (advisory only)
   - Open a SERIAL step: one knob, full-hist before/after vs current live pin
   - KEEP ON only if after-cost PF↑ or DD clearly better without wrecking PF
5. Never auto-write `config/default.yaml` from this script
6. Prefer Replay champion book when live sample stays thin (as in tighten K)

Artifacts:
- Journal: `data/replay/paper_feedback_journal.jsonl`
- Snapshot: `data/replay/paper_feedback_latest.md`
- This checklist: `data/replay/paper_gate_review_checklist.md`
"""
    CHECKLIST.write_text(body, encoding="utf-8")


def main() -> int:
    if not PAPER.is_file():
        print(f"missing {PAPER}")
        return 1
    payload = json.loads(PAPER.read_text(encoding="utf-8"))
    closed = list(payload.get("closed") or [])
    seen = _load_seen()
    new_rows = []
    for c in closed:
        cid = str(c.get("id") or "")
        if not cid or cid in seen:
            continue
        new_rows.append(
            {
                "id": cid,
                "pair": c.get("pair"),
                "side": c.get("side"),
                "outcome": c.get("outcome"),
                "exit_reason": c.get("exit_reason"),
                "realized": c.get("realized"),
                "confidence": c.get("confidence"),
                "entry_time": c.get("entry_time"),
                "exit_time": c.get("exit_time"),
                "session": c.get("session"),
                "strategy_id": c.get("strategy_id"),
                "logged_at_dhaka": _now_dhaka(),
            }
        )
    n_new = _append(new_rows)
    summary = _summarize(closed)
    thin = summary["n"] < MIN_CLOSES
    candidate = _candidate_knob(closed)
    decision = (
        f"THIN SAMPLE (n={summary['n']} < {MIN_CLOSES}) — no gate invented; habit only"
        if thin
        else f"SAMPLE OK (n={summary['n']}) — candidate for future SERIAL: {candidate}"
    )
    md = f"""# Paper feedback latest

- Generated: {_now_dhaka()}
- Source: `data/paper_broker.json`
- Journal append: **{n_new}** new closes → `data/replay/paper_feedback_journal.jsonl`
- Threshold for gate derivation: **{MIN_CLOSES}** closes

## Snapshot

| metric | value |
|---|---:|
| n_closed | {summary['n']} |
| WR | {summary['wr']:.3f} |
| sum realized | {summary['sum_pnl']:.6f} |
| rough PF | {summary['pf'] if summary['pf'] != float('inf') else 'inf'} |
| mean confidence | {summary['mean_conf']:.3f} |

Exit reasons: {summary['by_exit_reason']}

## Decision

**{decision}**

Candidate knob (advisory): {candidate}

## Next habit action

- [ ] Re-run this script after new paper closes
- [ ] If n≥{MIN_CLOSES}, open SERIAL before/after Replay for the candidate only
- [ ] Checklist: `data/replay/paper_gate_review_checklist.md`
"""
    LATEST.write_text(md, encoding="utf-8")
    write_checklist()
    print(f"n_closed={summary['n']} new_logged={n_new} thin={thin}")
    print(decision)
    print(f"wrote {LATEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
