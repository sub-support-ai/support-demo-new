import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,   // падаем с ошибкой, если порт занят — не молчим
    host: "0.0.0.0",
    allowedHosts: ["supportpoint.duckdns.org"],
  },
});
