import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物挂在 FastAPI 的 /app 路径下;开发时代理 /api 到后端
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
