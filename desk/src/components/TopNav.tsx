import { useEffect, useState, type ReactNode } from "react";
import type { Mode } from "../types";

const MODES: { id: Mode; label: string; short?: string }[] = [
  { id: "decision", label: "Decision", short: "Dec" },
  { id: "calendar", label: "Calendar", short: "Cal" },
  { id: "paper", label: "Portfolio", short: "Port" },
  { id: "lab", label: "Lab" },
  { id: "awareness", label: "Awareness", short: "Aware" },
  { id: "learnings", label: "Learnings", short: "Learn" },
];

function formatDhaka(now: Date): string {
  const fmt = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Dhaka",
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const parts = fmt.formatToParts(now);
  const g = (t: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === t)?.value ?? "";
  return `${g("weekday")} ${g("day")} ${g("month")} ${g("hour")}:${g("minute")}:${g("second")}`;
}

export function TopNav({
  mode,
  onMode,
  status,
}: {
  mode: Mode;
  onMode: (mode: Mode) => void;
  /** Compact Decision status (pair · live · 5s, optional model age). */
  status?: ReactNode;
}) {
  const [clock, setClock] = useState(() => formatDhaka(new Date()));
  useEffect(() => {
    const id = window.setInterval(() => setClock(formatDhaka(new Date())), 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <header className="topnav">
      <a
        className="brand"
        href="#decision"
        onClick={(e) => {
          e.preventDefault();
          onMode("decision");
        }}
      >
        <span className="brand-mark">FX</span>
        ForX
      </a>
      <nav className="modes" aria-label="Modes">
        {MODES.map((item) => (
          <button
            key={item.id}
            className={item.id === mode ? "mode active" : "mode"}
            type="button"
            title={item.label}
            aria-label={item.label}
            onClick={() => onMode(item.id)}
          >
            <span className="mode-full">{item.label}</span>
            <span className="mode-short">{item.short || item.label}</span>
          </button>
        ))}
      </nav>
      {status ? (
        <div className="nav-status" aria-label="Active data status">
          {status}
        </div>
      ) : null}
      <div className="nav-right">
        <div className="clock" title="Asia/Dhaka">
          <span className="dot" aria-hidden="true" />
          <span>{clock}</span>
          <span className="tz">Dhaka</span>
        </div>
        <div className="user-chip" title="Ashiqul Islam">
          <span className="avatar">AI</span>
          <span className="user-name">Ashiqul Islam</span>
        </div>
      </div>
    </header>
  );
}
