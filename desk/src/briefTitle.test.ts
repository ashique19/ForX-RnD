import assert from "node:assert/strict";
import test from "node:test";
import { collapsedBriefTitle, confidencePercent } from "./briefTitle.ts";

test("collapsed title is pair, side, and integer confidence", () => {
  assert.equal(collapsedBriefTitle("EURUSD", "SELL bias", 0.7), "EURUSD (Sell : 70%)");
  assert.equal(collapsedBriefTitle("EURUSD", "BUY bias", 0.4696), "EURUSD (Buy : 47%)");
  assert.equal(collapsedBriefTitle("GBPUSD", "HOLD", 0), "GBPUSD (Hold : 0%)");
  assert.equal(collapsedBriefTitle("EURUSD", "SELL bias", 70), "EURUSD (Sell : 70%)");
});

test("missing confidence or side does not invent a percent", () => {
  assert.equal(collapsedBriefTitle("EURUSD", "SELL bias", null), "EURUSD (Sell)");
  assert.equal(collapsedBriefTitle("EURUSD", "SELL bias", undefined), "EURUSD (Sell)");
  assert.equal(collapsedBriefTitle("EURUSD", "NO LIVE BIAS", 0.7), "EURUSD");
  assert.equal(collapsedBriefTitle("EURUSD", "—", null), "EURUSD");
  assert.equal(collapsedBriefTitle("  ", "SELL bias", 0.7), "Signal brief");
  assert.equal(confidencePercent(Number.NaN), null);
  assert.equal(confidencePercent(150), null);
});
