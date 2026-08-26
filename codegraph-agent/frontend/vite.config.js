import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api/* is forwarded to the FastAPI backend so the frontend never
// hardcodes an origin (and never fights CORS in dev).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});