import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During development, every request to /api is forwarded to the FastAPI
// backend, so the React app and the backend behave as if they were on the
// same origin. Change BACKEND_URL if your backend runs somewhere else.
const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 700 }, // the graph library alone is ~580 kB
  server: {
    port: 5173,
    proxy: {
      "/api": { target: BACKEND_URL, changeOrigin: true },
    },
  },
});
