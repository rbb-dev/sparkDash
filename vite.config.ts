import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  // mgllm: served under a sub-path (e.g. VITE_BASE=/sparkdash/ behind a reverse proxy); "/" by default
  base: process.env.VITE_BASE || "/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    // Allow HMR in Docker
    watch: {
      usePolling: process.env.CHOKIDAR_USEPOLLING === "1",
    },
    proxy: {
      "/api": "http://127.0.0.1:5555",
      "/metrics": "http://127.0.0.1:5555",
      "/ws": {
        target: "ws://127.0.0.1:5555",
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});