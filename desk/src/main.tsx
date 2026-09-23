import { Component, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

type BoundaryState = { error: Error | null };

/** Keep a render crash on screen. A thrown child used to leave a blank white page. */
class DeskErrorBoundary extends Component<{ children?: unknown }, BoundaryState> {
  state: BoundaryState = { error: null };

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

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <DeskErrorBoundary>
        <App />
      </DeskErrorBoundary>
    </StrictMode>,
  );
}
