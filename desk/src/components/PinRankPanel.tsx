import { useEffect, useState } from "react";
import { api } from "../api";

type PinRow = {
  rank: number;
  pair: string;
  job_id: string;
  pf: number;
  wr: number;
  n: number;
  dd: number;
  total_return: number;
  sma_pf: number;
  qa: string;
  promote: null;
  note?: string;
};

type PinPayload = {
  as_of_dhaka?: string | null;
  disclaimer?: string;
  rows?: PinRow[];
  freezes?: Record<string, string>;
};

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function fmtPf(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(2);
}

export function PinRankPanel() {
  const [data, setData] = useState<PinPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const payload = await api.pinScoreboard();
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const rows = Array.isArray(data?.rows) ? data!.rows! : [];

  return (
    <section className="pin-rank panel" aria-label="Research pin scoreboard">
      <div className="panel-hd">
        <span className="tag">Research pins</span>
        <strong>After-cost pin rank</strong>
        {data?.as_of_dhaka ? <span className="replay-when">{data.as_of_dhaka}</span> : null}
      </div>
      <p className="pin-rank-disclaimer">
        {data?.disclaimer || "Research-only. Promote=null. Live Active stays EURUSD."}
      </p>
      {error ? <p className="replay-mismatch">{error}</p> : null}
      {rows.length ? (
        <div className="table-wrap">
          <table className="pin-rank-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Pair</th>
                <th>PF</th>
                <th>WR</th>
                <th>n</th>
                <th>DD</th>
                <th>QA</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.pair} className={row.qa === "ACTIVE" ? "is-active" : undefined}>
                  <td>{row.rank}</td>
                  <td>
                    <strong>{row.pair}</strong>
                  </td>
                  <td>{fmtPf(row.pf)}</td>
                  <td>{fmtPct(row.wr)}</td>
                  <td>{row.n}</td>
                  <td>{fmtPct(row.dd)}</td>
                  <td>
                    <span className={row.qa === "ACTIVE" ? "tag active" : "tag"}>{row.qa}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : !error ? (
        <p className="muted">No pin rows.</p>
      ) : null}
      {data?.freezes ? (
        <p className="pin-rank-freezes muted">
          Freezes: {Object.entries(data.freezes).map(([k, v]) => `${k}=${v}`).join(" · ")}
        </p>
      ) : null}
    </section>
  );
}
