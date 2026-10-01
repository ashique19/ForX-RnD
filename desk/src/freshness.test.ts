import assert from "node:assert/strict";
import test from "node:test";
import { coerceChartInterval, collectTargets, collectWatchlistLightTargets } from "./freshness.ts";

test("active pair refresh is only that pair, with 1h before 1d", () => {
  const targets = collectTargets("EURUSD", "1h", "1h");
  const keys = targets.map((item) => item.pair + ":" + item.interval);
  assert.deepEqual(keys, ["EURUSD:1h", "EURUSD:1d"]);
  assert.ok(keys.indexOf("EURUSD:1h") < keys.indexOf("EURUSD:1d"));
});

test("15m/4h chart or row selections do not schedule those intervals", () => {
  const targets = collectTargets("USDJPY", "4h", "15m");
  const keys = targets.map((item) => item.pair + ":" + item.interval);
  assert.deepEqual(keys, ["USDJPY:1h", "USDJPY:1d"]);
});

test("empty selection refreshes nothing", () => {
  assert.deepEqual(collectTargets("", "1h", "1h"), []);
  assert.deepEqual(collectTargets("   ", "15m", "1d"), []);
});

test("coerceChartInterval allowlists 1h and 1d only", () => {
  assert.equal(coerceChartInterval("1h"), "1h");
  assert.equal(coerceChartInterval("1d"), "1d");
  assert.equal(coerceChartInterval("15m"), "1h");
  assert.equal(coerceChartInterval("4h"), "1h");
  assert.equal(coerceChartInterval(""), "1h");
  assert.equal(coerceChartInterval(null), "1h");
});

test("collectWatchlistLightTargets skips Active and uses 1h only", () => {
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
    { pair: "AUDUSD", interval: "1h" },
    { pair: "USDJPY", interval: "1h" },
  ]);
});

test("collectWatchlistLightTargets empty when only Active", () => {
  assert.deepEqual(collectWatchlistLightTargets([{ pair: "EURUSD" }], "EURUSD"), []);
});

