import assert from "node:assert/strict";
import test from "node:test";
import {
  REPLAY_ADVISORY,
  bookLabel,
  formatDrawdown,
  formatProfitFactor,
  formatReturn,
  formatTrades,
  formatWinRate,
  mergeLatest,
  orderedBooks,
  replayWhen,
  verdictLabel,
} from "./scoreboard.ts";
import type { ReplayJob, ReplayLatest, ScoreboardBook } from "./types.ts";

test("scoreboard books stay champion, challenger, SMA", () => {
  const rows: ScoreboardBook[] = [
    { book: "sma", n_trades: 1, win_rate: 0.2, expectancy: -0.01, net_pnl: -0.01, max_drawdown: -0.2, profit_factor: 0.4 },
    { book: "champion", n_trades: 2, win_rate: 0.5, expectancy: 0.001, net_pnl: 0.002, max_drawdown: -0.04, profit_factor: 1.2 },
    { book: "challenger", n_trades: 3, win_rate: null, expectancy: null, net_pnl: 0, max_drawdown: null, profit_factor: "inf" },
  ];
  assert.deepEqual(
    orderedBooks(rows).map((row) => row.book),
    ["champion", "challenger", "sma"],
  );
  assert.equal(bookLabel("sma"), "SMA");
  assert.equal(formatWinRate(0.555), "55.5%");
  assert.equal(formatWinRate(null), "—");
  assert.equal(formatReturn(0.0012), "+0.0012");
  assert.equal(formatReturn(-0.01), "-0.0100");
  assert.equal(formatReturn(Number.NaN), "—");
  assert.equal(formatDrawdown(-0.032), "-3.2%");
  assert.equal(formatProfitFactor("inf"), "∞");
  assert.equal(formatProfitFactor(1.25), "1.25");
  assert.equal(formatProfitFactor(null), "—");
  assert.equal(formatTrades(null), "—");
  assert.equal(formatTrades(8), "8");
});

test("promotion copy stays advisory", () => {
  assert.match(REPLAY_ADVISORY, /advisory research/);
  assert.match(REPLAY_ADVISORY, /weekly retrain gate/);
  assert.match(REPLAY_ADVISORY, /does not overwrite the live champion/);
  assert.equal(verdictLabel({ verdict: "promote", promote: true, reasons: [] }), "Promote — advisory only");
  assert.equal(verdictLabel({ verdict: "null", promote: false, reasons: [] }), "Null — keep the live champion");
  assert.equal(verdictLabel({ verdict: "seed", promote: false, reasons: [] }), "Seed — not a promotion");
  assert.equal(verdictLabel(null), "No promotion verdict");
  assert.equal(replayWhen({ finished_at_dhaka: "2026-09-23 21:05 Asia/Dhaka", as_of_dhaka: "2015-06-01 18:00 Asia/Dhaka" }), "2026-09-23 21:05 Asia/Dhaka");
  assert.equal(replayWhen({ as_of_dhaka: "2015-06-01 18:00 Asia/Dhaka" }), "window end 2015-06-01 18:00 Asia/Dhaka");
});

test("a finished replay replaces the running row and clears a stale error", () => {
  const running: ReplayJob = {
    job_id: "run",
    kind: "replay",
    status: "running",
    phase: "replay",
    pair: "EURUSD",
    interval: "1h",
    fraction: 0.4,
    message: "Walking",
    as_of_dhaka: null,
    error: null,
    calendar_note: null,
    promotion_line: null,
    source: null,
    bid_ask: null,
    rows: null,
    report: null,
  };
  const failed: ReplayJob = { ...running, job_id: "bad", status: "error", error: "Train failed" };
  const prev: ReplayLatest = {
    pair: "EURUSD",
    interval: "1h",
    interval_match: true,
    advisory: REPLAY_ADVISORY,
    job: null,
    recent_error: failed,
    running,
  };
  const done: ReplayJob = {
    ...running,
    status: "done",
    phase: "done",
    promotion_line: "Promotion null",
    scoreboard: [],
  };
  const next = mergeLatest(prev, done, "EURUSD", "1h");
  assert.equal(next.job?.status, "done");
  assert.equal(next.running, null);
  assert.equal(next.recent_error, null);
  assert.equal(next.interval_match, true);

  const otherTf = mergeLatest(prev, { ...done, interval: "4h" }, "EURUSD", "1h");
  assert.equal(otherTf.interval_match, false);
});
