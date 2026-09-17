import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the UI is served by Vite and /api is proxied to the FastAPI backend.
// In Docker, nginx serves the built assets and proxies /api instead (see docker/nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
