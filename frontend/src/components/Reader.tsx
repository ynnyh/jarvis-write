// 全屏阅读器:写作页单章阅读与「阅读全书」共用。
// 内含:偏好设置(背景/字体/字号,localStorage 持久化)、定稿/草稿 tab、上一章/下一章、Esc 关闭;
// 传入 toc 时变为全书模式 —— PC 左侧目录栏,窄屏(≤640px)收成「目录」抽屉;
// 传入 polishCtx 时开启段落点选润色(选段 → 输方向 → 对照 → 替换并同步一致性引擎);
// 传入 restoreScroll / onScrollPos 时支持全书阅读位置记忆(恢复与上报)。
import { useEffect, useRef, useState } from "react";
import { api, ChapterDetail, EditorAction } from "../api";
import { pollJob } from "../pollJob";

export const STATUS_CN: Record<string, string> = {
  empty: "未生成", drafting: "生成中", drafted: "有草稿",
  finalized: "已定稿", stale: "大纲已变",
};

/** 正文分段:按空行/换行切开,去空白;阅读器渲染与片段替换共用同一套分段逻辑 */
export function splitParas(text: string): string[] {
  return text.split(/\n+/).map((s) => s.trim()).filter(Boolean);
}

/** 定位第 idx 个非空段落在原文中的字符区间(与 splitParas 同口径:按 \n 分行、trim、过滤空行)。
 *  用段落序号而非全文 indexOf(selText),避免正文里有重复段落时替换改错位置。 */
export function nthParaSpan(
  source: string, idx: number,
): { start: number; end: number; text: string } | null {
  const re = /[^\n]+/g;
  let count = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    const trimmed = m[0].trim();
    if (!trimmed) continue; // 纯空白行不算段落,与 filter(Boolean) 对齐
    if (count === idx) {
      // 收窄到 trim 后的区间,使 text 与 splitParas 产出的段落逐字一致(替换只动正文、不吞行内缩进)
      const lead = m[0].length - m[0].trimStart().length;
      const start = m.index + lead;
      return { start, end: start + trimmed.length, text: trimmed };
    }
    count++;
  }
  return null;
}

