import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

// The shell is served from the robot host, so dev proxies to a locally running
// runtime rather than assuming a separate origin. Same shape in dev and production
// keeps CORS out of the picture entirely.
const RUNTIME = process.env.TENDON_RUNTIME ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  server: {
    port: 5273,
    proxy: {
      "/api": { target: RUNTIME, changeOrigin: true },
      "/ws": { target: RUNTIME, ws: true, changeOrigin: true },
    },
  },
  build: {
    // Served by the runtime from src/tendon/api, not from a CDN.
    outDir: "dist",
    sourcemap: true,
  },
});
