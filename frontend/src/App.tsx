import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { api, token, setUnauthorizedHandler, Me } from "./api";
import { isDesktop } from "./desktop";
import LoginPage from "./pages/LoginPage";
import LockScreen from "./pages/LockScreen";
import HelpPage from "./pages/HelpPage";
import { Toaster } from "./ui/Toaster";
import { ConfirmHost } from "./ui/ConfirmDialog";
import CloseGuard from "./ui/CloseGuard";
import { ErrorBoundary } from "./ui/ErrorBoundary";
import { TaskCenterBadge, TaskCenterProvider } from "./ui/TaskCenter";
import UpdateBanner from "./ui/UpdateBanner";

const GH_URL = "https://github.com/ynnyh/jarvis-write";

// 应用锁(仅桌面单机模式):解锁标记存 sessionStorage——刷新同标签页不重输,
// 关掉重开要重输。锁屏时不渲染主界面(见下方 locked 分支)。
const UNLOCK_KEY = "jarvis_app_unlocked";

function sleepMs(ms: number) { return new Promise((r) => setTimeout(r, ms)); }

// mode 探测带有限重试(3 次,间隔 1.5s):桌面壳的后端由子进程拉起,
// 启动早期可能还没就绪;一次失败就按 server 处理,会把桌面用户误带到登录页。
async function probeMode(): Promise<{ is_local: boolean; has_lock: boolean } | null> {
  for (let i = 0; i < 3; i++) {
    try {
      const m = await api.mode();
      return m;
    } catch {
      if (i < 2) await sleepMs(1500);
    }
  }
  return null;
}

