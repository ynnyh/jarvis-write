// src/theme.ts — 全局外观:light / dark / auto(跟随系统)。
//
// 令牌在 styles/01-tokens.css 按 [data-theme="dark"] 整组覆写,这里只负责:
// 读偏好(localStorage)→ 解析 auto → 把结果写到 <html data-theme>。
// 首屏防闪烁:index.html 有一段内联脚本在 React 挂载前做同一件事,
// 两边的解析逻辑保持一致(改一边记得改另一边)。

export type ThemePref = "light" | "dark" | "auto";

const THEME_KEY = "jarvis_theme";

/** 读取用户偏好;未设置 / 非法值一律回落 auto(跟随系统)。 */
export function getThemePref(): ThemePref {
  try {
    const v = localStorage.getItem(THEME_KEY);
    if (v === "light" || v === "dark" || v === "auto") return v;
  } catch {
    /* 隐私模式等读失败:静默回落 */
  }
  return "auto";
}

/** 系统当前是否深色。 */
function systemDark(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

/** 把偏好解析成实际主题(auto → 跟随系统)。 */
export function resolveTheme(pref: ThemePref): "light" | "dark" {
  return pref === "auto" ? (systemDark() ? "dark" : "light") : pref;
}

/** 应用主题到 <html data-theme>,styles.css 的暗色令牌随之生效。 */
function applyResolved(resolved: "light" | "dark"): void {
  document.documentElement.dataset.theme = resolved;
}

/** 设置偏好并立即生效;auto 下同时挂系统主题变化监听。 */
export function setThemePref(pref: ThemePref): void {
  try {
    localStorage.setItem(THEME_KEY, pref);
  } catch {
    /* 写失败也照常应用本次选择 */
  }
  applyResolved(resolveTheme(pref));
}

/** 当前实际是否深色(阅读器等需要跟随全局暗色时用)。 */
export function isDark(): boolean {
  return resolveTheme(getThemePref()) === "dark";
}

/** 初始化:应用当前偏好;auto 时监听系统主题切换实时跟随。 */
export function initTheme(): void {
  const pref = getThemePref();
  applyResolved(resolveTheme(pref));
  if (pref !== "auto") return;
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    // 只在用户仍处 auto 时跟随(用户可能初始化后改成了固定主题)
    if (getThemePref() === "auto") applyResolved(mq.matches ? "dark" : "light");
  };
  if (typeof mq.addEventListener === "function") mq.addEventListener("change", onChange);
  else if (typeof mq.addListener === "function") mq.addListener(onChange); // 旧 WebView 兜底
}
