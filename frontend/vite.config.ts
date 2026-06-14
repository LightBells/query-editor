import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api (HTTP + WebSocket) to the FastAPI backend,
// so the frontend can use relative URLs and avoid CORS during development.
// BACKEND_URL lets the Docker Compose setup point at the `backend` service.
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: backend,
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
