import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { DeskErrorBoundary } from "./ErrorBoundary";
import "./styles.css";

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
