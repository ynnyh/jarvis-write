import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import ProjectsPage from "./pages/ProjectsPage";
import ProjectPage from "./pages/ProjectPage";
import PromoPage from "./pages/PromoPage";
import OnboardingFlow from "./pages/OnboardingFlow";
import AdminPage from "./pages/AdminPage";
import HelpPage from "./pages/HelpPage";
import SettingsPage from "./pages/SettingsPage";
import "./styles.css";
import { initTheme } from "./theme";

// 外观:应用 light/dark/auto 偏好,auto 下挂系统主题监听(首屏脚本见 index.html)
initTheme();

// 数据层:窗口聚焦不自动重拉(LLM 数据不易变),错误只重试一次
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 30_000 },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <Routes>
          <Route element={<App />}>
            <Route index element={<ProjectsPage />} />
            {/* 创作起步流:/new 建草稿 → /new/:id/:step 五步走 */}
            <Route path="new/:id?/:step?" element={<OnboardingFlow />} />
            {/* 工作台步骤进 URL:/project/3/write;旧链接 /project/3 重定向由组件内处理 */}
            <Route path="project/:id/:step?" element={<ProjectPage />} />
            {/* 宣传片工坊(独立于小说项目):/promo 列表,/promo/5 工作台 */}
            <Route path="promo/:id?" element={<PromoPage />} />
            <Route path="admin" element={<AdminPage />} />
            <Route path="help" element={<HelpPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </HashRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
