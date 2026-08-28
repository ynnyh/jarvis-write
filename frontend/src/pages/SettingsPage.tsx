// 设置页(桌面/网页统一,SPA 内路由 /#/settings):
//   · 关于 & 更新:显示版本;桌面版可检查更新 → 静默下载 → 提示重启生效
//   · 账号:修改登录密码(仅 server 多用户模式)
//   · 应用锁:启动密码设/改/移除(仅 local 桌面单机模式)
//   · 模型设置:每用户多套命名配置,增删改测 + 一键切换默认/快档(取代旧的独立 settings.html)
//   · 偏好:外观(跟随系统/浅色/深色,全端生效)+ 启动时自动检查更新开关、
//     关闭窗口时最小化到托盘开关(后两者仅桌面)
// 桌面能力经 desktop.ts 优雅降级:非桌面(网页)隐藏更新相关 UI。
// 各卡片已拆分到 pages/settings/,本文件只装配容器。
import { Link } from "react-router-dom";
import { api } from "../api";
import { isDesktop } from "../desktop";
import { AboutUpdateCard } from "./settings/AboutUpdateCard";
import { AccountCard } from "./settings/AccountCard";
import { AppLockCard } from "./settings/AppLockCard";
import { ProvidersCard } from "./settings/ProvidersCard";
import { PreferencesCard } from "./settings/PreferencesCard";
import { RenderCard } from "./settings/RenderCard";

export default function SettingsPage() {
  return (
    <div className="settings-page">
      <div className="settings-head">
        <Link to="/" className="linkbtn">← 返回工作台</Link>
        <h1>设置</h1>
      </div>
      <AboutUpdateCard />
      <AccountCard />
      <AppLockCard />
      <ProvidersCard />
      <RenderCard />
      <PreferencesCard />
      <div className="settings-foot">
        <a
          href="/docs"
          onClick={(e) => {
            // 桌面壳不处理 target=_blank;交后端用系统浏览器打开
            if (isDesktop()) { e.preventDefault(); api.openLink(`${location.origin}/docs`).catch(() => {}); }
          }}
          target={isDesktop() ? undefined : "_blank"}
          rel="noreferrer"
        >
          API 文档 / OpenAPI →
        </a>
      </div>
    </div>
  );
}
