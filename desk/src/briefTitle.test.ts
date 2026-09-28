import assert from "node:assert/strict";
import test from "node:test";
import { collapsedBriefTitle, confidencePercent } from "./briefTitle.ts";

test("collapsed title is pair, side, and integer confidence", () => {
  assert.equal(collapsedBriefTitle("EURUSD", "SELL bias", 0.7), "EURUSD (Sell : 70%)");
  assert.equal(collapsedBriefTitle("EURUSD", "BUY bias", 0.4696), "EURUSD (Buy : 47%)");
  assert.equal(collapsedBriefTitle("GBPUSD", "HOLD", 0), "GBPUSD (Hold : 0%)");
  assert.equal(collapsedBriefTitle("EURUSD", "SELL bias", 70), "EURUSD (Sell : 70%)");
});

test("gated HOLD exposes raw BUY/SELL class", () => {
  assert.equal(
    collapsedBriefTitle("EURUSD", "HOLD", 0.38, "SELL"),
    "EURUSD (SELL 38% gated → HOLD)",
  );
  assert.equal(
    collapsedBriefTitle("EURUSD", "HOLD", 0.42, "BUY"),
    "EURUSD (BUY 42% gated → HOLD)",
  );
  assert.equal(
    collapsedBriefTitle("EURUSD", "HOLD", null, "SELL"),
    "EURUSD (SELL gated → HOLD)",
  );
  // Ungated: raw matches bias side — keep the clean form.
  assert.equal(
    collapsedBriefTitle("EURUSD", "SELL bias", 0.5, "SELL"),
    "EURUSD (Sell : 50%)",
  );
  // Model itself said HOLD — no gated wording.
  assert.equal(
    collapsedBriefTitle("EURUSD", "HOLD", 0.38, "HOLD"),
    "EURUSD (Hold : 38%)",
  );
});

test("missing confidence or side does not invent a percent", () => {
  assert.equal(collapsedBriefTitle("EURUSD", "SELL bias", null), "EURUSD (Sell)");
  assert.equal(collapsedBriefTitle("EURUSD", "SELL bias", undefined), "EURUSD (Sell)");
  assert.equal(collapsedBriefTitle("EURUSD", "NO LIVE BIAS", 0.7), "EURUSD");
  assert.equal(collapsedBriefTitle("EURUSD", "-", null), "EURUSD");
  assert.equal(collapsedBriefTitle("  ", "SELL bias", 0.7), "Signal brief");
  assert.equal(confidencePercent(Number.NaN), null);
  assert.equal(confidencePercent(150), null);
});
