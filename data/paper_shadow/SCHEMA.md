# Paper-live shadow journal schema (Stage-2)
# Live-edge roadmap: _ROADMAP_LIVE_EDGE_GRADE_20261002.txt
# Written: 2026-10-02 Asia/Dhaka
# Status: foundation only. NOT a Replay. NOT a promote/gate flip.

## Path
- JSONL: `data/paper_shadow/journal.jsonl` (runtime; gitignored)
- Module: `forex_lab/paper_shadow.py` (fail-soft append)
- Wire: `api/paperdesk.py` on paper open/close (manual board CTA + auto desk)
- Weekly stub: `scripts/shadow_weekly_compare.py`

## Event kinds
- `open`  — paper fill / journal open (board CTA or desk auto)
- `close` — paper close (manual or auto barrier/duration/opposite)
- `desk_call` — board advisory stamped for later (optional; schema-ready)

## Required / common fields (one JSON object per line)
| field | type | notes |
|-------|------|-------|
| schema | str | always `paper_shadow_v1` |
| event | str | open \| close \| desk_call |
| ts_utc | str | ISO-8601 UTC with Z |
| ts_dhaka | str | `YYYY-MM-DD HH:MM:SS Asia/Dhaka` |
| pair | str | e.g. EURUSD |
| side | str | BUY \| SELL \| CLOSE |
| conf | float\|null | model confidence at event |
| muted | bool | weekday / live mute advisory at event |
| below_min | bool | below min_conf advisory at event |
| entry_mid | float\|null | entry mid (cached last/close); null on pure desk_call |
| exit_mid | float\|null | exit mid on close; else null |
| bid | float\|null | tip Bid if column present; else null (never invented) |
| ask | float\|null | tip Ask if column present; else null |
| sl | float\|null | intended stop |
| tp | float\|null | intended target |
| source | str | `board` (manual / Journal CTA) \| `desk` (auto paper) |
| position_id | str\|null | paper position id when known |
| fill_id | str\|null | paper fill id when known |
| exit_reason | str\|null | sl \| tp \| duration \| opposite \| manual \| … |
| advisory_id | str\|null | board line id when desk_call / CTA linked |
| notes | str | short free text; keep tiny |
| assumed_spread_pips | float | Stage-3: config spread_pips (usually 1.0 RT) |
| assumed_slippage_pips | float | Stage-3: replay.slippage_pips (usually 0.2) |
| assumed_cost_pips | float\|null | Stage-3: RT mid-only cost when BA missing; null when BA present |
| ba_available | bool | Stage-3: True only when tip Bid+Ask both present |
| measured_spread_pips | float\|null | Stage-3: (ask-bid)/pip when BA present; else null |
| notes_cost | str\|null | Stage-3: ba_missing_mid_only_assumed_rt \| ba_present_bbo_is_spread |

## Rules
- Append is **fail-soft**: never raise into the desk; never block BUY/SELL/CLOSE.
- Do **not** count STALE/MISSING/ERROR submits as fills (paper_order already blocks those).
- Do **not** invent bid/ask. Mid-only fills leave bid/ask null.
- Stage-3: when BA missing, stamp `assumed_cost_pips` (spread_pips + 2*slippage). When BA present, leave assumed_cost_pips null and record measured_spread_pips.
- Cost probe: `scripts/cost_honesty_probe.py` -> `data/paper_shadow/cost_probe_YYYYMMDD.json` + `_COST_HONESTY_RESULT_YYYYMMDD.txt`.
- No YAML gate / promote / min_conf / sessions / portfolio flips from this journal.
- exit_hold stays at research KEEP (`min_bars_before_sl: 2`).

## Weekly compare (stub)
Run `scripts/shadow_weekly_compare.py` to print shadow counts from the JSONL.
Replay-pin diff is intentionally stubbed until Stage-2 has N weeks of fills.
