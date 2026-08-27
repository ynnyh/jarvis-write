import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import ProjectsPage from "./pages/ProjectsPage";
import "./styles.css";
import { initTheme } from "./theme";

// 路由按需加载:首屏只需要「我的小说」列表,而 ProjectPage(正文编辑器 + 全书面板 + 漫剧工坊)
// 是整个包里最重的一块,写小说的人也未必当天就进宣传片/短片工坊。
// 拆开后首屏不再为没打开的页面付流量(实测单包 860kB → 主包 425kB,gzip 270kB → 140kB;
// ProjectPage 单独 321kB 按需拉)。加载中的兜底在 App 里(Suspense 包着 Outlet),顶栏与全局层不闪。
const ProjectPage = React.lazy(() => import("./pages/ProjectPage"));
const OnboardingFlow = React.lazy(() => import("./pages/OnboardingFlow"));
const PromoPage = React.lazy(() => import("./pages/PromoPage"));
const ClipsPage = React.lazy(() => import("./pages/ClipsPage"));
const BirthdayPage = React.lazy(() => import("./pages/BirthdayPage"));
const AdminPage = React.lazy(() => import("./pages/AdminPage"));
const SettingsPage = React.lazy(() => import("./pages/SettingsPage"));
// HelpPage 不拆:未登录也能看(App 里直接渲染),拆了要多一层 Suspense 才不闪
import HelpPage from "./pages/HelpPage";

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
            {/* 情绪短片 / 灵感工坊 / 故事工坊:三条线共用 ClipsPage(按路径分 mode) */}
            <Route path="clips/:id?" element={<ClipsPage />} />
            <Route path="inspire/:id?" element={<ClipsPage />} />
            <Route path="free/:id?" element={<ClipsPage />} />
            {/* 生日祝福:寿星定制祝福片,独立线(自有表/引擎/手卡出片盘) */}
            <Route path="birthday/:id?" element={<BirthdayPage />} />
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
