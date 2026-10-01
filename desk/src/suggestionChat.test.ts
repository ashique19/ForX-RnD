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
    expect(line?.text).toContain("open BUY window");
    expect(line?.text).toContain("research levels from gates");
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

  it("need_train placeholder becomes quiet line", () => {
    const line = formatBoardChatLine(base({ pair: "AUDUSD", signal: "\u2014", status: "need_train" }), {
      active: "EURUSD",
    });
    expect(line?.kind).toBe("status");
    expect(line?.text).toContain("no trained flash yet");
    expect(line?.text).toContain("watching @");
    expect(line?.text.includes("\u2014")).toBe(false);
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
});