export default function App() {
  const [tokens, setTokens] = useState<string>("");
  const [me, setMe] = useState<Me | null>(null);
  // 桌面单机(local)模式:免登录,不显示登录页/退出/邀请码等多用户 UI
  const [isLocal, setIsLocal] = useState<boolean>(false);
  // 应用锁:local 模式且已设锁且本次会话未解锁 → 全屏锁屏页
  const [locked, setLocked] = useState<boolean>(false);
  // 是否已设应用锁:决定顶栏「锁定」入口是否出现(local 模式才有意义)
  const [hasLock, setHasLock] = useState<boolean>(false);
  // 未配置模型引导:null=未探测,false=未配置(显示横幅)
  const [llmConfigured, setLlmConfigured] = useState<boolean | null>(null);
  // 引导态:正在用已存 token 拉当前用户,或正在探测运行模式
  const [booting, setBooting] = useState<boolean>(true);
  // 桌面壳专用:mode 重试后仍失败 → 后端没起来/崩溃,渲染失败页而非登录页
  const [backendDead, setBackendDead] = useState(false);
  // 「重试」按钮靠自增它重新跑启动引导
  const [bootNonce, setBootNonce] = useState(0);
  const location = useLocation();

  // 401 统一处理:清 token、回登录页
  useEffect(() => {
    setUnauthorizedHandler(() => { setMe(null); });
  }, []);

  // 启动:先探运行模式。local → 免登录直接拉用户;server → 有 token 才校验。
  useEffect(() => {
    let cancelled = false;
    setBackendDead(false);
    (async () => {
      let local = false;
      let hasAppLock = false;
      const m = await probeMode();
      if (cancelled) return;
      if (m) {
        local = m.is_local;
        hasAppLock = m.has_lock;
      } else if (isDesktop()) {
        // 桌面壳但重试后仍连不上后端:渲染专用失败页(可重试),
        // 绝不能落到登录页误导——桌面单机根本没有账号体系。
        setBackendDead(true);
        setBooting(false);
        return;
      }
      // server 浏览器场景:探测失败维持原行为(按 server 处理,落登录页)
      if (cancelled) return;
      setIsLocal(local);
      setHasLock(local && hasAppLock);
      if (local) {
        // 桌面单机:后端免鉴权,直接取本地用户
        // 但设了应用锁且本次会话未解锁:先出锁屏,解锁后才拉用户进主界面
        if (hasAppLock && sessionStorage.getItem(UNLOCK_KEY) !== "1") {
          setLocked(true);
          setBooting(false);
          return;
        }
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
  }, [bootNonce]);

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

  // 设置页里设/改/移除锁后,顶栏「锁定」入口需跟上:路由切换时顺手重探一次
  useEffect(() => {
    if (!me) return;
    api.mode()
      .then((m) => setHasLock(m.is_local && m.has_lock))
      .catch(() => {});
  }, [me, location.pathname]);

  // 「锁定」入口:清掉本会话解锁标记,立即回到锁屏页(主界面随之整体卸载,不留内容)
  function lockNow() {
    sessionStorage.removeItem(UNLOCK_KEY);
    setLocked(true);
  }

  if (booting) {
    return <div className="auth-wrap"><span className="spin" /></div>;
  }

  if (backendDead) {
    // 桌面壳后端未就绪/崩溃:给明确失败页 + 重试,而不是误导性的登录页
    return (
      <div className="auth-wrap">
        <div className="card auth-card">
          <h1 className="auth-brand">jarvis<span>·write</span></h1>
          <div className="auth-sub">本地后端服务没有响应</div>
          <div className="notice notice-err">
            后端可能仍在启动,或已异常退出。稍后点「重试」;
            若反复失败,请重启应用,仍不行可查看应用日志目录中的 updater.log 排查。
          </div>
          <button
            className="primary btn-lg btn-block"
            style={{ marginTop: 18 }}
            onClick={() => { setBooting(true); setBootNonce((n) => n + 1); }}
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  if (locked) {
    // 应用锁屏:解锁成功标记本会话已解锁,再拉本地用户进主界面
    return (
      <>
        <LockScreen
          onUnlocked={() => {
            sessionStorage.setItem(UNLOCK_KEY, "1");
            api.me()
              .then((u) => { setMe(u); setLocked(false); })
              .catch(() => { /* 忽略,极少发生 */ });
          }}
        />
        {/* 锁屏期间点 X 也走关闭守卫(锁着也可能有后台任务在跑) */}
        <CloseGuard />
      </>
    );
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
        {/* 设置改走 SPA 路由(同窗切换):既含模型设置,也含桌面版「关于&更新」。
            桌面 WebView2 不处理 target=_blank,SPA 内路由天然规避新开窗问题。 */}
        <Link to="/settings">设置</Link>
        {/* 「立即锁定」:仅桌面单机且已设锁时出现,不必重启 app 才能锁 */}
        {isLocal && hasLock && (
          <button className="linkbtn" title="立即锁定,需输入密码才能重新进入" onClick={lockNow}>锁定</button>
        )}
        {isLocal ? (
          // 桌面单机:GitHub 经后端 open-link 交系统浏览器(WebView2 不开新标签页)。
          <a className="topbar-gh" href={GH_URL}
            onClick={(e) => { e.preventDefault(); api.openLink(GH_URL).catch(() => {}); }}>GitHub</a>
        ) : (
          <a className="topbar-gh" href={GH_URL} target="_blank" rel="noreferrer">GitHub</a>
        )}
        {/* local 单机免登录:不显示账号名与退出 */}
        {!isLocal && <span className="muted" title={me.is_admin ? "管理员" : "用户"}>{me.username}</span>}
        {!isLocal && <button className="linkbtn" onClick={logout}>退出</button>}
      </div>
      <UpdateBanner />
      {llmConfigured === false && (
        <div className="llm-banner">
          还没有配置模型——大部分功能需要模型才能工作。
          <Link to="/settings" className="llm-banner-link">去「设置」配置你的 key →</Link>
        </div>
      )}
      <div className="wrap">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </div>
      <Toaster />
      <ConfirmHost />
      {/* 桌面关闭守卫:拦截 X,按后台任务/托盘偏好决定去向(非桌面自动不生效) */}
      <CloseGuard />
    </TaskCenterProvider>
  );
}
