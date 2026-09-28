"""Rolling weekly promote-only gate on AFTER-COST Replay scoreboards.

Pins a Replay champion (job + champion-book metrics). A weekly challenger is
another finished Replay job's book (default: that job's champion book). Promote
ONLY when ``forex_lab.retrain.promotion_decision`` says so (relative improve /
non-regression **and** absolute floors). Otherwise KEEP the pinned champion.

Does **not** start a Replay. Does **not** overwrite ``data/champion`` / live
joblib unless ``apply_live_on_promote`` is explicitly true (default false —
Replay stays advisory for live models until you opt in).

Research only — not a live edge. Fail-soft. No BrokerPort.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from forex_lab.clock import fmt_display, timezone_name
from forex_lab.config_loader import load_config
from forex_lab.paths import resolve_under_root
from forex_lab.retrain import (
    HONEST_NOTE,
    VERDICT_KEEP,
    VERDICT_NULL,
    VERDICT_PROMOTE,
    VERDICT_SEED,
    promotion_decision,
    promotion_floors,
    retrain_cfg,
    save_champion,
)

DEFAULT_POINTER = "data/replay/weekly_champion.json"
DEFAULT_HISTORY = "data/replay/weekly_promote_history.jsonl"
DEFAULT_BASELINE_JOB = "45bbad3a346c45098fbfe9bf358e8c54"
METRIC_KEYS = (
    "n_trades",
    "win_rate",
    "profit_factor",
    "total_return",
    "max_drawdown",
    "expectancy",
    "net_pnl",
)


def weekly_replay_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    rc = retrain_cfg(cfg)
    raw = dict(rc.get("weekly_replay") or {})
    raw.setdefault("enabled", True)
    raw.setdefault("pointer", DEFAULT_POINTER)
    raw.setdefault("history", DEFAULT_HISTORY)
    raw.setdefault("baseline_job", DEFAULT_BASELINE_JOB)
    raw.setdefault("challenger_book", "champion")  # book on challenger job
    raw.setdefault("apply_live_on_promote", False)
    return raw


def replay_store(cfg: dict[str, Any] | None = None) -> Path:
    rel = str(((cfg or {}).get("replay") or {}).get("store_dir") or "data/replay")
    path = resolve_under_root(rel)
    path.mkdir(parents=True, exist_ok=True)
    return path


def pointer_path(cfg: dict[str, Any] | None = None) -> Path:
    return resolve_under_root(str(weekly_replay_cfg(cfg).get("pointer") or DEFAULT_POINTER))


def history_path(cfg: dict[str, Any] | None = None) -> Path:
    return resolve_under_root(str(weekly_replay_cfg(cfg).get("history") or DEFAULT_HISTORY))


def job_dir(job_id: str, cfg: dict[str, Any] | None = None) -> Path:
    return replay_store(cfg) / str(job_id).strip()


def load_scoreboard_book(
    job_id: str,
    book: str = "champion",
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load after-cost metrics for one Replay book from scoreboard.csv."""
    path = job_dir(job_id, cfg) / "scoreboard.csv"
    if not path.exists() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"Replay scoreboard missing: {path}")
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("book") or "").strip().lower() != str(book).strip().lower():
                continue
            out: dict[str, Any] = {"book": book, "job_id": str(job_id)}
            for key in METRIC_KEYS:
                raw = row.get(key)
                if raw is None or raw == "":
                    continue
                try:
                    out[key] = float(raw)
                except (TypeError, ValueError):
                    out[key] = raw
            if "n_trades" in out:
                out["n_trades"] = int(float(out["n_trades"]))
            return out
    raise KeyError(f"book={book!r} not in {path}")


def load_job_status(job_id: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    path = job_dir(job_id, cfg) / "status.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def load_weekly_champion(cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    path = pointer_path(cfg)
    if not path.exists() or path.stat().st_size <= 0:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _stamp(now: datetime | None, cfg: dict[str, Any] | None) -> dict[str, str]:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return {
        "at_utc": clock.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "at_display": fmt_display(clock, cfg, seconds=True),
        "timezone": timezone_name(cfg),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(payload), indent=2, default=str), encoding="utf-8")
        return path
    except OSError:
        return None


def _append_history(cfg: dict[str, Any] | None, row: Mapping[str, Any]) -> None:
    path = history_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(row), default=str) + "\n")
    except OSError:
        return


