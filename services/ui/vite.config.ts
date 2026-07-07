import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        // Match docker-compose host port for API (see docker-compose.yml api.ports)
        target: process.env.VITE_DEV_API_PROXY ?? "http://127.0.0.1:8001",
        changeOrigin: true,
      },
    },
  },
});
