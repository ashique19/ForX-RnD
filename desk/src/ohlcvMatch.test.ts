import assert from "node:assert/strict";
import test from "node:test";
import { ohlcvMatchesActive } from "./ohlcvMatch.ts";

test("ohlcvMatchesActive rejects other pair candles", () => {
  assert.equal(ohlcvMatchesActive({ pair: "EURUSD", interval: "1h", tf: "1h" }, "GBPUSD", "1h"), false);
  assert.equal(ohlcvMatchesActive({ pair: "EURUSD", interval: "1h", tf: "1h" }, "EURUSD", "1h"), true);
  assert.equal(ohlcvMatchesActive({ pair: "eurusd", interval: "1h", tf: "1h" }, "EURUSD", "1h"), true);
  assert.equal(ohlcvMatchesActive({ pair: "EURUSD", interval: "1d", tf: "1d" }, "EURUSD", "1h"), false);
  assert.equal(ohlcvMatchesActive({ pair: "EURUSD", interval: "60m", tf: "1h" }, "EURUSD", "1h"), true);
  assert.equal(ohlcvMatchesActive(null, "EURUSD", "1h"), false);
  assert.equal(ohlcvMatchesActive({ pair: "EURUSD", interval: "1h", tf: "1h" }, "", "1h"), false);
});
