import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/health": api,
      "/watchlist": api,
      "/board": api,
      "/brief": api,
      "/consensus": api,
      "/ohlcv": api,
      "/refresh": api,
      "/pipeline": api,
    },
  },
});
