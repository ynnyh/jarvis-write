import { Link, Outlet } from "react-router-dom";

export default function App() {
  return (
    <>
      <div className="topbar">
        <Link to="/" className="logo">jarvis<span>·write</span></Link>
        <span className="muted">AI 长篇小说工作台</span>
        <div style={{ flex: 1 }} />
        <a href="/settings" target="_blank" rel="noreferrer">模型设置</a>
        <a href="/docs" target="_blank" rel="noreferrer">API</a>
      </div>
      <div className="wrap">
        <Outlet />
      </div>
    </>
  );
}
