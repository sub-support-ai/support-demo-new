import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,   // падаем с ошибкой, если порт занят — не молчим
    host: "0.0.0.0",
    allowedHosts: ["supportpoint.duckdns.org"],
    proxy: {
      // Все запросы /api/ и /docs перенаправляются на бэкенд.
      // Работает и локально, и через туннель — браузеру не нужен порт :8000.
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/docs": { target: "http://localhost:8000", changeOrigin: true },
      "/openapi.json": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
