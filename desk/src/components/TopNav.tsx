import { useEffect, useState } from "react";
import type { Mode } from "../types";

const MODES: { id: Mode; label: string }[] = [
  { id: "decision", label: "Decision" },
  { id: "calendar", label: "Calendar" },
  { id: "paper", label: "Portfolio" },
  { id: "lab", label: "Lab" },
  { id: "awareness", label: "Awareness" },
  { id: "learnings", label: "Learnings" },
];

function formatDhaka(now: Date): string {
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Dhaka",
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const parts = fmt.formatToParts(now);
  const g = (t: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === t)?.value ?? "";
  return `${g("weekday")} ${g("day")} ${g("month")} ${g("year")} · ${g("hour")}:${g("minute")}:${g("second")}`;
}

export function TopNav({ mode, onMode }: { mode: Mode; onMode: (mode: Mode) => void }) {
  const [clock, setClock] = useState(() => formatDhaka(new Date()));
  useEffect(() => {
    const id = window.setInterval(() => setClock(formatDhaka(new Date())), 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <header className="topnav">
      <a className="brand" href="#decision" onClick={(e) => { e.preventDefault(); onMode("decision"); }}>
        <span className="brand-mark">FX</span>
        ForX
      </a>
      <nav className="modes" aria-label="Modes">
        {MODES.map((item) => (
          <button
            key={item.id}
            className={item.id === mode ? "mode active" : "mode"}
            type="button"
            onClick={() => onMode(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      <div className="nav-right">
        <div className="clock" title="Asia/Dhaka">
          <span className="dot" aria-hidden="true" />
          <span>{clock}</span>
          <span className="tz">Dhaka</span>
        </div>
        <div className="user-chip">
          <span className="avatar">AI</span>
          <span>Ashiqul Islam</span>
        </div>
      </div>
    </header>
  );
}
