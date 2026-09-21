"""Weekly champion/challenger retrain gate.

Walk-forward compare a freshly trained challenger against the saved champion.
Promote only when profit factor, total return, and max drawdown all improve
(or clear a configured non-regression bar). Otherwise keep the champion and
report ``null``.

Research only — not a live edge. Does not call BrokerPort. Fail-soft.
"""
from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from forex_lab.clock import fmt_display, timezone_name
from forex_lab.config_loader import load_config
from forex_lab.paths import resolve_under_root

HONEST_NOTE = (
    "Walk-forward champion/challenger comparison on cached yfinance bars — "
    "not a live edge, not broker truth, not a deployable trading system. "
    "Past WF metrics do not predict future results."
)

MODE_IMPROVE = "improve"
MODE_NON_REGRESSION = "non_regression"
VERDICT_PROMOTE = "promote"
VERDICT_NULL = "null"
VERDICT_SEED = "seed"
VERDICT_KEEP = "keep"
VERDICT_ERROR = "error"

DEFAULT_STORE = "data/champion"
DEFAULT_NONREG_PF_EPS = 0.05
DEFAULT_NONREG_RET_EPS = 0.03
DEFAULT_NONREG_DD_EPS = 0.01


def retrain_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("retrain") or {})
    raw.setdefault("enabled", True)
    raw.setdefault("fail_soft", True)
    raw.setdefault("pair", None)
    raw.setdefault("store", DEFAULT_STORE)
    raw.setdefault("mode", MODE_IMPROVE)
    raw.setdefault("pf_eps", 0.0)
    raw.setdefault("return_eps", 0.0)
    raw.setdefault("dd_eps", 0.0)
    raw.setdefault("min_trades", 1)
    raw.setdefault("seed_from_metrics", True)
    raw.setdefault("compare_logistic", False)
    raw.setdefault("train_on_promote", True)
    raw.setdefault("write_report", False)
    return raw


def champion_dir(cfg: dict[str, Any] | None = None) -> Path:
    rel = str(retrain_cfg(cfg).get("store") or DEFAULT_STORE)
    path = resolve_under_root(rel)
    path.mkdir(parents=True, exist_ok=True)
    return path


def champion_meta_path(pair: str, cfg: dict[str, Any] | None = None) -> Path:
    return champion_dir(cfg) / f"{str(pair).upper()}.json"


def challenger_meta_path(pair: str, cfg: dict[str, Any] | None = None) -> Path:
    return champion_dir(cfg) / f"{str(pair).upper()}_challenger.json"


def champion_history_path(pair: str, cfg: dict[str, Any] | None = None) -> Path:
    return champion_dir(cfg) / f"{str(pair).upper()}_history.jsonl"


def models_champion_pointer(pair: str, cfg: dict[str, Any] | None = None) -> Path:
    from forex_lab.model import model_dir

    return model_dir(cfg or {}) / f"{str(pair).upper()}_champion.json"


