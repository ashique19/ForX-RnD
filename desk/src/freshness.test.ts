import assert from "node:assert/strict";
import test from "node:test";
import { collectTargets } from "./freshness.ts";

test("active pair refresh is only that pair, with 1h before 1d", () => {
  const targets = collectTargets("EURUSD", "1h", "1h");
  const keys = targets.map((item) => `${item.pair}:${item.interval}`);
  assert.deepEqual(keys, ["EURUSD:1h", "EURUSD:1d"]);
  assert.ok(keys.indexOf("EURUSD:1h") < keys.indexOf("EURUSD:1d"));
});

test("chart timeframe sits between 1h and 1d for the selected pair", () => {
  const targets = collectTargets("USDJPY", "4h", "4h");
  const keys = targets.map((item) => `${item.pair}:${item.interval}`);
  assert.deepEqual(keys, ["USDJPY:1h", "USDJPY:4h", "USDJPY:1d"]);
});

test("empty selection refreshes nothing", () => {
  assert.deepEqual(collectTargets("", "1h", "1h"), []);
  assert.deepEqual(collectTargets("   ", "15m", "1d"), []);
});