def seed_weekly_champion(
    job_id: str,
    cfg: dict[str, Any] | None = None,
    *,
    book: str = "champion",
    now: datetime | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Pin a finished Replay job's book as the rolling weekly champion (not a promotion)."""
    cfg = cfg if cfg is not None else load_config()
    metrics = load_scoreboard_book(job_id, book=book, cfg=cfg)
    status = load_job_status(job_id, cfg)
    stamps = _stamp(now, cfg)
    record = {
        "pair": str(status.get("pair") or (cfg.get("retrain") or {}).get("pair") or "EURUSD").upper(),
        "status": "weekly_champion",
        "verdict": VERDICT_SEED,
        "source": "replay_scoreboard",
        "job_id": str(job_id),
        "book": book,
        "metrics": {k: metrics.get(k) for k in METRIC_KEYS if k in metrics},
        "min_confidence": status.get("min_confidence"),
        "start": status.get("start"),
        "finished_at_dhaka": status.get("finished_at_dhaka"),
        "honest_note": HONEST_NOTE,
        "live_edge": False,
        "advisory": (
            "Rolling weekly Replay champion pin. After-cost scoreboard only. "
            "Does not overwrite data/champion unless apply_live_on_promote."
        ),
        **stamps,
    }
    if persist:
        _write_json(pointer_path(cfg), record)
        _append_history(
            cfg,
            {
                "event": "seed",
                "verdict": VERDICT_SEED,
                "promote": False,
                "job_id": str(job_id),
                "book": book,
                **stamps,
            },
        )
    return record


def _fmt_metrics(m: Mapping[str, Any] | None) -> str:
    m = m or {}
    pf = m.get("profit_factor")
    ret = m.get("total_return")
    dd = m.get("max_drawdown")
    wr = m.get("win_rate")
    n = m.get("n_trades")
    try:
        pf_s = f"{float(pf):.4f}" if pf is not None else "?"
    except (TypeError, ValueError):
        pf_s = str(pf)
    try:
        ret_s = f"{float(ret):+.2%}" if ret is not None else "?"
    except (TypeError, ValueError):
        ret_s = str(ret)
    try:
        dd_s = f"{float(dd):.2%}" if dd is not None else "?"
    except (TypeError, ValueError):
        dd_s = str(dd)
    try:
        wr_s = f"{float(wr):.3f}" if wr is not None else "?"
    except (TypeError, ValueError):
        wr_s = str(wr)
    return f"PF={pf_s} ret={ret_s} DD={dd_s} WR={wr_s} n={n}"


def format_weekly_promote_md(result: Mapping[str, Any]) -> str:
    champ = result.get("champion_metrics") or {}
    chal = result.get("challenger_metrics") or {}
    dec = result.get("decision") or {}
    floors = result.get("floors") or {}
    lines = [
        "# Rolling weekly promote-only Replay",
        "",
        f"- Generated: {result.get('at_display') or result.get('at_utc')}",
        f"- Pair: `{result.get('pair')}`",
        f"- Mode: `{dec.get('mode')}` (same gate as `forex_lab.retrain`)",
        f"- Dry-run: **{bool(result.get('dry_run'))}**",
        f"- Verdict: **{result.get('verdict')}** (promote={result.get('promote')})",
        f"- Live joblib apply: **{bool(result.get('live_applied'))}** "
        f"(apply_live_on_promote={bool(result.get('apply_live_on_promote'))})",
        "",
        "## Incumbent (pinned Replay champion)",
        "",
        f"- job: `{result.get('champion_job')}` book=`{result.get('champion_book')}`",
        f"- {_fmt_metrics(champ)}",
        "",
        "## Challenger (Replay after-cost)",
        "",
        f"- job: `{result.get('challenger_job')}` book=`{result.get('challenger_book')}`",
        f"- {_fmt_metrics(chal)}",
        "",
        "## Deltas (challenger - champion)",
        "",
    ]
    deltas = dec.get("deltas") or {}
    lines.append(
        f"- d_pf={deltas.get('profit_factor')}  d_ret={deltas.get('total_return')}  "
        f"d_dd={deltas.get('max_drawdown')}"
    )
    lines.append("")
    lines.append("## Floors")
    lines.append("")
    lines.append(
        f"- min_pf={floors.get('min_profit_factor')}  max_dd_floor={floors.get('max_drawdown_floor')}  "
        f"min_wr={floors.get('min_win_rate')}  min_trades={floors.get('min_trades')}"
    )
    lines.append(
        f"- floors_ok={dec.get('floors_ok')}  pf_ok={dec.get('pf_ok')}  "
        f"return_ok={dec.get('return_ok')}  dd_ok={dec.get('dd_ok')}"
    )
    lines.append("")
    lines.append("## Reasons")
    lines.append("")
    for reason in dec.get("reasons") or []:
        lines.append(f"- {reason}")
    if not dec.get("reasons"):
        lines.append("- (none)")
    lines.append("")
    lines.append("## Decision")
    lines.append("")
    if result.get("verdict") == VERDICT_PROMOTE and result.get("promote"):
        lines.append("**PROMOTE** challenger over pinned Replay champion.")
    elif result.get("verdict") == VERDICT_SEED:
        lines.append("**SEED** — no pinned champion yet (not a claimed improvement).")
    else:
        lines.append("**KEEP** pinned Replay champion (no silent overwrite).")
    if result.get("dry_run"):
        lines.append("")
        lines.append("Dry-run: pointer / live joblib **not** written.")
    lines.append("")
    lines.append("## Honest note")
    lines.append("")
    lines.append(HONEST_NOTE)
    lines.append("")
    lines.append(
        "Replay promote remains advisory for live models unless "
        "`retrain.weekly_replay.apply_live_on_promote: true`."
    )
    lines.append("")
    return "\n".join(lines)


def format_weekly_promote_text(result: Mapping[str, Any]) -> str:
    champ = result.get("champion_metrics") or {}
    chal = result.get("challenger_metrics") or {}
    dec = result.get("decision") or {}
    d = dec.get("deltas") or {}
    lines = [
        f"Weekly Replay promote-only  pair={result.get('pair')}  "
        f"verdict={result.get('verdict')}  dry_run={result.get('dry_run')}",
        HONEST_NOTE,
        f"champ_job={result.get('champion_job')}  chal_job={result.get('challenger_job')}",
        f"mode={dec.get('mode')}  d_pf={d.get('profit_factor')}  "
        f"d_ret={d.get('total_return')}  d_dd={d.get('max_drawdown')}",
    ]
    for reason in dec.get("reasons") or []:
        lines.append(f"  - {reason}")
    lines.append(f"champion: {_fmt_metrics(champ)}")
    lines.append(f"challenger: {_fmt_metrics(chal)}")
    if result.get("dry_run"):
        lines.append("dry-run: pointer not written (compare only)")
    elif result.get("verdict") == VERDICT_PROMOTE and result.get("promote"):
        lines.append("pinned Replay champion updated (promote)")
    elif result.get("verdict") == VERDICT_SEED:
        lines.append("weekly champion seeded (not a promotion)")
    else:
        lines.append("pinned Replay champion unchanged (keep)")
    from forex_lab.console import ascii_text

    return ascii_text("\n".join(lines) + "\n")


def run_weekly_replay_promote(
    pair: str | None = None,
    cfg: dict[str, Any] | None = None,
    *,
    champion_job: str | None = None,
    challenger_job: str | None = None,
    challenger_book: str | None = None,
    dry_run: bool = True,
    seed_if_missing: bool = True,
    now: datetime | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Compare Replay after-cost metrics; promote-or-keep the weekly pin.

    Default dry_run=True so a weekly habit cannot silently overwrite.
    """
    cfg = cfg if cfg is not None else load_config()
    wr = weekly_replay_cfg(cfg)
    stamps = _stamp(now, cfg)
    pair_u = str(
        pair or (cfg.get("retrain") or {}).get("pair") or "EURUSD"
    ).upper()
    apply_live = bool(wr.get("apply_live_on_promote", False))
    chal_book = str(challenger_book or wr.get("challenger_book") or "champion")

    result: dict[str, Any] = {
        "pair": pair_u,
        "ok": True,
        "verdict": VERDICT_KEEP,
        "promote": False,
        "dry_run": bool(dry_run),
        "error": None,
        "pointer_written": False,
        "live_applied": False,
        "apply_live_on_promote": apply_live,
        "floors": promotion_floors(cfg),
        "honest_note": HONEST_NOTE,
        "live_edge": False,
        **stamps,
    }

    try:
        pinned = load_weekly_champion(cfg)
        champ_job = champion_job or (pinned or {}).get("job_id") or wr.get("baseline_job")
        if not champ_job:
            result["ok"] = False
            result["error"] = "no champion_job / baseline_job / pinned weekly champion"
            result["verdict"] = VERDICT_NULL
            return result

        if pinned is None and seed_if_missing:
            # First habit run: pin baseline (seed, not promote). Still compare.
            if not dry_run and persist:
                pinned = seed_weekly_champion(str(champ_job), cfg, now=now, persist=True)
            else:
                pinned = seed_weekly_champion(str(champ_job), cfg, now=now, persist=False)
            result["seeded"] = True

        if pinned and not champion_job:
            champ_metrics = dict((pinned.get("metrics") or {}))
            champ_job = str(pinned.get("job_id") or champ_job)
            champ_book = str(pinned.get("book") or "champion")
            if not champ_metrics:
                champ_metrics = load_scoreboard_book(champ_job, book=champ_book, cfg=cfg)
        else:
            champ_book = "champion"
            champ_metrics = load_scoreboard_book(str(champ_job), book=champ_book, cfg=cfg)

        # Challenger: another finished Replay (required for a real weekly compare).
        # Dry-run default when omitted: same job's *challenger* book (logistic) so
        # the gate is exercised without starting a new Replay.
        if challenger_job:
            chal_job = str(challenger_job)
            chal_metrics = load_scoreboard_book(chal_job, book=chal_book, cfg=cfg)
        else:
            chal_job = str(champ_job)
            # Prefer within-job challenger book when no new Replay was supplied.
            try:
                chal_metrics = load_scoreboard_book(chal_job, book="challenger", cfg=cfg)
                chal_book = "challenger"
            except KeyError:
                chal_metrics = load_scoreboard_book(chal_job, book=chal_book, cfg=cfg)

        decision = promotion_decision(champ_metrics, chal_metrics, cfg)
        result["decision"] = decision.as_dict()
        result["champion_job"] = str(champ_job)
        result["champion_book"] = champ_book if pinned else "champion"
        result["challenger_job"] = chal_job
        result["challenger_book"] = chal_book
        result["champion_metrics"] = {k: champ_metrics.get(k) for k in METRIC_KEYS if k in champ_metrics}
        result["challenger_metrics"] = {k: chal_metrics.get(k) for k in METRIC_KEYS if k in chal_metrics}
        result["verdict"] = decision.verdict
        result["promote"] = bool(decision.promote)

        if decision.promote and not dry_run and persist:
            new_pin = {
                "pair": pair_u,
                "status": "weekly_champion",
                "verdict": VERDICT_PROMOTE,
                "source": "replay_scoreboard",
                "job_id": chal_job,
                "book": chal_book,
                "metrics": result["challenger_metrics"],
                "previous_job_id": str(champ_job),
                "honest_note": HONEST_NOTE,
                "live_edge": False,
                **stamps,
            }
            status = load_job_status(chal_job, cfg)
            new_pin["min_confidence"] = status.get("min_confidence")
            new_pin["start"] = status.get("start")
            new_pin["finished_at_dhaka"] = status.get("finished_at_dhaka")
            written = _write_json(pointer_path(cfg), new_pin)
            result["pointer_written"] = written is not None
            result["weekly_champion"] = new_pin
            if apply_live:
                live_rec = {
                    "pair": pair_u,
                    "status": "champion",
                    "verdict": VERDICT_PROMOTE,
                    "source": "weekly_replay_promote",
                    "metrics": result["challenger_metrics"],
                    "replay_job_id": chal_job,
                    "honest_note": HONEST_NOTE,
                    "live_edge": False,
                    "promoted_at": stamps["at_utc"],
                    "promoted_at_display": stamps["at_display"],
                    "timezone": stamps["timezone"],
                }
                save_champion(pair_u, cfg, live_rec)
                result["live_applied"] = True

        if persist:
            _append_history(
                cfg,
                {
                    "event": "compare",
                    "verdict": result["verdict"],
                    "promote": result["promote"],
                    "dry_run": bool(dry_run),
                    "champion_job": result.get("champion_job"),
                    "challenger_job": result.get("challenger_job"),
                    "deltas": (result.get("decision") or {}).get("deltas"),
                    **stamps,
                },
            )

            stamp_file = datetime.now().strftime("%Y%m%dT%H%M%S")
            base = replay_store(cfg) / f"weekly_promote_{stamp_file}"
            md_path = Path(str(base) + ".md")
            json_path = Path(str(base) + ".json")
            md_path.write_text(format_weekly_promote_md(result), encoding="utf-8")
            _write_json(json_path, result)
            # Stable "latest" aliases for the habit / 8:04 routine.
            latest_md = replay_store(cfg) / "weekly_promote_latest.md"
            latest_json = replay_store(cfg) / "weekly_promote_latest.json"
            latest_md.write_text(format_weekly_promote_md(result), encoding="utf-8")
            _write_json(latest_json, result)
            result["scoreboard_md"] = str(md_path)
            result["scoreboard_json"] = str(json_path)
            result["scoreboard_latest_md"] = str(latest_md)
            result["scoreboard_latest_json"] = str(latest_json)

        return result
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = str(exc)
        result["verdict"] = VERDICT_KEEP
        result["promote"] = False
        result["decision"] = promotion_decision(None, None, cfg).as_dict()
        result["decision"]["reasons"] = [f"weekly Replay promote failed ({exc})"]
        return result


__all__ = [
    "format_weekly_promote_md",
    "format_weekly_promote_text",
    "job_dir",
    "load_job_status",
    "load_scoreboard_book",
    "load_weekly_champion",
    "run_weekly_replay_promote",
    "seed_weekly_champion",
    "weekly_replay_cfg",
]
