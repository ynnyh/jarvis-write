// 全屏阅读器:写作页单章阅读与「阅读全书」共用。
// 内含:偏好设置(背景/字体/字号,localStorage 持久化)、定稿/草稿 tab、上一章/下一章、Esc 关闭;
// 传入 toc 时变为全书模式 —— PC 左侧目录栏,窄屏(≤640px)收成「目录」抽屉。
import { useEffect, useRef, useState } from "react";
import { ChapterDetail } from "../api";

export const STATUS_CN: Record<string, string> = {
  empty: "未生成", drafting: "生成中", drafted: "有草稿",
  finalized: "已定稿", stale: "大纲已变",
};

/** 正文按空行/换行分段渲染成 <p>,保证可读性 */
export function Paragraphs({ text }: { text: string }) {
  const paras = text.split(/\n+/).map((s) => s.trim()).filter(Boolean);
  if (!paras.length) return <div className="muted">(空)</div>;
  return <>{paras.map((p, i) => <p key={i}>{p}</p>)}</>;
}

/** 阅读器个性化设置:背景主题/字体/字号,localStorage 持久化,不登录、跨项目生效 */
type ReaderTheme = "paper" | "kraft" | "night";
type ReaderFont = "song" | "hei" | "kai";
type ReaderSize = "sm" | "md" | "lg";
interface ReaderPrefs { theme: ReaderTheme; font: ReaderFont; size: ReaderSize; }
const READER_PREFS_KEY = "reader-prefs";
const DEFAULT_READER_PREFS: ReaderPrefs = { theme: "kraft", font: "song", size: "md" };

