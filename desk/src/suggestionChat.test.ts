import { describe, expect, it } from "vitest";
import { buildClientSuggestionFeed, formatBoardChatLine } from "./suggestionChat";
import type { BoardRow } from "./types";

const base = (over: Partial<BoardRow> = {}): BoardRow => ({
  pair: "EURUSD",
  tf: "H1",
  interval: "1h",
  signal: "HOLD",
  target: null,
  target_text: "\u2014",
  last: 1.13276,
  last_text: "1.13276",
  validity: "OK",
  validity_reason: "ok",
  data: { text: "Live", tone: "ok" },
  session: { text: "Asia", key: "asia" },
  age: "1m",
  last_bar_dhaka: "",
  last_fetch_dhaka: "",
  last_signal_dhaka: "",
  status: "ready",
  rationale: "",
  ...over,
});

describe("suggestionChat", () => {
  it("formats open buy window", () => {
    const line = formatBoardChatLine(base({ signal: "BUY", target: 1.13454 }), { active: "EURUSD" });
    expect(line?.kind).toBe("open_window");
    expect(line?.text).toContain("still within n/a BUY conf");
    expect(line?.text).toContain("Buy and hold");
    expect(line?.text).toContain("Research levels only");
  });

  it("formats window gone", () => {
    const line = formatBoardChatLine(base({ signal: "HOLD", raw_signal: "BUY", gate_reason: "conf low" }), {
      active: "EURUSD",
    });
    expect(line?.kind).toBe("window_gone");
    expect(line?.text).toContain("Don't buy now");
    expect(line?.text).toContain("No actionable target/stop while gated");
  });

  it("orders active first", () => {
    const { lines } = buildClientSuggestionFeed({
      rows: [base({ pair: "BTCUSD", signal: "HOLD" }), base({ pair: "EURUSD", signal: "HOLD", raw_signal: "SELL", gate_reason: "gate" })],
      active: "EURUSD",
    });
    expect(lines[0]?.pair).toBe("EURUSD");
  });

  it("need_train omitted from chat (model strip owns it)", () => {
    const line = formatBoardChatLine(base({ pair: "AUDUSD", signal: "\u2014", status: "need_train" }), {
      active: "EURUSD",
    });
    expect(line).toBeNull();
  });

  it("need_train fingerprint omitted across price ticks", () => {
    const a = formatBoardChatLine(
      base({ pair: "AUDUSD", signal: "\u2014", status: "need_train", last: 0.69555 }),
      { active: "EURUSD" },
    );
    const b = formatBoardChatLine(
      base({ pair: "AUDUSD", signal: "\u2014", status: "need_train", last: 0.6961 }),
      { active: "EURUSD" },
    );
    expect(a).toBeNull();
    expect(b).toBeNull();
  });

  it("paper open monitoring keeps pnl", () => {
    const line = formatBoardChatLine(base({ signal: "BUY" }), {
      active: "EURUSD",
      openPosition: {
        id: "x",
        pair: "EURUSD",
        strategy_id: "brief",
        strategy_name: "Brief",
        status: "open",
        trigger: "BUY",
        confidence: 0.7,
        confidence_text: "70%",
        entry_price: 1.13392,
        entry_price_text: "1.13392",
        entry_time: 0,
        entry_time_dhaka: "",
        exit_price: null,
        exit_price_text: null,
        exit_time_dhaka: null,
        duration: "",
        pnl_price: 0.0001,
        pnl_text: "+0.12R",
        pnl_r: 0.12,
        pnl_basis: "mtm",
        outcome: null,
        exit_reason: null,
        source: "auto",
        size: 1,
        sl: null,
        tp: null,
      } as any,
    });
    expect(line?.kind).toBe("open_pos");
    expect(line?.text).toContain("+0.12R");
  });

  it("weekday mute clarity + stable fingerprint", () => {
    const a = formatBoardChatLine(
      base({ signal: "HOLD", raw_signal: "BUY", gate_reason: "conf=0.44 < min 0.60 Â· weekday_gate blocks Mon,Thu (UTC); today=Thu" }),
      { active: "EURUSD" },
    );
    const b = formatBoardChatLine(
      base({ signal: "HOLD", raw_signal: "BUY", gate_reason: "conf=0.49 < min 0.60 Â· weekday_gate blocks Mon,Thu (UTC); today=Thu" }),
      { active: "EURUSD" },
    );
    expect(a?.text.toLowerCase()).toContain("muted");
    expect(a?.kind).toBe("open_window");
    expect(a?.text.toLowerCase()).toContain("still within");
    expect(a?.text.toLowerCase()).toContain("not opening");
    expect(a?.text.toLowerCase()).toMatch(/buy and hold|sell and hold/);
    expect(a?.text).toContain("lifts Fri UTC");
    expect(a?.id).toBe(b?.id);
  });

  it("below min confidence keeps a directional lean", () => {
    const line = formatBoardChatLine(
      base({ pair: "AUDUSD", signal: "HOLD", raw_signal: "SELL", gate_reason: "conf=0.55 < min 0.60", last: 0.69555, stop: 0.698, target: 0.692 }),
      { active: "EURUSD" },
    );
    expect(line?.kind).toBe("open_window");
    expect(line?.text).toContain("lean");
    expect(line?.text.toLowerCase()).toContain("below min_conf");
    expect(line?.muted_advisory).toBeFalsy();
    expect(line?.below_min_conf).toBe(true);
    expect(line?.text).toContain("Sell and hold @ 0.69555");
  });

  it("BTCUSD pin-skip honesty not Train", () => {
    const line = formatBoardChatLine(base({ pair: "BTCUSD", signal: "\u2014", status: "need_train", last: 84320.22 }), {
      active: "EURUSD",
    });
    expect(line?.kind).toBe("status");
    expect(line?.text.toLowerCase()).toContain("pin-skip");
    expect(line?.text.includes("Train idle") || line?.text.includes("Train (Lab")).toBe(false);
    expect(line?.id.endsWith("|pin_skip")).toBe(true);
  });

  it("open window lists stop target at price", () => {
    const line = formatBoardChatLine(
      base({ signal: "BUY", target: 1.13454, last: 1.13276 }),
      { active: "EURUSD", briefHourly: { signal: "BUY", now: 1.13276, target: 1.13454, stop: 1.13098, confidence: 0.72 } as any },
    );
    expect(line?.kind).toBe("open_window");
    expect(line?.text).toContain("@ 1.13276");
    expect(line?.text).toContain("close at 1.13454");
    expect(line?.text).toContain("Stoploss at 1.13098");
  });

});
