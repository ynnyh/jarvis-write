// 偏好卡:外观(全端生效)+ 启动自动检查更新 / 关闭进托盘(仅桌面)。拆自 SettingsPage.tsx。
// 外观选择全端生效(写 localStorage 并即改 <html data-theme>);
// 自动更新 / 关闭进托盘开关仅桌面有意义(网页随访问自动最新、没有窗口 X),非桌面不渲染。
import { useState } from "react";
import { getThemePref, setThemePref, ThemePref } from "../../theme";
import { getCloseToTrayPref, setCloseToTrayPref } from "../../ui/CloseGuard";
import { isDesktop } from "../../desktop";

// 偏好:启动时自动检查更新(仅桌面有意义)。默认开。
const AUTO_CHECK_KEY = "jarvis_auto_check_update";
function getAutoCheck(): boolean {
  return localStorage.getItem(AUTO_CHECK_KEY) !== "false";
}

const THEME_OPTIONS: { v: ThemePref; label: string }[] = [
  { v: "auto", label: "跟随系统" },
  { v: "light", label: "浅色" },
  { v: "dark", label: "深色" },
];

export function PreferencesCard() {
  const [theme, setTheme] = useState<ThemePref>(getThemePref);
  const [autoCheck, setAutoCheck] = useState(getAutoCheck());
  const [closeToTray, setCloseToTray] = useState(getCloseToTrayPref());

  function pickTheme(v: ThemePref) {
    setThemePref(v);
    setTheme(v);
  }

  function toggle(v: boolean) {
    setAutoCheck(v);
    localStorage.setItem(AUTO_CHECK_KEY, v ? "true" : "false");
  }

  return (
    <div className="card">
      <div className="card-head"><h2>偏好</h2></div>
      <div className="appearance-row">
        <span className="fl">外观</span>
        <div className="chips">
          {THEME_OPTIONS.map((o) => (
            <button key={o.v} type="button"
              className={"chip" + (theme === o.v ? " on" : "")}
              onClick={() => pickTheme(o.v)}>
              {o.label}
            </button>
          ))}
        </div>
      </div>
      {isDesktop() && (
        <label className="default-pick">
          <input type="checkbox" checked={autoCheck} onChange={(e) => toggle(e.target.checked)} />
          启动时自动检查更新
        </label>
      )}
      {isDesktop() && (
        <label className="default-pick">
          <input
            type="checkbox"
            checked={closeToTray}
            onChange={(e) => { setCloseToTrayPref(e.target.checked); setCloseToTray(e.target.checked); }}
          />
          关闭窗口时最小化到托盘
        </label>
      )}
      {isDesktop() && closeToTray && (
        <p className="card-desc" style={{ marginBottom: 0 }}>
          开启后点 X 不询问、直接进托盘;有后台任务运行时仍会先询问,避免误杀任务。
        </p>
      )}
    </div>
  );
}
