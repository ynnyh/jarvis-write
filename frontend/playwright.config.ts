// e2e 冒烟配置:临时后端(8765,独立 SQLite)+ vite dev(5173,代理指过去)。
// 项目分三段:setup 登录取 storageState → desktop(1280×800)/ mobile(390×844) 复用。
// 跑法:cd frontend && npm run e2e(需要本地 Python 环境能起后端)。
import { defineConfig, devices } from "@playwright/test";

const BACKEND_PORT = 8765;
// 不用 5173:那是日常 dev server 的口,e2e 与它并行互不干扰
const FRONT_PORT = 5180;

export default defineConfig({
  testDir: "./e2e",
  // 只认冒烟用例;全局不重试——冒烟要的是快和确定性,红了就是真问题
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 60_000,
  globalTimeout: 10 * 60_000,

  use: {
    baseURL: `http://127.0.0.1:${FRONT_PORT}`,
  },

  projects: [
    {
      name: "setup",
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: "desktop",
      testIgnore: /auth\.setup\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        storageState: "e2e/.auth/state.json",
      },
      dependencies: ["setup"],
    },
    {
      name: "mobile",
      testIgnore: /auth\.setup\.ts/,
      use: {
        // 与引擎卡/概念页两次翻车同款的窄屏视口——冒烟必须覆盖
        ...devices["Pixel 7"],
        storageState: "e2e/.auth/state.json",
      },
      dependencies: ["setup"],
    },
  ],

  webServer: [
    {
      command: `python scripts/e2e_seed.py && python -m uvicorn app.main:app --port ${BACKEND_PORT}`,
      cwd: "../backend",
      url: `http://127.0.0.1:${BACKEND_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        DATABASE_URL: "sqlite:///./e2e_jarvis_write.db",
        PYTHONIOENCODING: "utf-8",
      },
    },
    {
      // --host 127.0.0.1:vite 默认绑 localhost(本机解析成 ::1),探活/访问统一走 IPv4
      command: `npm run dev -- --port ${FRONT_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${FRONT_PORT}/app/`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        VITE_API_TARGET: `http://127.0.0.1:${BACKEND_PORT}`,
      },
    },
  ],
});
