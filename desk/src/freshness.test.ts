import assert from "node:assert/strict";
import test from "node:test";
import { collectTargets } from "./freshness.ts";
import type { BoardRow } from "./types.ts";

function row(pair: string, interval: string): BoardRow {
  return {
    pair,
    tf: interval,
    interval,
    signal: "—",
    target: null,
    target_text: "—",
    last: null,
    last_text: "—",
    validity: "OK",
    validity_reason: "",
    data: { text: "Live", tone: "ok" },
    session: { text: "—", key: "off" },
    age: "",
    last_bar_dhaka: "",
    last_fetch_dhaka: "",
    last_signal_dhaka: "",
    status: "ready",
    rationale: "",
  };
}

test("active pair refresh includes 1h and 1d; other pairs stay on their interval", () => {
  const targets = collectTargets(
    [row("EURUSD", "1h"), row("GBPUSD", "15m"), row("USDJPY", "4h")],
    "EURUSD",
    "1h",
    "1h",
  );
  const keys = targets.map((item) => `${item.pair}:${item.interval}`);
  assert.deepEqual(keys, ["EURUSD:1h", "GBPUSD:15m", "USDJPY:4h", "EURUSD:1d"]);
  assert.ok(!keys.includes("GBPUSD:1d"));
  assert.ok(!keys.includes("USDJPY:1h"));
  assert.ok(keys.indexOf("EURUSD:1h") < keys.indexOf("EURUSD:1d"));
});

test("active pair not yet on the board still requests 1h before 1d", () => {
  const targets = collectTargets([row("GBPUSD", "1h")], "USDJPY", "4h", "4h");
  const keys = targets.map((item) => `${item.pair}:${item.interval}`);
  assert.deepEqual(keys, ["GBPUSD:1h", "USDJPY:1h", "USDJPY:4h", "USDJPY:1d"]);
});
