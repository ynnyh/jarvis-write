import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { api, token, setUnauthorizedHandler, Me } from "./api";
import LoginPage from "./pages/LoginPage";

export default function App() {
  const [tokens, setTokens] = useState<string>("");
  const [me, setMe] = useState<Me | null>(null);
  // 引导态:正在用已存 token 拉当前用户
  const [booting, setBooting] = useState<boolean>(!!token.get());

  // 401 统一处理:清 token、回登录页
  useEffect(() => {
    setUnauthorizedHandler(() => { setMe(null); });
  }, []);

  // 启动时若有 token,校验并取用户;失效则回登录
  useEffect(() => {
    if (!token.get()) { setBooting(false); return; }
    api.me()
      .then((u) => setMe(u))
      .catch(() => { token.clear(); setMe(null); })
      .finally(() => setBooting(false));
  }, []);

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
    return <LoginPage onAuthed={setMe} />;
  }

  return (
    <>
      <div className="topbar">
        <Link to="/" className="logo">jarvis<span>·write</span></Link>
        <span className="muted">AI 长篇小说工作台</span>
        <div style={{ flex: 1 }} />
        {tokens && <span className="muted" title="累计 LLM 用量">{tokens}</span>}
        <a href="/settings" target="_blank" rel="noreferrer">模型设置</a>
        <a href="/docs" target="_blank" rel="noreferrer">API</a>
        <span className="muted" title={me.is_admin ? "管理员" : "用户"}>{me.username}</span>
        <a onClick={logout} style={{ cursor: "pointer" }}>退出</a>
      </div>
      <div className="wrap">
        <Outlet />
      </div>
    </>
  );
}
