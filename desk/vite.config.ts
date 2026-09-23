import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

const api = "http://127.0.0.1:8000";

/**
 * Proxy FastAPI routes only.
 * A path with a file extension is a desk file (the old build put JS under /assets/).
 * Forwarding that to :8000 returns JSON or an empty 500, the module never runs, and the page stays white.
 */
function apiProxy(): ProxyOptions {
  return {
    target: api,
    bypass(req) {
      const path = (req.url ?? "").split("?")[0];
      if (path.lastIndexOf(".") > path.lastIndexOf("/")) return path;
    },
  };
}

export default defineConfig({
  plugins: [react()],
  build: {
    assetsDir: "static",
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/health": apiProxy(),
      "/watchlist": apiProxy(),
      "/board": apiProxy(),
      "/brief": apiProxy(),
      "/consensus": apiProxy(),
      "/ohlcv": apiProxy(),
      "/refresh": apiProxy(),
      "/pipeline": apiProxy(),
      "/paper": apiProxy(),
      "/assets": apiProxy(),
      "/learnings": apiProxy(),
      "/calendar": apiProxy(),
      "/history": apiProxy(),
      "/replay": apiProxy(),
    },
  },
});
