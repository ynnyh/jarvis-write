import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import ProjectsPage from "./pages/ProjectsPage";
import ProjectPage from "./pages/ProjectPage";
import OnboardingFlow from "./pages/OnboardingFlow";
import AdminPage from "./pages/AdminPage";
import HelpPage from "./pages/HelpPage";
import "./styles.css";
import "./tailwind.css";

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
            <Route path="admin" element={<AdminPage />} />
            <Route path="help" element={<HelpPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </HashRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
