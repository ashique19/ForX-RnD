import assert from "node:assert/strict";
import test from "node:test";
import { alertClauses, flipBadges, formatAlertBanner } from "./alertBanner.ts";
import type { AlertItem } from "./types.ts";

function flip(pair: string, from: string, to: string): AlertItem {
  return { kind: "flip", pair, message: `${pair} ${from} → ${to}` };
}

test("same-pair flips collapse to one clause", () => {
  const alerts = [flip("EURUSD", "SELL", "HOLD"), flip("EURUSD", "BUY", "SELL"), flip("EURUSD", "SELL", "BUY")];
  assert.equal(formatAlertBanner(alerts), "SELL→BUY → BUY→SELL → EURUSD: SELL→HOLD");
  assert.deepEqual(alertClauses(alerts), [
    { type: "flips", pair: "EURUSD", changes: ["SELL→HOLD", "BUY→SELL", "SELL→BUY"] },
  ]);
});

test("different pairs stay separate and a single flip keeps the pair beside the change", () => {
  const alerts = [flip("EURUSD", "SELL", "HOLD"), flip("GBPUSD", "BUY", "SELL")];
  assert.equal(formatAlertBanner(alerts), "EURUSD SELL→HOLD · GBPUSD BUY→SELL");
});

test("a non-flip note stays intact between flip groups", () => {
  const alerts: AlertItem[] = [
    flip("EURUSD", "SELL", "HOLD"),
    { kind: "stale", pair: "EURUSD", message: "EURUSD 1h data STALE — last bar old" },
    flip("EURUSD", "HOLD", "BUY"),
    { kind: "session", pair: "", message: "FX session closed" },
  ];
  assert.equal(
    formatAlertBanner(alerts),
    "EURUSD SELL→HOLD · EURUSD 1h data STALE — last bar old · EURUSD HOLD→BUY",
  );
});

test("only the first three alerts are shown", () => {
  const alerts = [
    flip("EURUSD", "SELL", "HOLD"),
    flip("EURUSD", "BUY", "SELL"),
    flip("GBPUSD", "SELL", "BUY"),
    flip("USDJPY", "HOLD", "BUY"),
  ];
  assert.equal(formatAlertBanner(alerts), "BUY→SELL → EURUSD: SELL→HOLD · GBPUSD SELL→BUY");
});

test("ascii arrows and extra spaces still group", () => {
  const alerts: AlertItem[] = [
    { kind: "flip", pair: "USDJPY", message: "  USDJPY   HOLD  ->   SELL  " },
    { kind: "flip", pair: "USDJPY", message: "USDJPY SELL -> BUY" },
  ];
  assert.equal(formatAlertBanner(alerts), "SELL→BUY → USDJPY: HOLD→SELL");
});

test("badges read oldest to newest, with the pair only on the latest pill", () => {
  const [grouped] = alertClauses([
    flip("EURUSD", "SELL", "HOLD"),
    flip("EURUSD", "BUY", "SELL"),
    flip("EURUSD", "SELL", "BUY"),
  ]);
  assert.equal(grouped.type, "flips");
  if (grouped.type !== "flips") return;
  assert.deepEqual(
    grouped.changes,
    ["SELL→HOLD", "BUY→SELL", "SELL→BUY"],
    "ingest order stays newest first",
  );
  assert.deepEqual(flipBadges(grouped), [
    { pair: null, change: "SELL→BUY", latest: false },
    { pair: null, change: "BUY→SELL", latest: false },
    { pair: "EURUSD", change: "SELL→HOLD", latest: true },
  ]);

  const [single] = alertClauses([flip("GBPUSD", "BUY", "SELL")]);
  assert.equal(single.type, "flips");
  if (single.type !== "flips") return;
  assert.deepEqual(flipBadges(single), [{ pair: "GBPUSD", change: "BUY→SELL", latest: true }]);
});

test("empty and non-flip strips are unchanged", () => {
  assert.equal(formatAlertBanner([]), "No active alerts");
  assert.equal(
    formatAlertBanner([{ kind: "proximity", pair: "EURUSD", message: "EURUSD 1h target hit proximity — 1.10000 within 8 pips of TP" }]),
    "EURUSD 1h target hit proximity — 1.10000 within 8 pips of TP",
  );
});
