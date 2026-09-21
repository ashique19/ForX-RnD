# ForX Decision — static HTML mock

Self-contained approval mock for the ForX-RnD **Decision** trader screen (pre-React rebuild).

Open `index.html` in any browser (offline). Inline CSS/JS; no build step.

## Information architecture

| Region | Role |
|--------|------|
| **Top nav** | Wordmark ForX · mode tabs (Decision active) · live Asia/Dhaka clock · user chip |
| **Alerts strip** | One-line compact status above the board |
| **Watchlist** (left) | Pair / TF / Signal / Target / Data / Session / Last · selected row · **+ Add pair** |
| **Aux** (left, collapsed) | Nav help / Workspace / Lab TF hints behind chevron |
| **Signal brief** (right top) | Bias headline · **Hourly** + **Daily** cards (levels, forecasters, ranges, scenario line) |
| **Chart** (right bottom) | TF chips · Realtime + reload · OHLC · static SVG candles/EMA/volume · TP/SL guides |
| **MOCK badge** | Fixed corner marker for sample data |

Light desk theme (white / soft gray, 8px grid, Inter/system UI). Not Streamlit; no hero banner or disclaimer card.

Checked in at `design/decision-mock/` as the Phase 1 visual reference for `desk/`. The React screen uses live `forex_lab` data; this file stays the static approval mock.
