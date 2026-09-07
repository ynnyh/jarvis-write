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
const SeriesPage = React.lazy(() => import("./pages/SeriesPage"));
const ScriptsPage = React.lazy(() => import("./pages/ScriptsPage"));
const AdminPage = React.lazy(() => import("./pages/AdminPage"));
const SettingsPage = React.lazy(() => import("./pages/SettingsPage"));
// HelpPage 不拆:未登录也能看(App 里直接渲染),拆了要多一层 Suspense 才不闪
import HelpPage from "./pages/HelpPage";
import SharePage from "./pages/SharePage";

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
          {/* 公开分享阅读页:免登录只读,必须在 App 布局(登录墙)之外 */}
          <Route path="/share/:token" element={<SharePage />} />
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
            {/* 系列短片:固定主角的 5-15 秒系列,主角档案持久化(资产制) */}
            <Route path="series/:id?" element={<SeriesPage />} />
            {/* 剧本工坊:独立写剧 + 小说改编共用集管线 */}
            <Route path="scripts/:id?" element={<ScriptsPage />} />
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

// PWA:生产构建注册最小 Service Worker(仅满足「安装到主屏幕」条件,不做缓存——
// 壳资源带 hash 且有版本提醒机制,贸然缓存会造成发版后旧壳错配;见 public/sw.js)。
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/app/sw.js").catch(() => {
      /* 注册失败不影响使用:SW 只服务于安装能力 */
    });
  });
}
