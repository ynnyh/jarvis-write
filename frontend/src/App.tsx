import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { api, token, setUnauthorizedHandler, Me } from "./api";
import LoginPage from "./pages/LoginPage";
import HelpPage from "./pages/HelpPage";
import { Toaster } from "./ui/Toaster";
import { ConfirmHost } from "./ui/ConfirmDialog";
import { ErrorBoundary } from "./ui/ErrorBoundary";
import { TaskCenterBadge, TaskCenterProvider } from "./ui/TaskCenter";
import UpdateBanner from "./ui/UpdateBanner";

const GH_URL = "https://github.com/ynnyh/jarvis-write";

export default function App() {
  const [tokens, setTokens] = useState<string>("");
  const [me, setMe] = useState<Me | null>(null);
  // 桌面单机(local)模式:免登录,不显示登录页/退出/邀请码等多用户 UI
  const [isLocal, setIsLocal] = useState<boolean>(false);
  // 未配置模型引导:null=未探测,false=未配置(显示横幅)
  const [llmConfigured, setLlmConfigured] = useState<boolean | null>(null);
  // 引导态:正在用已存 token 拉当前用户,或正在探测运行模式
  const [booting, setBooting] = useState<boolean>(true);
  const location = useLocation();

  // 401 统一处理:清 token、回登录页
  useEffect(() => {
    setUnauthorizedHandler(() => { setMe(null); });
  }, []);

  // 启动:先探运行模式。local → 免登录直接拉用户;server → 有 token 才校验。
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let local = false;
      try {
        const m = await api.mode();
        local = m.is_local;
      } catch { /* 探测失败按 server 处理 */ }
      if (cancelled) return;
      setIsLocal(local);
      if (local) {
        // 桌面单机:后端免鉴权,直接取本地用户
        try {
          const u = await api.me();
          if (!cancelled) setMe(u);
        } catch { /* 忽略,极少发生 */ }
        if (!cancelled) setBooting(false);
        return;
      }
      // server 模式:沿用原有 token 引导
      if (!token.get()) { if (!cancelled) setBooting(false); return; }
      try {
        const u = await api.me();
        if (!cancelled) setMe(u);
      } catch {
        token.clear();
        if (!cancelled) setMe(null);
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // 模型配置探测:登录后拉一次,未配置则显示全局引导横幅
  useEffect(() => {
    if (!me) { setLlmConfigured(null); return; }
    api.providerStatus()
      .then((s) => setLlmConfigured(s.configured))
      .catch(() => setLlmConfigured(null));
  }, [me]);

  // 用量轮询:登录后才拉
  useEffect(() => {
    if (!me) { setTokens(""); return; }
    const load = () =>
      api.usage()
        .then((u) => {
          const total = u.total_prompt_tokens + u.total_completion_tokens;
          setTokens(total > 0 ? `${(total / 1000).toFixed(1)}k tokens · ${u.total_calls} 次调用` : "");
        })
        .catch(() => setTokens(""));
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [me]);

  function logout() {
    token.clear();
    setMe(null);
  }

  if (booting) {
    return <div className="auth-wrap"><span className="spin" /></div>;
  }

  if (!me) {
    // 使用指南对未登录用户开放(新用户注册前就能看)
    if (location.pathname === "/help") {
      return (
        <div className="wrap">
          <HelpPage />
        </div>
      );
    }
    // local(桌面单机)模式免登录:此时 me 尚未就绪只是还在拉,显示 loading 而非登录页
    if (isLocal) {
      return <div className="auth-wrap"><span className="spin" /></div>;
    }
    return <LoginPage onAuthed={setMe} />;
  }

  return (
    <TaskCenterProvider enabled={!!me}>
      <div className="topbar">
        <Link to="/" className="logo">jarvis<span>·write</span></Link>
        <span className="muted">AI 长篇小说工作台</span>
        <div className="grow" />
        <TaskCenterBadge />
        {tokens && <span className="muted" title="累计 LLM 用量">{tokens}</span>}
        <Link to="/">首页</Link>
        {!isLocal && me.is_admin && <Link to="/admin">管理</Link>}
        <Link to="/help">指南</Link>
        {isLocal ? (
          <>
            {/* 桌面单机:WebView2 不处理 target=_blank。模型设置窗口内打开(自带返回链接);
                API/GitHub 经后端 open-link 交系统浏览器。 */}
            <a href="/settings">模型设置</a>
            <a href={`${window.location.origin}/docs`}
              onClick={(e) => { e.preventDefault(); api.openLink(`${window.location.origin}/docs`).catch(() => {}); }}>API</a>
            <a className="topbar-gh" href={GH_URL}
              onClick={(e) => { e.preventDefault(); api.openLink(GH_URL).catch(() => {}); }}>GitHub</a>
          </>
        ) : (
          <>
            <a href="/settings" target="_blank" rel="noreferrer">模型设置</a>
            <a href="/docs" target="_blank" rel="noreferrer">API</a>
            <a className="topbar-gh" href={GH_URL} target="_blank" rel="noreferrer">GitHub</a>
          </>
        )}
        {/* local 单机免登录:不显示账号名与退出 */}
        {!isLocal && <span className="muted" title={me.is_admin ? "管理员" : "用户"}>{me.username}</span>}
        {!isLocal && <a className="linkbtn" onClick={logout}>退出</a>}
      </div>
      <UpdateBanner />
      {llmConfigured === false && (
        <div className="llm-banner">
          还没有配置模型——大部分功能需要模型才能工作。
          {isLocal
            ? <a href="/settings">去「模型设置」配置你的 key →</a>
            : <a href="/settings" target="_blank" rel="noreferrer">去「模型设置」配置你的 key →</a>}
        </div>
      )}
      <div className="wrap">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </div>
      <Toaster />
      <ConfirmHost />
    </TaskCenterProvider>
  );
}
