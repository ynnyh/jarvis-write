/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 构建产物挂在 FastAPI 的 /app 路径下;开发时代理后端路径(/settings、/docs 是后端自带页面,
// /openapi.json 是 swagger 文档的数据源)。生产环境前后端同源,不走此代理,不受影响。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/app/",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/settings": "http://127.0.0.1:8000",
      "/docs": "http://127.0.0.1:8000",
      "/openapi.json": "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
