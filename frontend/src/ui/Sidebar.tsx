// 全局左侧导航栏(工作台外壳的「左边菜单」):一级入口常驻,右侧留给内容区。
// 为什么从顶栏改成侧栏:顶栏横排只能塞 4-5 个纯文字链,三个工坊(小说/情绪短片/宣传片)
// 的入口只能埋在首页 page-head 的按钮堆里,不像一台「工作台」;侧栏是纵向空间,
// 入口图标+文字一行一个,active 态一眼可见,还能在底部常驻任务中心与用量。
// 桌面端常驻;移动端(≤767px)由 App 收成 ☰ 抽屉(见 .m-shellbar),组件同一份。
import { Link, NavLink } from "react-router-dom";
import { api, Me } from "../api";
import { TaskCenterBadge } from "./TaskCenter";

const GH_URL = "https://github.com/ynnyh/jarvis-write";

// 一级入口:顺序=使用频率。首页(/)的 NavLink 在 v6 只做精确匹配,不会殃及其他页。
const ENTRIES = [
  { to: "/", ico: "📚", label: "我的小说" },
  { to: "/clips", ico: "⚡", label: "情绪短片" },
  { to: "/inspire", ico: "💡", label: "灵感工坊" },
  { to: "/free", ico: "✨", label: "故事工坊" },
  { to: "/promo", ico: "🎬", label: "宣传片工坊" },
  { to: "/birthday", ico: "🎂", label: "生日祝福" },
  { to: "/help", ico: "📖", label: "使用指南" },
  { to: "/settings", ico: "⚙︎", label: "设置" },
];

export default function Sidebar({ me, isLocal, hasLock, tokens, onLock, onLogout }: {
  me: Me;
  isLocal: boolean;
  hasLock: boolean;
  tokens: string;
  onLock: () => void;
  onLogout: () => void;
}) {
  return (
    <>
      <Link to="/" className="side-brand">
        jarvis<span>·write</span>
        <small>AI 长篇小说工作台</small>
      </Link>

      <nav className="side-nav">
        {ENTRIES.map((e) => (
          <NavLink key={e.to} to={e.to}
            className={({ isActive }) => "side-link" + (isActive ? " on" : "")}>
            <span className="side-ico">{e.ico}</span>
            <span className="side-label">{e.label}</span>
          </NavLink>
        ))}
        {!isLocal && me.is_admin && (
          <NavLink to="/admin"
            className={({ isActive }) => "side-link" + (isActive ? " on" : "")}>
            <span className="side-ico">🛡️</span>
            <span className="side-label">管理</span>
          </NavLink>
        )}
      </nav>

      {/* 任务中心常驻:后台在跑的任务(连写/出片)随时可回来看进度 */}
      <div className="side-tasks"><TaskCenterBadge /></div>

      <div className="side-foot">
        {tokens && <div className="side-usage" title="累计 LLM 用量">{tokens}</div>}
        {isLocal && hasLock && (
          <button className="side-mini" title="立即锁定,需输入密码才能重新进入" onClick={onLock}>🔒 锁定</button>
        )}
        {/* 桌面单机:GitHub 经后端 open-link 交系统浏览器(WebView2 不开新标签页) */}
        <a className="side-mini" href={GH_URL}
          onClick={(e) => {
            if (!isLocal) return;
            e.preventDefault();
            api.openLink(GH_URL).catch(() => {});
          }}>GitHub ↗</a>
        {!isLocal && (
          <>
            <span className="side-user" title={me.is_admin ? "管理员" : "用户"}>{me.username}</span>
            <button className="side-mini" onClick={onLogout}>退出</button>
          </>
        )}
      </div>
    </>
  );
}
