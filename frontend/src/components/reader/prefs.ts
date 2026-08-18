// 阅读器个性化偏好:背景主题/字体/字号,localStorage 持久化,不登录、跨项目生效。拆自 Reader.tsx。
import { isDark } from "../../theme";

/** 阅读器个性化设置:背景主题/字体/字号 */
export type ReaderTheme = "paper" | "kraft" | "night";
export type ReaderFont = "song" | "hei" | "kai";
export type ReaderSize = "sm" | "md" | "lg";
export interface ReaderPrefs { theme: ReaderTheme; font: ReaderFont; size: ReaderSize; }
export const READER_PREFS_KEY = "reader-prefs";

/** 默认阅读器偏好:无已存偏好时,背景跟随全局外观(暗色 → 暗夜,否则牛皮纸) */
export function defaultReaderPrefs(): ReaderPrefs {
  return { theme: isDark() ? "night" : "kraft", font: "song", size: "md" };
}

export function loadReaderPrefs(): ReaderPrefs {
  try {
    const raw = localStorage.getItem(READER_PREFS_KEY);
    if (!raw) return defaultReaderPrefs();
    return { ...defaultReaderPrefs(), ...JSON.parse(raw) };
  } catch {
    return defaultReaderPrefs();
  }
}

export const THEME_OPTIONS: { v: ReaderTheme; label: string }[] = [
  { v: "paper", label: "纸白" },
  { v: "kraft", label: "牛皮纸" },
  { v: "night", label: "暗夜" },
];
export const FONT_OPTIONS: { v: ReaderFont; label: string; cls: string }[] = [
  { v: "song", label: "宋体", cls: "rs-font-song" },
  { v: "hei", label: "黑体", cls: "rs-font-hei" },
  { v: "kai", label: "楷体", cls: "rs-font-kai" },
];
export const SIZE_OPTIONS: { v: ReaderSize; label: string }[] = [
  { v: "sm", label: "小" },
  { v: "md", label: "标准" },
  { v: "lg", label: "大" },
];
// 常用润色方向(点一下填入输入框,可再改)
export const DIRECTION_CHIPS = ["更生动", "更紧张", "更简洁", "去 AI 味"];
