import { Component } from "react";

type BoundaryState = { error: Error | null };

/** Render crashes stay on screen. This file is not the Vite entry, so it cannot self-accept and skip painting. */
export class DeskErrorBoundary extends Component<{ children?: unknown }, BoundaryState> {
  constructor(props: { children?: unknown }) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  render() {
    if (!this.state.error) return <>{this.props.children}</>;
    return (
      <div style={{ fontFamily: "system-ui, sans-serif", padding: 24, color: "#101828", background: "#f4f5f7", minHeight: "100vh" }}>
        <h1 style={{ fontSize: 18, margin: "0 0 8px" }}>Desk hit an error</h1>
        <p style={{ margin: "0 0 16px" }}>{this.state.error.message || "The desk could not render."}</p>
        <button type="button" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
