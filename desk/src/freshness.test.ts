import assert from "node:assert/strict";
import test from "node:test";
import { coerceChartInterval, collectTargets, collectWatchlistLightTargets, pickIdleTrainPair } from "./freshness.ts";

test("active pair refresh is 1h only (no 1d on Decision)", () => {
  const targets = collectTargets("EURUSD", "1h", "1h");
  const keys = targets.map((item) => item.pair + ":" + item.interval);
  assert.deepEqual(keys, ["EURUSD:1h"]);
});

test("15m/4h chart or row selections still schedule only 1h", () => {
  const targets = collectTargets("USDJPY", "4h", "15m");
  const keys = targets.map((item) => item.pair + ":" + item.interval);
  assert.deepEqual(keys, ["USDJPY:1h"]);
});

test("empty selection refreshes nothing", () => {
  assert.deepEqual(collectTargets("", "1h", "1h"), []);
  assert.deepEqual(collectTargets("   ", "15m", "1d"), []);
});

test("coerceChartInterval Decision allowlist is 1h only", () => {
  assert.equal(coerceChartInterval("1h"), "1h");
  assert.equal(coerceChartInterval("1d"), "1h");
  assert.equal(coerceChartInterval("15m"), "1h");
  assert.equal(coerceChartInterval("4h"), "1h");
  assert.equal(coerceChartInterval(""), "1h");
  assert.equal(coerceChartInterval(null), "1h");
});

test("collectWatchlistLightTargets refreshes ALL pairs at 1h including Active", () => {
  const targets = collectWatchlistLightTargets(
    [
      { pair: "EURUSD", interval: "1h" },
      { pair: "AUDUSD", interval: "1h" },
      { pair: "USDJPY", interval: "4h" },
      { pair: "EURUSD", interval: "1d" },
    ],
    "EURUSD",
  );
  assert.deepEqual(targets, [
    { pair: "EURUSD", interval: "1h" },
    { pair: "AUDUSD", interval: "1h" },
    { pair: "USDJPY", interval: "1h" },
  ]);
});

test("collectWatchlistLightTargets Active-only still gets 1h", () => {
  assert.deepEqual(collectWatchlistLightTargets([{ pair: "EURUSD" }], "EURUSD"), [
    { pair: "EURUSD", interval: "1h" },
  ]);
});


test("pickIdleTrainPair prefers USDJPY/GBPUSD among idle need_train", () => {
  const pair = pickIdleTrainPair(
    [
      { pair: "EURUSD", status: "ready" },
      { pair: "AUDUSD", status: "need_train" },
      { pair: "USDJPY", status: "need_train" },
      { pair: "GBPUSD", status: "need_train" },
    ],
    "EURUSD",
  );
  assert.equal(pair, "USDJPY");
});

test("pickIdleTrainPair skips Active even if need_train", () => {
  assert.equal(
    pickIdleTrainPair([{ pair: "EURUSD", status: "need_train" }, { pair: "AUDUSD", status: "need_train" }], "EURUSD"),
    "AUDUSD",
  );
});

test("pickIdleTrainPair null when none need train", () => {
  assert.equal(pickIdleTrainPair([{ pair: "USDJPY", status: "ready" }], "EURUSD"), null);
});
