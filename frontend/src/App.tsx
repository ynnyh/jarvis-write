import { useEffect, useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { api } from "./api";

export default function App() {
  const [tokens, setTokens] = useState<string>("");

  useEffect(() => {
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
  }, []);

  return (
    <>
      <div className="topbar">
        <Link to="/" className="logo">jarvis<span>·write</span></Link>
        <span className="muted">AI 长篇小说工作台</span>
        <div style={{ flex: 1 }} />
        {tokens && <span className="muted" title="累计 LLM 用量">{tokens}</span>}
        <a href="/settings" target="_blank" rel="noreferrer">模型设置</a>
        <a href="/docs" target="_blank" rel="noreferrer">API</a>
      </div>
      <div className="wrap">
        <Outlet />
      </div>
    </>
  );
}
