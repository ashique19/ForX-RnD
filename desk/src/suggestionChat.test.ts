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
    expect(line?.text).toContain("not an order");
  });

  it("formats window gone", () => {
    const line = formatBoardChatLine(base({ signal: "HOLD", raw_signal: "BUY", gate_reason: "conf low" }), {
      active: "EURUSD",
    });
    expect(line?.kind).toBe("window_gone");
    expect(line?.text).toContain("Don't buy now");
  });

  it("orders active first", () => {
    const lines = buildClientSuggestionFeed({
      rows: [base({ pair: "BTCUSD", signal: "HOLD" }), base({ pair: "EURUSD", signal: "HOLD", raw_signal: "SELL", gate_reason: "gate" })],
      active: "EURUSD",
    });
    expect(lines[0]?.pair).toBe("EURUSD");
  });
});
