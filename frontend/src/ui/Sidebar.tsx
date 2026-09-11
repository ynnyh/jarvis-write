// 全局左侧导航栏(工作台外壳的「左边菜单」):一级入口常驻,右侧留给内容区。
// 为什么从顶栏改成侧栏:顶栏横排只能塞 4-5 个纯文字链,三个工坊(小说/情绪短片/宣传片)
// 的入口只能埋在首页 page-head 的按钮堆里,不像一台「工作台」;侧栏是纵向空间,
// 入口图标+文字一行一个,active 态一眼可见,还能在底部常驻任务中心与用量。
// 桌面端常驻;移动端(≤767px)由 App 收成 ☰ 抽屉(见 .m-shellbar),组件同一份。
//
// 2026-09-11 导航归组:此前 8 个一级入口平铺,小说主线被稀释得没分量(用户拍板)。
// 改为「主线独占 + 创作辅助/制片工坊两组折叠」——只动导航壳,不动路由/数据/管线;
// 制片各线仍各自独立工坊(管线差异大,合并等改编主线启动时按「项目中心+产出视图」再做)。
import { useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { api, Me } from "../api";
import { TaskCenterBadge } from "./TaskCenter";

const GH_URL = "https://github.com/ynnyh/jarvis-write";

interface Entry { to: string; ico: string; label: string }

// 主线:独占首位,不进任何组——「AI 长篇小说工作台」的主行动。
const MAIN: Entry = { to: "/", ico: "📚", label: "我的小说" };

// 副线分组。顺序=组内使用频率;制片线默认收起(点组头展开),创作辅助默认展开。
// 折叠互斥于业务:这里只是入口收纳,各工坊页面与路由原样保留。
const GROUPS: { title: string; defaultOpen: boolean; entries: Entry[] }[] = [
  {
    title: "创作辅助", defaultOpen: true,
    entries: [
      { to: "/inspire", ico: "💡", label: "灵感工坊" },
      { to: "/free", ico: "✨", label: "故事工坊" },
    ],
  },
  {
    title: "制片工坊", defaultOpen: false,
    entries: [
      { to: "/scripts", ico: "🎭", label: "剧本工坊" },
      { to: "/promo", ico: "🎬", label: "宣传片工坊" },
      { to: "/clips", ico: "⚡", label: "情绪短片" },
      { to: "/series", ico: "🐾", label: "系列短片" },
      { to: "/birthday", ico: "🎂", label: "生日祝福" },
    ],
  },
];

// 沉底的功能页,不算工坊,不参与分组。
const FOOT_ENTRIES: Entry[] = [
  { to: "/help", ico: "📖", label: "使用指南" },
  { to: "/settings", ico: "⚙︎", label: "设置" },
];

function SideLink({ e }: { e: Entry }) {
  return (
    <NavLink to={e.to}
      className={({ isActive }) => "side-link" + (isActive ? " on" : "")}>
      <span className="side-ico">{e.ico}</span>
      <span className="side-label">{e.label}</span>
    </NavLink>
  );
}

// 折叠组:头部是组名+条目数+箭头;当前路由就在组内时自动展开并点亮头部
// (避免「收起后不知道自己在哪」);用户手动点过头之后,以手动状态为准。
function SideGroup({ title, defaultOpen, entries }: {
  title: string; defaultOpen: boolean; entries: Entry[];
}) {
  const loc = useLocation();
  // 组内条目首段互不重叠(scripts/promo/clips/...),startsWith 足够且能盖住子路由
  const active = entries.some((e) => loc.pathname.startsWith(e.to));
  // null = 尚未手动干预,跟随 defaultOpen/active
  const [manual, setManual] = useState<boolean | null>(null);
  const open = manual ?? (defaultOpen || active);
  return (
    <div className={"side-group" + (active ? " active" : "")}>
      <button type="button" className="side-group-head" aria-expanded={open}
        title={open ? "收起" : "展开"}
        onClick={() => setManual(!open)}>
        <span className="side-group-title">{title}</span>
        <span className="side-group-n">{entries.length}</span>
        <span className="side-group-arrow" aria-hidden="true">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="side-group-body">
          {entries.map((e) => <SideLink key={e.to} e={e} />)}
        </div>
      )}
    </div>
  );
}

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
        <SideLink e={MAIN} />
        {GROUPS.map((g) => <SideGroup key={g.title} {...g} />)}
        <div className="side-sep" role="presentation" />
        {FOOT_ENTRIES.map((e) => <SideLink key={e.to} e={e} />)}
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
