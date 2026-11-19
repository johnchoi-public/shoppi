import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build goes straight into the Python package, so FastAPI serves it.
// For UI development run `npm run dev` (port 5173): /api is proxied to the Python server.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../src/shoppi/web/dist", emptyOutDir: true, chunkSizeWarningLimit: 1200 },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});
