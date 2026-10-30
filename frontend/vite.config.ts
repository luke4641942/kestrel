import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: `npm run dev` serves the UI on :5173 and proxies /api to the Python server on :8000.
// Build: output goes into the Python package so `kestrel serve` hosts the dashboard itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    outDir: "../src/kestrel/api/static",
    emptyOutDir: true,
  },
});