function loadReaderPrefs(): ReaderPrefs {
  try {
    const raw = localStorage.getItem(READER_PREFS_KEY);
    if (!raw) return DEFAULT_READER_PREFS;
    return { ...DEFAULT_READER_PREFS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_READER_PREFS;
  }
}

const THEME_OPTIONS: { v: ReaderTheme; label: string }[] = [
  { v: "paper", label: "纸白" },
  { v: "kraft", label: "牛皮纸" },
  { v: "night", label: "暗夜" },
];
const FONT_OPTIONS: { v: ReaderFont; label: string; cls: string }[] = [
  { v: "song", label: "宋体", cls: "rs-font-song" },
  { v: "hei", label: "黑体", cls: "rs-font-hei" },
  { v: "kai", label: "楷体", cls: "rs-font-kai" },
];
const SIZE_OPTIONS: { v: ReaderSize; label: string }[] = [
  { v: "sm", label: "小" },
  { v: "md", label: "标准" },
  { v: "lg", label: "大" },
];

/** 全书目录条目:disabled 表示该章尚未生成正文(置灰不可点) */
export interface ReaderTocItem { num: number; label: string; disabled?: boolean; }

interface Props {
  loading: boolean;            // 翻章/加载中:禁用翻页按钮;chapter 为空时显示加载态
  chapter: ChapterDetail | null;
  title?: string;              // 章节标题(来自大纲)
  hasPrev: boolean;
  hasNext: boolean;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  toc?: { items: ReaderTocItem[]; current: number | null; onSelect: (n: number) => void };
}

export default function Reader({
  loading, chapter, title, hasPrev, hasNext, onPrev, onNext, onClose, toc,
}: Props) {
  const [tab, setTab] = useState<"final" | "draft">("final");
  const [prefs, setPrefs] = useState<ReaderPrefs>(loadReaderPrefs);
  const [showSettings, setShowSettings] = useState(false);
  const [tocOpen, setTocOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  // 偏好变化即写入 localStorage(隐私模式等写失败时静默忽略)
  useEffect(() => {
    try { localStorage.setItem(READER_PREFS_KEY, JSON.stringify(prefs)); } catch { /* ignore */ }
  }, [prefs]);

  // 设置面板:点击面板外任意处收起
  useEffect(() => {
    if (!showSettings) return;
    const onDown = (e: MouseEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setShowSettings(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [showSettings]);

  // 换章:默认看定稿(无定稿看草稿),收起设置/目录,正文滚动回顶
  useEffect(() => {
    if (!chapter) return;
    setTab(chapter.final_content ? "final" : "draft");
    setShowSettings(false);
    setTocOpen(false);
    contentRef.current?.scrollTo(0, 0);
  }, [chapter]);

  // Esc 关闭阅读器
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="reader-overlay" onClick={onClose}>
      <div
        className={"reader" + (toc ? " reader-book" : "")}
        data-theme={prefs.theme}
        data-font={prefs.font}
        data-size={prefs.size}
        onClick={(e) => e.stopPropagation()}
      >
        {chapter ? (
          <>
            <div className="reader-head">
              {toc && (
                <button className="btn-sm reader-toc-btn" onClick={() => setTocOpen((v) => !v)}>
                  目录
                </button>
              )}
              <h2 className="reader-title">
                <span className="reader-title-text">第{chapter.chapter_number}章 {title ?? ""}</span>
                <span className="reader-meta">
                  <span className={"badge " + (chapter.is_stale ? "err" : chapter.status === "finalized" ? "ok" : "")}>
                    {chapter.is_stale ? "大纲已变" : STATUS_CN[chapter.status] ?? chapter.status}
                  </span>
                  <span className="muted"> {chapter.word_count}字</span>
                </span>
              </h2>
              {chapter.draft_content && chapter.draft_content !== chapter.final_content && (
                <div className="reader-tabs">
                  <span
                    className={"reader-tab" + (tab === "final" ? " on" : "")}
                    onClick={() => setTab("final")}
                  >定稿</span>
                  <span
                    className={"reader-tab" + (tab === "draft" ? " on" : "")}
                    onClick={() => setTab("draft")}
                  >草稿</span>
                </div>
              )}
              <div className="reader-settings" ref={settingsRef}>
                <button className="btn-sm" onClick={() => setShowSettings((v) => !v)}>
                  设置
                </button>
                {showSettings && (
                  <div className="reader-settings-pop">
                    <div className="rs-group">
                      <div className="rs-label">背景</div>
                      <div className="chips">
                        {THEME_OPTIONS.map((o) => (
                          <span
                            key={o.v}
                            className={"chip" + (prefs.theme === o.v ? " on" : "")}
                            onClick={() => setPrefs((p) => ({ ...p, theme: o.v }))}
                          >{o.label}</span>
                        ))}
                      </div>
                    </div>
                    <div className="rs-group">
                      <div className="rs-label">字体</div>
                      <div className="chips">
                        {FONT_OPTIONS.map((o) => (
                          <span
                            key={o.v}
                            className={"chip " + o.cls + (prefs.font === o.v ? " on" : "")}
                            onClick={() => setPrefs((p) => ({ ...p, font: o.v }))}
                          >{o.label}</span>
                        ))}
                      </div>
                    </div>
                    <div className="rs-group">
                      <div className="rs-label">字号</div>
                      <div className="chips">
                        {SIZE_OPTIONS.map((o) => (
                          <span
                            key={o.v}
                            className={"chip" + (prefs.size === o.v ? " on" : "")}
                            onClick={() => setPrefs((p) => ({ ...p, size: o.v }))}
                          >{o.label}</span>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
              <button className="reader-close" onClick={onClose}>关闭</button>
              {/* 窄屏强制换行点:仅 ≤640px 显示,把头部切成两行(见 styles.css) */}
              <div className="reader-head-br" />
            </div>
            <div className="reader-body">
              {toc && (
                <div className={"reader-toc" + (tocOpen ? " open" : "")}>
                  {toc.items.map((it) => (
                    <div
                      key={it.num}
                      className={"reader-toc-item"
                        + (it.num === toc.current ? " on" : "")
                        + (it.disabled ? " off" : "")}
                      onClick={() => {
                        if (it.disabled) return;
                        toc.onSelect(it.num);
                        setTocOpen(false);
                      }}
                    >
                      <b>第{it.num}章</b> {it.label}
                    </div>
                  ))}
                </div>
              )}
              <div className="reader-content" ref={contentRef}>
                <Paragraphs
                  text={tab === "final"
                    ? chapter.final_content || chapter.draft_content
                    : chapter.draft_content}
                />
              </div>
            </div>
            <div className="reader-nav">
              <button disabled={!hasPrev || loading} onClick={onPrev}>
                ← 上一章
              </button>
              <button disabled={!hasNext || loading} onClick={onNext}>
                下一章 →
              </button>
            </div>
          </>
        ) : (
          <div className="reader-content muted"><span className="spin" />加载正文…</div>
        )}
      </div>
    </div>
  );
}
