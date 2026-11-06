/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev the UI runs on :5173 and proxies API calls to the FastAPI server, so no CORS config is needed.
export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", globals: true, css: false },
  server: { proxy: { "/api": "http://127.0.0.1:8000", "/health": "http://127.0.0.1:8000" } },
});
