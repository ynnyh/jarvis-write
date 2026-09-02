/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物挂在 FastAPI 的 /app 路径下;开发时代理后端路径(/settings、/docs 是后端自带页面,
// /openapi.json 是 swagger 文档的数据源)。生产环境前后端同源,不走此代理,不受影响。
export default defineConfig({
  plugins: [react()],
  base: "/app/",
  server: {
    port: 5173,
    proxy: {
      // e2e 冒烟用 VITE_API_TARGET 把 /api 指到临时后端(如 8765),默认本地 8000
      "/api": process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
      "/settings": process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
      "/docs": process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
      "/openapi.json": process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
    },
  },
  build: {
    outDir: "dist",
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // e2e(playwright)目录不进 vitest:那是真浏览器冒烟,不是单测
    exclude: ["**/node_modules/**", "e2e/**"],
  },
});