/** 正文按空行/换行分段渲染成 <p>,保证可读性;传 onSelect 时段落可点选(片段润色用) */
export function Paragraphs({ text, selectedIdx, onSelect }: {
  text: string;
  selectedIdx?: number | null;
  onSelect?: (idx: number) => void;
}) {
  const paras = splitParas(text);
  if (!paras.length) return <div className="muted">(空)</div>;
  return <>{paras.map((p, i) => (
    <p
      key={i}
      className={
        (onSelect ? "pickable" : "") + (onSelect && selectedIdx === i ? " sel" : "") || undefined
      }
      onClick={onSelect ? (e) => { e.stopPropagation(); onSelect(i); } : undefined}
    >{p}</p>
  ))}</>;
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
// 常用润色方向(点一下填入输入框,可再改)
const DIRECTION_CHIPS = ["更生动", "更紧张", "更简洁", "去 AI 味"];

/** 全书目录条目:disabled 表示该章尚未生成正文(置灰不可点) */
export interface ReaderTocItem { num: number; label: string; disabled?: boolean; }

/** 片段润色上下文:由 BookReader / ChaptersPanel 传入以开启段落点选润色 */
export interface PolishCtx {
  pid: number;
  chapterNumber: number;
  onApplied: (updated: ChapterDetail) => void;
}

interface Props {
  loading: boolean;            // 翻章/加载中:禁用翻页按钮;chapter 为空时显示加载态
  chapter: ChapterDetail | null;
  title?: string;              // 章节标题(来自大纲)
  hasPrev: boolean;
  hasNext: boolean;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  toc?: {
    items: ReaderTocItem[];
    current: number | null;
    onSelect: (n: number) => void;
    bookTitle?: string;        // 全书模式:目录栏顶部书名
    synopsis?: string | null;  // 目录栏书名下的简介(无则不显示)
  };
  restoreScroll?: number | null;              // 全书模式:首次打开要恢复的滚动位置
  onScrollPos?: (chapterNum: number, scroll: number) => void; // 滚动位置上报(父级防抖持久化)
  polishCtx?: PolishCtx;       // 传入即开启「点选段落润色」
}

export default function Reader({
  loading, chapter, title, hasPrev, hasNext, onPrev, onNext, onClose, toc,
  restoreScroll, onScrollPos, polishCtx,
}: Props) {
  const [tab, setTab] = useState<"final" | "draft">("final");
  const [prefs, setPrefs] = useState<ReaderPrefs>(loadReaderPrefs);
  const [showSettings, setShowSettings] = useState(false);
  const [tocOpen, setTocOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  // 全书位置记忆:恢复滚动只在首个章节应用一次
  const restoreAppliedRef = useRef(false);
  // 上一次的章号:区分「换章」(要重置滚动/收面板)与「同章内容更新」(保持阅读位置)
  const prevChapterNumRef = useRef<number | null>(null);
  const scrollTimerRef = useRef<number | null>(null);

  // ---- 片段润色状态 ----
  const [selPara, setSelPara] = useState<number | null>(null);
  const [polishOpen, setPolishOpen] = useState(false);
  // 手动改段:选中段落直接改字(和 AI 润色共用替换+同步链路)
  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState("");
  // 编辑部预设优化动作(润色方向 chips;拉不到时退回内置四个)
  const [proseActions, setProseActions] = useState<EditorAction[]>([]);
  // 替换后同步引擎的轮询:关阅读器时中止,防卸载后 setState
  const applyAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => applyAbortRef.current?.abort(), []);
  useEffect(() => {
    if (!polishCtx) return;
    api.editorialActions().then((a) => setProseActions(a.prose)).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [direction, setDirection] = useState("");
  const [polishing, setPolishing] = useState(false);
  const [polished, setPolished] = useState<string | null>(null);
  const [applying, setApplying] = useState(false); // 替换存盘中(快;不含同步)
  const [polishErr, setPolishErr] = useState("");
  // 手改后「是否同步一致性引擎」询问(章号;null=无);同步本身非阻塞。
  const [pendingSyncNum, setPendingSyncNum] = useState<number | null>(null);
  // 阅读器内正在跑的一致性同步:非阻塞角标(章号 + 阶段),不挡阅读/翻章。
  const [syncNum, setSyncNum] = useState<number | null>(null);
  const [syncStage, setSyncStage] = useState("");

  // ---- 段落对话状态(选中段 → 问 AI:可解释、可给改写建议) ----
  const [discussOpen, setDiscussOpen] = useState(false);
  const [discussMsgs, setDiscussMsgs] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [discussInput, setDiscussInput] = useState("");
  const [discussing, setDiscussing] = useState(false);
  const [discussErr, setDiscussErr] = useState("");
  // 最近一条 AI 回复携带的改写建议(null=纯解释);采用即走 applyReplacement
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const discussLogRef = useRef<HTMLDivElement>(null);

  const closePolish = () => {
    setPolishOpen(false);
    setPolished(null);
    setPolishErr("");
    setDirection("");
  };

  const closeDiscuss = () => {
    setDiscussOpen(false);
    setDiscussMsgs([]);
    setDiscussInput("");
    setDiscussErr("");
    setSuggestion(null);
  };

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

  // 换章 vs 同章内容更新:
  // - 换章(章号变了):默认看定稿,收起设置/目录/润色,清除段落选择;全书模式首章恢复记忆位置,之后翻章回顶。
  // - 同章内容更新(手动改/润色替换后 onApplied 回填同一章):保持滚动位置,不收回顶,避免阅读进度丢失。
  useEffect(() => {
    if (!chapter) return;
    const num = chapter.chapter_number;
    const switched = prevChapterNumRef.current !== num;
    prevChapterNumRef.current = num;
    setTab(chapter.final_content ? "final" : "draft");
    if (!switched) return; // 同章更新:不动滚动位置与面板
    setShowSettings(false);
    setTocOpen(false);
    setSelPara(null);
    setPolishOpen(false);
    setPolished(null);
    setPolishErr("");
    setEditOpen(false);
    closeDiscuss(); // 换章关掉段落对话,清空历史
    setPendingSyncNum(null); // 换章丢弃未决的同步询问,避免「第 N 章已保存」串到别章头上
    // 注:syncNum(正在跑的后台同步角标)不清 —— 非阻塞,允许换章后继续显示直到完成
    const target = !restoreAppliedRef.current && restoreScroll != null ? restoreScroll : 0;
    restoreAppliedRef.current = true;
    contentRef.current?.scrollTo(0, target);
  }, [chapter, restoreScroll]);

  // Esc:先关对话/润色弹层 → 再取消段落选择 → 最后才关阅读器
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (discussOpen) { if (!discussing) closeDiscuss(); return; }
      if (polishOpen) { closePolish(); return; }
      if (editOpen) { setEditOpen(false); return; }
      if (selPara != null) { setSelPara(null); return; }
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, discussOpen, discussing, polishOpen, editOpen, selPara]);

  // 卸载时清掉滚动防抖定时器
  useEffect(() => () => {
    if (scrollTimerRef.current) window.clearTimeout(scrollTimerRef.current);
  }, []);

  // 对话有新消息时,聊天记录滚到底
  useEffect(() => {
    if (discussOpen) discussLogRef.current?.scrollTo(0, discussLogRef.current.scrollHeight);
  }, [discussMsgs, discussing, discussOpen]);

  // 滚动 ~500ms 防抖后上报位置(全书模式父级持久化到 localStorage)
  const handleContentScroll = () => {
    if (!onScrollPos || !chapter) return;
    if (scrollTimerRef.current) window.clearTimeout(scrollTimerRef.current);
    scrollTimerRef.current = window.setTimeout(() => {
      onScrollPos(chapter.chapter_number, contentRef.current?.scrollTop ?? 0);
    }, 500);
  };

  const curText = chapter
    ? (tab === "final" ? chapter.final_content || chapter.draft_content : chapter.draft_content)
    : "";
  const paras = curText ? splitParas(curText) : [];
  const selText = selPara != null && selPara < paras.length ? paras[selPara] : null;
  // 只在定稿 tab 且有定稿正文时允许点选润色(替换目标是 final_content)
  const polishEnabled = !!polishCtx && tab === "final" && !!chapter?.final_content;

  async function doPolish() {
    if (!polishCtx || selText == null) return;
    setPolishing(true); setPolishErr("");
    try {
      const r = await api.polishFragment(
        polishCtx.pid, polishCtx.chapterNumber, selText, direction.trim(),
      );
      setPolished(r.polished);
    } catch (e) {
      setPolishErr(e instanceof Error ? e.message : String(e));
    } finally { setPolishing(false); }
  }

  // 把选中段替换为 replacement 并落库(AI 润色应用与手动改共用)。
  // 只做「快速存盘」——绝不在此阻塞同步一致性引擎。是否同步由 askSync 决定,
  // 且同步永远走非阻塞角标(startReaderSync),不挡阅读/翻章:
  //  - 润色:只改文笔不动情节 → askSync=false,从不同步。
  //  - 手改:可能动情节 → askSync=true,存完弹一句问,用户要才后台同步。
  async function applyReplacement(replacement: string, askSync: boolean) {
    if (!polishCtx || !chapter || selText == null || selPara == null) return;
    const source = chapter.final_content;
    // 按段落序号(而非全文 indexOf)精确定位:正文里有重复段落时也不会改错位置。
    // span.text 必须与当前选中段逐字一致,否则说明正文已被别处改动,选择已失效。
    const span = nthParaSpan(source, selPara);
    if (!span || span.text !== selText) {
      setPolishErr("在定稿正文中找不到该段落(可能已被修改),请关闭阅读器重试");
      return;
    }
    const newContent = source.slice(0, span.start) + replacement + source.slice(span.end);
    setApplying(true); setPolishErr("");
    try {
      const updated = await api.editChapterContent(polishCtx.pid, polishCtx.chapterNumber, newContent);
      polishCtx.onApplied(updated);
      closePolish();
      setEditOpen(false);
      setSelPara(null);
      if (askSync) setPendingSyncNum(polishCtx.chapterNumber);
    } catch (e) {
      setPolishErr(e instanceof Error ? e.message : String(e));
    } finally { setApplying(false); }
  }

  async function applyPolish() {
    if (polished == null) return;
    await applyReplacement(polished, false);
  }

  // 阅读器内的一致性同步:非阻塞角标,不挡阅读/翻章(手改后用户选「同步」时触发)。
  async function startReaderSync(num: number) {
    if (!polishCtx) return;
    setPendingSyncNum(null);
    const ctrl = new AbortController();
    applyAbortRef.current = ctrl;
    setSyncNum(num); setSyncStage("启动同步…");
    try {
      const { job_id } = await api.reExtractAsync(polishCtx.pid, num);
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (s) => setSyncStage(s || "同步中…"),
      });
    } catch {
      // 非阻塞:失败静默(任务可能仍在后台跑),不打断阅读
    } finally {
      if (!ctrl.signal.aborted) { setSyncNum(null); setSyncStage(""); }
    }
  }

  // 段落对话:发一句 → 追加到历史 → 请求 AI(带选段原文,后端自动补上下文)。
  // AI 回复可能携带改写建议(suggestion),浮出「采用此改写」按钮。
  async function sendDiscuss() {
    if (!polishCtx || selText == null) return;
    const text = discussInput.trim();
    if (!text || discussing) return;
    const next = [...discussMsgs, { role: "user" as const, content: text }];
    setDiscussMsgs(next);
    setDiscussInput("");
    setDiscussing(true); setDiscussErr(""); setSuggestion(null);
    try {
      const r = await api.discussFragment(polishCtx.pid, polishCtx.chapterNumber, next, selText);
      setDiscussMsgs((m) => [...m, { role: "assistant", content: r.reply || "(见下方改写建议)" }]);
      setSuggestion(r.suggestion);
    } catch (e) {
      // 失败时回退刚发出的那条,方便用户重发
      setDiscussMsgs((m) => m.slice(0, -1));
      setDiscussInput(text);
      setDiscussErr(e instanceof Error ? e.message : String(e));
    } finally { setDiscussing(false); }
  }

  // 采用对话里的改写建议:走与润色相同的替换+同步链路(改了文字→不问同步)。
  async function adoptSuggestion() {
    if (suggestion == null) return;
    await applyReplacement(suggestion, false);
    closeDiscuss();
  }

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
                    onClick={() => { setTab("final"); setSelPara(null); closePolish(); }}
                  >定稿</span>
                  <span
                    className={"reader-tab" + (tab === "draft" ? " on" : "")}
                    onClick={() => { setTab("draft"); setSelPara(null); closePolish(); }}
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
            {(pendingSyncNum !== null || syncNum !== null) && (
              <div className="reader-sync-bar">
                {pendingSyncNum !== null && (
                  <div className="sync-ask">
                    <span className="sync-ask-text">
                      第 {pendingSyncNum} 章已保存。改动了情节吗?要同步一致性引擎吗?
                      <b className="hint">同步会更新人物状态、伏笔与后续章节的前情摘要;只改了文字/措辞可以跳过。</b>
                    </span>
                    <span className="sync-ask-actions">
                      <button className="primary btn-sm" disabled={syncNum !== null}
                        onClick={() => startReaderSync(pendingSyncNum)}>同步</button>
                      <button className="btn-sm" onClick={() => setPendingSyncNum(null)}>跳过</button>
                    </span>
                  </div>
                )}
                {syncNum !== null && (
                  <div className="sync-badge">
                    <span className="spin spin-sm" />
                    <span>第 {syncNum} 章同步一致性引擎中({syncStage})· 不影响继续阅读</span>
                  </div>
                )}
              </div>
            )}
            <div className="reader-body">
              {toc && (
                <div className={"reader-toc" + (tocOpen ? " open" : "")}>
                  {toc.bookTitle && (
                    <div className="reader-toc-book">
                      <div className="reader-toc-book-title">{toc.bookTitle}</div>
                      {toc.synopsis && (
                        <div className="reader-toc-book-syn" title={toc.synopsis}>
                          {toc.synopsis}
                        </div>
                      )}
                    </div>
                  )}
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
              <div
                className="reader-content"
                ref={contentRef}
                onScroll={handleContentScroll}
                onClick={(e) => {
                  // 点正文空白处取消段落选择(点段落本身已 stopPropagation)
                  if (e.target === e.currentTarget) setSelPara(null);
                }}
              >
                <Paragraphs
                  text={curText}
                  selectedIdx={polishEnabled ? selPara : null}
                  onSelect={polishEnabled ? (i) => setSelPara(i) : undefined}
                />
              </div>
              {polishEnabled && selPara != null && !polishOpen && !editOpen && !discussOpen && (
                <div className="para-tools">
                  <button className="btn-sm primary" onClick={() => setDiscussOpen(true)}>
                    💬 问 AI
                  </button>
                  <button className="btn-sm" onClick={() => setPolishOpen(true)}>
                    ✨ 润色此段
                  </button>
                  <button className="btn-sm" onClick={() => { setEditText(selText ?? ""); setEditOpen(true); }}>
                    ✍️ 手动改
                  </button>
                  <button className="btn-sm" onClick={() => setSelPara(null)}>取消选择</button>
                </div>
              )}
            </div>
            <div className="reader-nav">
              <button disabled={!hasPrev || loading} onClick={onPrev}>
                ← 上一章
              </button>
              <button disabled={!hasNext || loading} onClick={onNext}>
                下一章 →
              </button>
            </div>
            {polishOpen && selText != null && (
              <div className="reader-polish" onClick={() => { if (!polishing && !applying) closePolish(); }}>
                <div className="reader-polish-panel" onClick={(e) => e.stopPropagation()}>
                  {polished == null ? (
                    <>
                      <div className="rp-label">选中段落</div>
                      <div className="rp-orig">{selText}</div>
                      <div className="rp-label">润色方向(只改文笔,不动情节)</div>
                      <input
                        type="text"
                        value={direction}
                        placeholder="如:更紧张一些 / 去掉 AI 腔"
                        onChange={(e) => setDirection(e.target.value)}
                      />
                      <div className="chips rp-chips">
                        {(proseActions.length
                          ? proseActions.map((a) => ({ label: a.label, value: a.directive }))
                          : DIRECTION_CHIPS.map((c) => ({ label: c, value: c }))
                        ).map((c) => (
                          <span
                            key={c.label}
                            className={"chip" + (direction === c.value ? " on" : "")}
                            onClick={() => setDirection(c.value)}
                          >{c.label}</span>
                        ))}
                      </div>
                      <div className="rp-actions">
                        <button className="primary" disabled={polishing} onClick={doPolish}>
                          {polishing && <span className="spin" />}开始润色
                        </button>
                        <button disabled={polishing} onClick={closePolish}>取消</button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="rp-compare">
                        <div className="rp-col">
                          <div className="rp-label">原文</div>
                          <div className="rp-text">{selText}</div>
                        </div>
                        <div className="rp-col">
                          <div className="rp-label">润色后</div>
                          <div className="rp-text rp-new">{polished}</div>
                        </div>
                      </div>
                      <div className="rp-actions">
                        <button className="primary" disabled={applying} onClick={applyPolish}>
                          {applying && <span className="spin" />}
                          {applying ? "替换中…" : "替换原文"}
                        </button>
                        <button
                          disabled={applying}
                          onClick={() => { setPolished(null); setPolishErr(""); }}
                        >重新润色</button>
                        <button disabled={applying} onClick={closePolish}>取消</button>
                      </div>
                    </>
                  )}
                  {polishErr && <div className="msg-err rp-err">{polishErr}</div>}
                </div>
              </div>
            )}
            {editOpen && selText != null && (
              <div className="reader-polish" onClick={() => { if (!applying) setEditOpen(false); }}>
                <div className="reader-polish-panel" onClick={(e) => e.stopPropagation()}>
                  <div className="rp-label">手动修改此段(只动这一段;保存后可选同步一致性引擎)</div>
                  <textarea
                    rows={Math.min(12, Math.max(4, Math.ceil(editText.length / 40)))}
                    value={editText}
                    autoFocus
                    onChange={(e) => setEditText(e.target.value)}
                  />
                  <div className="rp-actions">
                    <button className="primary"
                      disabled={applying || !editText.trim() || editText === selText}
                      onClick={() => applyReplacement(editText.trim(), true)}>
                      {applying && <span className="spin" />}
                      {applying ? "保存中…" : "保存修改"}
                    </button>
                    <button disabled={applying} onClick={() => setEditOpen(false)}>取消</button>
                  </div>
                  {polishErr && <div className="msg-err rp-err">{polishErr}</div>}
                </div>
              </div>
            )}
            {discussOpen && selText != null && (
              <div className="reader-polish" onClick={() => { if (!discussing && !applying) closeDiscuss(); }}>
                <div className="reader-polish-panel reader-discuss-panel" onClick={(e) => e.stopPropagation()}>
                  <div className="rp-label">与 AI 聊这一段(可以问它什么意思,也可以让它帮你改)</div>
                  <div className="rd-orig">{selText}</div>
                  <div className="rd-log" ref={discussLogRef}>
                    {discussMsgs.length === 0 && !discussing && (
                      <div className="muted rd-empty">
                        试试:「这段是什么意思?」「这里为什么这么写?」「帮我改得紧张一点」
                      </div>
                    )}
                    {discussMsgs.map((m, i) => (
                      <div key={i} className={"rd-msg rd-" + m.role}>
                        <div className="rd-bubble">{m.content}</div>
                      </div>
                    ))}
                    {discussing && (
                      <div className="rd-msg rd-assistant">
                        <div className="rd-bubble muted"><span className="spin spin-sm" />思考中…</div>
                      </div>
                    )}
                  </div>
                  {suggestion != null && (
                    <div className="rd-suggestion">
                      <div className="rp-label">AI 给出的改写(采用后替换这一段)</div>
                      <div className="rp-text rp-new">{suggestion}</div>
                      <div className="rp-actions">
                        <button className="primary" disabled={applying} onClick={adoptSuggestion}>
                          {applying && <span className="spin" />}
                          {applying ? "替换中…" : "采用此改写"}
                        </button>
                        <button disabled={applying} onClick={() => setSuggestion(null)}>不用,继续聊</button>
                      </div>
                    </div>
                  )}
                  <div className="rd-input">
                    <textarea
                      rows={2}
                      value={discussInput}
                      placeholder="问点什么,或说说想怎么改…(Enter 发送,Shift+Enter 换行)"
                      disabled={discussing}
                      onChange={(e) => setDiscussInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          sendDiscuss();
                        }
                      }}
                    />
                    <div className="rp-actions">
                      <button className="primary" disabled={discussing || !discussInput.trim()} onClick={sendDiscuss}>
                        {discussing && <span className="spin" />}发送
                      </button>
                      <button disabled={discussing} onClick={closeDiscuss}>关闭</button>
                    </div>
                  </div>
                  {discussErr && <div className="msg-err rp-err">{discussErr}</div>}
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="reader-content muted"><span className="spin" />加载正文…</div>
        )}
      </div>
    </div>
  );
}
