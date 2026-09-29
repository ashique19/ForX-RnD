import assert from "node:assert/strict";
import test from "node:test";
import {
  EMPTY_POSITIONS,
  ENTRY_BUY_COLOR,
  ENTRY_SELL_COLOR,
  MARKER_HIT_PX,
  POSITION_SL_COLOR,
  POSITION_TP_COLOR,
  buildDrawnPositions,
  dhakaLabelToUnix,
  entryUnix,
  markerTime,
  pickPositionId,
  positionIdFromObject,
} from "./openPositions.ts";
import type { PortfolioRow } from "./types.ts";

function row(over: Partial<PortfolioRow> = {}): PortfolioRow {
  return {
    id: "pos_brief",
    pair: "EURUSD",
    status: "open",
    trigger: "BUY",
    confidence: 0.7,
    confidence_text: "70%",
    entry_price: 1.1,
    entry_price_text: "1.10000",
    sl: 1.09,
    tp: 1.12,
    entry_time: 1_790_157_600,
    entry_time_dhaka: "2026-09-23 16:00:00 Asia/Dhaka",
    exit_price: null,
    exit_price_text: "—",
    exit_time_dhaka: null,
    duration: "—",
    pnl_price: null,
    pnl_text: "—",
    pnl_r: null,
    pnl_basis: null,
    outcome: null,
    exit_reason: null,
    source: "auto",
    strategy_id: "brief",
    strategy_name: "Brief",
    size: 1,
    ...over,
  };
}

const bars = [
  { time: 1_790_154_000 },
  { time: 1_790_157_600 },
  { time: 1_790_161_200 },
];

test("Dhaka display time converts to unix seconds", () => {
  assert.equal(dhakaLabelToUnix("2026-09-23 16:00:00 Asia/Dhaka"), 1_790_157_600);
  assert.equal(dhakaLabelToUnix("2026-09-23 10:00:00 UTC"), 1_790_157_600);
  assert.equal(dhakaLabelToUnix("n/a"), null);
  assert.equal(entryUnix(row({ entry_time: null })), 1_790_157_600);
  assert.equal(entryUnix(row({ entry_time: 1_790_157_600_000 })), 1_790_157_600);
});

test("entry marker snaps to the bar that contains the fill", () => {
  const step = 3600;
  assert.equal(markerTime(1_790_157_600, bars, step), 1_790_157_600);
  assert.equal(markerTime(1_790_158_800, bars, step), 1_790_157_600);
  assert.equal(markerTime(1_790_154_000 - 10, bars, step), 1_790_154_000);
  assert.equal(markerTime(1_790_154_000 - 3601, bars, step), null);
  assert.equal(markerTime(1_790_161_200 + 3601, bars, step), null);
  assert.equal(markerTime(null, bars, step), null);
});

test("only the active pair's open positions are drawn", () => {
  const other = row({ id: "pos_gbp", pair: "GBPUSD", strategy_id: "brief" });
  const closed = row({ id: "pos_old", status: "closed" });
  const sell = row({
    id: "pos_con",
    trigger: "SELL",
    strategy_id: "consensus",
    strategy_name: "Consensus",
    sl: 1.12,
    tp: 1.08,
    entry_time: null,
    entry_time_dhaka: "not a time",
  });
  const drawn = buildDrawnPositions([row(), other, closed, sell], "EURUSD", "EURUSD", bars, "1h");
  assert.deepEqual(
    drawn.lines.map((line) => line.title),
    ["Open BUY Brief", "SL Brief", "TP Brief", "Open SELL Consensus", "SL Consensus", "TP Consensus"],
  );
  assert.equal(drawn.lines[0].color, ENTRY_BUY_COLOR);
  assert.equal(drawn.lines[0].style, "solid");
  assert.equal(drawn.lines[1].color, POSITION_SL_COLOR);
  assert.equal(drawn.lines[1].style, "dotted");
  assert.equal(drawn.lines[2].color, POSITION_TP_COLOR);
  assert.equal(drawn.lines[3].color, ENTRY_SELL_COLOR);
  assert.equal(drawn.markers.length, 1);
  assert.equal(drawn.markers[0].positionId, "pos_brief");
  assert.equal(drawn.markers[0].time, 1_790_157_600);
  assert.equal(drawn.markers[0].text, "Open BUY Brief");
  assert.equal(positionIdFromObject(drawn.lines[0].objectId), "pos_brief");
  assert.equal(positionIdFromObject(drawn.markers[0].objectId), "pos_brief");
  assert.equal(positionIdFromObject("pattern"), null);
});

test("hides when the chart pair is not the active pair or nothing is open", () => {
  assert.equal(buildDrawnPositions([row()], "EURUSD", "GBPUSD", bars, "1h"), EMPTY_POSITIONS);
  assert.equal(buildDrawnPositions([row({ entry_price: null })], "eurusd", "EURUSD", bars, "1h"), EMPTY_POSITIONS);
  assert.equal(buildDrawnPositions([], "EURUSD", "EURUSD", bars, "1h"), EMPTY_POSITIONS);
  const noStops = buildDrawnPositions([row({ sl: null, tp: null })], "EURUSD", null, bars, "1d");
  assert.deepEqual(
    noStops.lines.map((line) => line.title),
    ["Open BUY Brief"],
  );
});

test("a click on the entry, stop, target, or open-time marker selects that book", () => {
  const drawn = buildDrawnPositions(
    [
      row(),
      row({
        id: "pos_con",
        trigger: "SELL",
        strategy_name: "Consensus",
        entry_price: 1.2,
        sl: 1.22,
        tp: 1.16,
      }),
    ],
    "EURUSD",
    "EURUSD",
    bars,
    "1h",
  );
  const yOf = (price: number) => price * 100;
  assert.equal(pickPositionId(drawn.hits, 110, bars[1].time, yOf), "pos_brief");
  assert.equal(pickPositionId(drawn.hits, 109, null, yOf), "pos_brief");
  assert.equal(pickPositionId(drawn.hits, 112, bars[0].time, yOf), "pos_brief");
  assert.equal(pickPositionId(drawn.hits, 120, bars[1].time, yOf), "pos_con");
  assert.equal(pickPositionId(drawn.hits, 116, null, yOf), "pos_con");
  assert.equal(pickPositionId(drawn.hits, 150, bars[1].time, yOf), null);
  const one = buildDrawnPositions([row()], "EURUSD", "EURUSD", bars, "1h");
  const marker = one.hits.find((hit) => hit.time != null);
  assert.ok(marker);
  assert.equal(marker.tolerance, MARKER_HIT_PX);
  assert.equal(pickPositionId(one.hits, yOf(1.1) + MARKER_HIT_PX, bars[1].time, yOf), "pos_brief");
  assert.equal(pickPositionId(one.hits, yOf(1.1) + MARKER_HIT_PX, bars[0].time, yOf), null);
});
