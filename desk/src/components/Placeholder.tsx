import { useState } from "react";
import type { Mode } from "../types";
import { api } from "../api";

const COPY: Record<Exclude<Mode, "decision" | "learnings" | "calendar">, { title: string; body: string }> = {
  paper: {
    title: "Paper",
    body: "Paper Buy/Sell stays on the Streamlit desk and the local PaperBroker. This screen does not submit orders.",
  },
  lab: {
    title: "Lab",
    body: "Run pipeline trains, then backtests, then generates signals. It does not run on the Decision data timer. Update now on Decision only refreshes watchlist prices. Streamlit on port 8501 is still the full Lab until cutover.",
  },
  awareness: {
    title: "Awareness",
    body: "Source health (OHLCV, news, model, calendar, FRED) stays on the Streamlit Awareness panel for this phase.",
  },
};

export function Placeholder({ mode, pair }: { mode: Exclude<Mode, "decision" | "learnings" | "calendar">; pair: string }) {
  const copy = COPY[mode];
  const [fetchBars, setFetchBars] = useState(false);
  const [log, setLog] = useState("");
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setLog("Running… this can take several minutes.");
    try {
      const result = await api.pipeline(pair, fetchBars);
      const lines = result.steps.map((step) => `${step.ok ? "OK" : "FAIL"} ${step.step}\n${step.log}`.trim());
      setLog(lines.join("\n\n") || (result.ok ? "OK" : `Failed at ${result.failed}`));
    } catch (err) {
      setLog(err instanceof Error ? err.message : "Pipeline failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel placeholder">
      <h2>{copy.title}</h2>
      <p>{copy.body}</p>
      {mode === "lab" && (
        <>
          <div className="row">
            <button className="btn primary" type="button" onClick={() => void run()} disabled={busy}>
              {busy ? "Running…" : `Run pipeline · ${pair}`}
            </button>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "#344054" }}>
              <input
                type="checkbox"
                checked={fetchBars}
                onChange={(e) => setFetchBars(e.target.checked)}
              />
              Also fetch
            </label>
          </div>
          {log && <pre className="log">{log}</pre>}
        </>
      )}
    </section>
  );
}