def _num(value: object, *, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(out):
        return default
    return out


def extract_model_metrics(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Pull the primary-model WF block from a result or latest_metrics.json."""
    if not payload:
        return {}
    block = payload.get("model") if isinstance(payload.get("model"), dict) else payload
    if not isinstance(block, dict):
        return {}
    keys = (
        "n_trades",
        "win_rate",
        "total_return",
        "max_drawdown",
        "profit_factor",
        "avg_return_per_trade",
    )
    out = {k: block.get(k) for k in keys}
    if payload.get("pair"):
        out["pair"] = payload.get("pair")
    if payload.get("model_type"):
        out["model_type"] = payload.get("model_type")
    if payload.get("folds") is not None:
        out["folds"] = payload.get("folds")
    return out


def _pf(metrics: Mapping[str, Any] | None) -> float:
    return _num((metrics or {}).get("profit_factor"), default=0.0)


def _ret(metrics: Mapping[str, Any] | None) -> float:
    return _num((metrics or {}).get("total_return"), default=0.0)


def _dd(metrics: Mapping[str, Any] | None) -> float:
    """Max drawdown is <= 0. Greater (closer to 0) is better."""
    return _num((metrics or {}).get("max_drawdown"), default=0.0)


def _eps_for_mode(cfg: dict[str, Any] | None) -> tuple[str, float, float, float]:
    rc = retrain_cfg(cfg)
    mode = str(rc.get("mode") or MODE_IMPROVE).strip().lower()
    if mode not in {MODE_IMPROVE, MODE_NON_REGRESSION}:
        mode = MODE_IMPROVE
    pf_eps = _num(rc.get("pf_eps"), default=0.0)
    ret_eps = _num(rc.get("return_eps"), default=0.0)
    dd_eps = _num(rc.get("dd_eps"), default=0.0)
    if mode == MODE_NON_REGRESSION:
        if pf_eps == 0.0 and rc.get("pf_eps") in (0, 0.0, None):
            pf_eps = DEFAULT_NONREG_PF_EPS
        if ret_eps == 0.0 and rc.get("return_eps") in (0, 0.0, None):
            ret_eps = DEFAULT_NONREG_RET_EPS
        if dd_eps == 0.0 and rc.get("dd_eps") in (0, 0.0, None):
            dd_eps = DEFAULT_NONREG_DD_EPS
    return mode, pf_eps, ret_eps, dd_eps


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    verdict: str
    reasons: tuple[str, ...]
    deltas: dict[str, float]
    mode: str
    pf_ok: bool
    return_ok: bool
    dd_ok: bool
    honest_note: str = HONEST_NOTE

    def as_dict(self) -> dict[str, Any]:
        return {
            "promote": self.promote,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "deltas": dict(self.deltas),
            "mode": self.mode,
            "pf_ok": self.pf_ok,
            "return_ok": self.return_ok,
            "dd_ok": self.dd_ok,
            "honest_note": self.honest_note,
        }


def _deltas(champion: Mapping[str, Any], challenger: Mapping[str, Any]) -> dict[str, float]:
    return {
        "profit_factor": _pf(challenger) - _pf(champion),
        "total_return": _ret(challenger) - _ret(champion),
        # Positive delta = shallower drawdown (improvement).
        "max_drawdown": _dd(challenger) - _dd(champion),
    }


def promotion_decision(
    champion: Mapping[str, Any] | None,
    challenger: Mapping[str, Any] | None,
    cfg: dict[str, Any] | None = None,
) -> PromotionDecision:
    """Pure gate. Promote only on a clear improve / non-regression; else null.

    Rules (documented in README):

    * ``improve`` (default): challenger PF **and** total return are strictly
      greater than champion (plus ``pf_eps`` / ``return_eps``), **and** max
      drawdown is not worse (challenger DD >= champion DD - ``dd_eps``).
    * ``non_regression``: no metric regresses beyond the acceptance bar
      (defaults PF 0.05 / return 0.03 / DD 0.01) **and** at least one of the
      three strictly improves. Equal-within-eps is ``null``.
    * Missing champion -> ``seed`` (not a promotion).
    * Missing / empty challenger, or below ``min_trades`` -> ``null``.
    """
    mode, pf_eps, ret_eps, dd_eps = _eps_for_mode(cfg)
    min_trades = int(retrain_cfg(cfg).get("min_trades") or 1)
    empty_deltas = {"profit_factor": 0.0, "total_return": 0.0, "max_drawdown": 0.0}

    if not champion:
        return PromotionDecision(
            promote=False,
            verdict=VERDICT_SEED,
            reasons=("no saved champion — first walk-forward seeds the slot (not a promotion)",),
            deltas=empty_deltas,
            mode=mode,
            pf_ok=False,
            return_ok=False,
            dd_ok=False,
        )
    if not challenger:
        return PromotionDecision(
            promote=False,
            verdict=VERDICT_NULL,
            reasons=("challenger metrics missing — keep champion",),
            deltas=empty_deltas,
            mode=mode,
            pf_ok=False,
            return_ok=False,
            dd_ok=False,
        )

    n = int(_num(challenger.get("n_trades"), default=0.0))
    if n < min_trades:
        return PromotionDecision(
            promote=False,
            verdict=VERDICT_NULL,
            reasons=(f"challenger n_trades={n} below min_trades={min_trades} — keep champion",),
            deltas=_deltas(champion, challenger),
            mode=mode,
            pf_ok=False,
            return_ok=False,
            dd_ok=False,
        )

    d = _deltas(champion, challenger)
    d_pf, d_ret, d_dd = d["profit_factor"], d["total_return"], d["max_drawdown"]

    if mode == MODE_NON_REGRESSION:
        pf_ok = d_pf >= -pf_eps
        ret_ok = d_ret >= -ret_eps
        dd_ok = d_dd >= -dd_eps
        any_improve = (d_pf > 0.0) or (d_ret > 0.0) or (d_dd > 0.0)
        reasons: list[str] = []
        if not pf_ok:
            reasons.append(
                f"PF regressed by {d_pf:.4f} (bar {pf_eps:g}) — keep champion"
            )
        if not ret_ok:
            reasons.append(
                f"total return regressed by {d_ret:.4f} (bar {ret_eps:g}) — keep champion"
            )
        if not dd_ok:
            reasons.append(
                f"max DD worsened by {d_dd:.4f} (bar {dd_eps:g}) — keep champion"
            )
        if pf_ok and ret_ok and dd_ok and not any_improve:
            reasons.append("non-regression but no metric improved — null / fold noise")
        promote = bool(pf_ok and ret_ok and dd_ok and any_improve)
        if promote:
            reasons.append(
                "non-regression bar cleared and at least one of PF / total return / max DD improved"
            )
        return PromotionDecision(
            promote=promote,
            verdict=VERDICT_PROMOTE if promote else VERDICT_NULL,
            reasons=tuple(reasons),
            deltas=d,
            mode=mode,
            pf_ok=pf_ok,
            return_ok=ret_ok,
            dd_ok=dd_ok,
        )

    # Strict improve: all three must get better (DD not worse).
    pf_ok = d_pf > pf_eps
    ret_ok = d_ret > ret_eps
    dd_ok = d_dd >= -dd_eps
    reasons = []
    if not pf_ok:
        reasons.append(
            f"PF did not improve ({_pf(challenger):.4f} vs champion {_pf(champion):.4f}, "
            f"delta {d_pf:.4f}, need > {pf_eps:g})"
        )
    if not ret_ok:
        reasons.append(
            f"total return did not improve ({_ret(challenger):.4f} vs champion {_ret(champion):.4f}, "
            f"delta {d_ret:.4f}, need > {ret_eps:g})"
        )
    if not dd_ok:
        reasons.append(
            f"max DD worsened ({_dd(challenger):.4f} vs champion {_dd(champion):.4f}, "
            f"delta {d_dd:.4f}, bar {dd_eps:g})"
        )
    promote = bool(pf_ok and ret_ok and dd_ok)
    if promote:
        reasons.append(
            "PF, total return, and max DD all improved vs champion (research sample only)"
        )
    else:
        reasons.append("keep champion and report null")
    return PromotionDecision(
        promote=promote,
        verdict=VERDICT_PROMOTE if promote else VERDICT_NULL,
        reasons=tuple(reasons),
        deltas=d,
        mode=mode,
        pf_ok=pf_ok,
        return_ok=ret_ok,
        dd_ok=dd_ok,
    )


def should_promote(
    champion: Mapping[str, Any] | None,
    challenger: Mapping[str, Any] | None,
    cfg: dict[str, Any] | None = None,
) -> bool:
    return promotion_decision(champion, challenger, cfg).promote


def load_champion(pair: str, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    path = champion_meta_path(pair, cfg)
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def load_metrics_file(cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    from forex_lab.ui.pipeline import load_metrics

    try:
        return load_metrics(cfg)
    except Exception:
        return None


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(payload), indent=2, default=str), encoding="utf-8")
        return path
    except OSError:
        return None


def _append_history(pair: str, cfg: dict[str, Any] | None, row: Mapping[str, Any]) -> None:
    path = champion_history_path(pair, cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(row), default=str) + "\n")
    except OSError:
        return


def save_champion(
    pair: str,
    cfg: dict[str, Any] | None,
    record: Mapping[str, Any],
) -> Path | None:
    path = champion_meta_path(pair, cfg)
    written = _write_json(path, record)
    pointer = {
        "pair": str(pair).upper(),
        "store": str(path),
        "promoted_at": record.get("promoted_at"),
        "verdict": record.get("verdict"),
        "metrics": record.get("metrics"),
        "honest_note": HONEST_NOTE,
        "live_edge": False,
    }
    _write_json(models_champion_pointer(pair, cfg), pointer)
    return written


def _copy_joblib(pair: str, cfg: dict[str, Any]) -> dict[str, str]:
    from forex_lab.model import model_paths

    copied: dict[str, str] = {}
    dest_dir = champion_dir(cfg)
    mtype = str((cfg.get("model") or {}).get("type") or "xgboost")
    for kind in (mtype, "logistic"):
        paths = model_paths(pair, cfg, kind)
        for label, src in paths.items():
            if not src.exists() or src.stat().st_size <= 0:
                continue
            dest = dest_dir / src.name
            try:
                shutil.copy2(src, dest)
                copied[f"{kind}_{label}"] = str(dest)
            except OSError:
                continue
    return copied


def _stamp(now: datetime | None, cfg: dict[str, Any] | None) -> dict[str, str]:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return {
        "promoted_at": clock.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "promoted_at_display": fmt_display(clock, cfg, seconds=True),
        "timezone": timezone_name(cfg),
    }


def _wf_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """Challenger walk-forward: skip logistic by default (speed). Never enables a live broker."""
    out = dict(cfg)
    rc = retrain_cfg(cfg)
    model = dict(out.get("model") or {})
    model["compare_logistic"] = bool(rc.get("compare_logistic", False))
    out["model"] = model
    return out


def format_retrain_text(result: Mapping[str, Any]) -> str:
    lines = [
        f"Champion/challenger retrain  pair={result.get('pair')}  "
        f"verdict={result.get('verdict')}",
        HONEST_NOTE,
    ]
    dec = result.get("decision") or {}
    if dec:
        d = dec.get("deltas") or {}
        lines.append(
            f"mode={dec.get('mode')}  "
            f"d_pf={d.get('profit_factor')}  "
            f"d_ret={d.get('total_return')}  "
            f"d_dd={d.get('max_drawdown')}"
        )
        for reason in dec.get("reasons") or []:
            lines.append(f"  - {reason}")
    champ = (result.get("champion") or {}).get("metrics") or result.get("champion_metrics") or {}
    chal = result.get("challenger_metrics") or {}
    if champ or chal:
        lines.append(
            "champion: "
            f"pf={champ.get('profit_factor')} ret={champ.get('total_return')} "
            f"dd={champ.get('max_drawdown')} n={champ.get('n_trades')}"
        )
        lines.append(
            "challenger: "
            f"pf={chal.get('profit_factor')} ret={chal.get('total_return')} "
            f"dd={chal.get('max_drawdown')} n={chal.get('n_trades')}"
        )
    if result.get("error"):
        lines.append(f"fail-soft: {result.get('error')}")
    if result.get("dry_run"):
        lines.append("dry-run: champion not written (compare only)")
    elif result.get("champion_written") and result.get("verdict") == VERDICT_SEED:
        lines.append("champion seeded (first record - not a claimed improvement)")
    elif result.get("trained"):
        lines.append("production joblib refreshed (train_on_promote)")
    elif result.get("verdict") == VERDICT_NULL:
        lines.append("champion unchanged (null)")
    elif result.get("verdict") == VERDICT_SEED:
        lines.append("champion not written (seed preview)")
    from forex_lab.console import ascii_text

    return ascii_text("\n".join(lines) + "\n")


def run_retrain_gate(
    pair: str,
    cfg: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    walk_forward_fn: Callable[..., Any] | None = None,
    train_fn: Callable[..., Any] | None = None,
    ohlcv: Any = None,
    challenger_metrics: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    persist: bool = True,
) -> dict[str, Any]:
    """Train/score a challenger and maybe promote it.

    ``dry_run`` uses ``challenger_metrics`` or ``reports/latest_metrics.json``
    and never writes joblib. Tests inject ``walk_forward_fn`` / ``train_fn``.
    """
    cfg = cfg if cfg is not None else load_config()
    rc = retrain_cfg(cfg)
    pair_u = str(pair or rc.get("pair") or "EURUSD").upper()
    fail_soft = bool(rc.get("fail_soft", True))
    stamps = _stamp(now, cfg)
    result: dict[str, Any] = {
        "pair": pair_u,
        "ok": True,
        "verdict": VERDICT_KEEP,
        "promote": False,
        "error": None,
        "dry_run": bool(dry_run),
        "trained": False,
        "champion_written": False,
        "copied": {},
        "honest_note": HONEST_NOTE,
        "live_edge": False,
        **stamps,
    }

    def _fail(msg: str) -> dict[str, Any]:
        result["ok"] = False
        result["error"] = msg
        result["verdict"] = VERDICT_ERROR if not fail_soft else VERDICT_KEEP
        result["promote"] = False
        result["decision"] = PromotionDecision(
            promote=False,
            verdict=VERDICT_NULL,
            reasons=(msg,),
            deltas={"profit_factor": 0.0, "total_return": 0.0, "max_drawdown": 0.0},
            mode=str(rc.get("mode") or MODE_IMPROVE),
            pf_ok=False,
            return_ok=False,
            dd_ok=False,
        ).as_dict()
        if persist:
            _write_json(challenger_meta_path(pair_u, cfg), result)
            _append_history(pair_u, cfg, {"verdict": result["verdict"], **stamps, "error": msg})
        return result

    try:
        champion_rec = load_champion(pair_u, cfg)
        champ_metrics = extract_model_metrics((champion_rec or {}).get("metrics") or champion_rec)
        if champion_rec and not champ_metrics:
            champ_metrics = extract_model_metrics(champion_rec)

        chal_metrics: dict[str, Any] | None = (
            extract_model_metrics(dict(challenger_metrics)) if challenger_metrics else None
        )
        wf_result = None
        walked = False
        seeded_from_disk = False

        def _metrics_from_disk() -> dict[str, Any] | None:
            disk = load_metrics_file(cfg)
            if not disk:
                return None
            disk_pair = str(disk.get("pair") or "").upper()
            if disk_pair and disk_pair != pair_u:
                return None
            return disk

        if chal_metrics is None and dry_run:
            disk = _metrics_from_disk()
            if disk:
                chal_metrics = extract_model_metrics(disk)
                wf_result = disk
        elif chal_metrics is None and champion_rec is None and bool(rc.get("seed_from_metrics", True)):
            disk = _metrics_from_disk()
            if disk:
                chal_metrics = extract_model_metrics(disk)
                wf_result = disk
                seeded_from_disk = True

        if chal_metrics is None and not dry_run:
            from forex_lab.backtest import walk_forward_backtest, write_report
            from forex_lab.data import load_ohlcv

            df = ohlcv
            if df is None:
                try:
                    df = load_ohlcv(pair_u, cfg)
                except Exception as exc:  # noqa: BLE001
                    return _fail(f"no OHLCV for {pair_u} ({exc})")
            wf_fn = walk_forward_fn or walk_forward_backtest
            try:
                wf_out = wf_fn(df, _wf_cfg(cfg), pair_u)
            except Exception as exc:  # noqa: BLE001
                return _fail(f"walk-forward failed ({exc})")
            if isinstance(wf_out, tuple):
                wf_result, trades = wf_out[0], wf_out[1] if len(wf_out) > 1 else None
            else:
                wf_result, trades = wf_out, None
            chal_metrics = extract_model_metrics(wf_result)
            walked = True
            if bool(rc.get("write_report")) and wf_result is not None:
                try:
                    write_report(wf_result, cfg, trades)
                except Exception:
                    pass

        if chal_metrics is None:
            return _fail("challenger metrics unavailable (run backtest or pass data)")

        decision = promotion_decision(
            champ_metrics if champion_rec else None,
            chal_metrics,
            cfg,
        )
        result["decision"] = decision.as_dict()
        result["challenger_metrics"] = chal_metrics
        result["champion"] = champion_rec
        result["champion_metrics"] = champ_metrics or None

        do_seed = decision.verdict == VERDICT_SEED
        do_promote = decision.promote
        result["verdict"] = decision.verdict
        result["promote"] = bool(do_promote)

        # Refresh production joblib only on a real promotion, or on a seed that
        # came from a fresh walk-forward (not a copy of latest_metrics.json).
        should_train = bool(rc.get("train_on_promote")) and not dry_run and (
            do_promote or (do_seed and walked)
        )

        if should_train:
            from forex_lab.model import train_models
            from forex_lab.data import load_ohlcv

            fn = train_fn or train_models
            df = ohlcv
            if df is None:
                try:
                    df = load_ohlcv(pair_u, cfg)
                except Exception as exc:  # noqa: BLE001
                    if do_promote:
                        return _fail(f"promote aborted; train failed ({exc})")
                    result["error"] = f"seed train skipped ({exc})"
                    should_train = False
            if should_train and df is not None:
                try:
                    fn(df, cfg, pair_u)
                    result["trained"] = True
                    result["copied"] = _copy_joblib(pair_u, cfg)
                except Exception as exc:  # noqa: BLE001
                    if do_promote:
                        return _fail(f"promote aborted; train failed ({exc})")
                    result["error"] = f"seed train skipped ({exc})"

        if persist and (do_promote or do_seed) and not dry_run:
            record = {
                "pair": pair_u,
                "status": "champion",
                "verdict": VERDICT_SEED if do_seed else VERDICT_PROMOTE,
                "source": (
                    "seed_metrics"
                    if do_seed and seeded_from_disk
                    else ("seed" if do_seed else "walk_forward")
                ),
                "metrics": chal_metrics,
                "fold_stability": (wf_result or {}).get("fold_stability") if isinstance(wf_result, dict) else None,
                "costs": (wf_result or {}).get("costs") if isinstance(wf_result, dict) else None,
                "copied": result.get("copied") or {},
                "honest_note": HONEST_NOTE,
                "live_edge": False,
                **stamps,
            }
            written = save_champion(pair_u, cfg, record)
            result["champion"] = record
            result["champion_metrics"] = chal_metrics
            result["champion_written"] = written is not None

        if persist:
            _write_json(challenger_meta_path(pair_u, cfg), result)
            _append_history(
                pair_u,
                cfg,
                {
                    "verdict": result["verdict"],
                    "promote": result["promote"],
                    "deltas": (result.get("decision") or {}).get("deltas"),
                    "error": result.get("error"),
                    **stamps,
                },
            )
        return result
    except Exception as exc:  # noqa: BLE001
        if fail_soft:
            return _fail(f"retrain gate failed ({exc})")
        raise


__all__ = [
    "HONEST_NOTE",
    "MODE_IMPROVE",
    "MODE_NON_REGRESSION",
    "PromotionDecision",
    "VERDICT_NULL",
    "VERDICT_PROMOTE",
    "VERDICT_SEED",
    "champion_dir",
    "champion_meta_path",
    "extract_model_metrics",
    "format_retrain_text",
    "load_champion",
    "promotion_decision",
    "retrain_cfg",
    "run_retrain_gate",
    "save_champion",
    "should_promote",
]
