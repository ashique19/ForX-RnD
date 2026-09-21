export function AuxHelp() {
  return (
    <details className="aux">
      <summary>
        <svg className="chev" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M6 4l4 4-4 4" />
        </svg>
        Nav help / Workspace / Lab TF hints
      </summary>
      <div className="aux-body">
        Decision focuses on active bias + levels. Switch to <strong>Lab</strong> for experiment TFs
        (<code>M15</code>, <code>H1</code>, <code>H4</code>) and <strong>Run pipeline</strong>.
        Streamlit stays the Lab UI on port 8501 until cutover.
        Workspace shortcuts live on the Streamlit desk. Calendar and Paper modes are placeholders.
        STALE / MISSING never render as a live BUY or SELL.
      </div>
    </details>
  );
}
