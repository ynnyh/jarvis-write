// 写作面板:逐章生成 / 阅读;本章蓝图上下文置顶;润色移步「润色」工作区
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, ChapterBrief, ChapterDetail, GenerateChapterResponse, Outline, Project, Tendency,
} from "../api";
import { pollJob } from "../pollJob";
import TendencySelector from "../components/TendencySelector";

interface Props { pid: number; project: Project; outlines: Outline[]; }

const STATUS_CN: Record<string, string> = {
  empty: "未生成", drafting: "生成中", drafted: "有草稿",
  finalized: "已定稿", stale: "大纲已变",
};

/** 正文按空行/换行分段渲染成 <p>,保证可读性 */
function Paragraphs({ text }: { text: string }) {
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

export default function ChaptersPanel({ pid, outlines }: Props) {
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [current, setCurrent] = useState<ChapterDetail | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [genResult, setGenResult] = useState<GenerateChapterResponse | null>(null);
  const [genTendency, setGenTendency] = useState<Tendency>({});
  const [showTendency, setShowTendency] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  // 阅读器(全屏遮罩):当前阅读章节 + 定稿/草稿 tab
  const [reader, setReader] = useState<ChapterDetail | null>(null);
  const [readerLoading, setReaderLoading] = useState(false);
  const [readerTab, setReaderTab] = useState<"final" | "draft">("final");
  // 阅读器设置:主题/字体/字号 + 设置面板开合
  const [readerPrefs, setReaderPrefs] = useState<ReaderPrefs>(loadReaderPrefs);
  const [showReaderSettings, setShowReaderSettings] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);
  // 组件卸载时中止轮询,防止卸载后继续 setState
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  // 偏好变化即写入 localStorage(隐私模式等写失败时静默忽略)
  useEffect(() => {
    try { localStorage.setItem(READER_PREFS_KEY, JSON.stringify(readerPrefs)); } catch { /* ignore */ }
  }, [readerPrefs]);

  // 设置面板:点击面板外任意处收起
  useEffect(() => {
    if (!showReaderSettings) return;
    const onDown = (e: MouseEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setShowReaderSettings(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [showReaderSettings]);

  const reload = useCallback(async () => {
    setChapters(await api.listChapters(pid));
  }, [pid]);
  useEffect(() => { reload().catch((e) => setErr(String(e))); }, [reload]);

  const byNum = new Map(chapters.map((c) => [c.chapter_number, c]));
  const currentOutline = current
    ? outlines.find((o) => o.chapter_number === current.chapter_number)
    : null;

  async function open(n: number) {
    setErr(""); setGenResult(null); setEditing(false);
    try { setCurrent(await api.getChapter(pid, n)); } catch (e) { setErr(String(e)); }
  }

  // 阅读器:打开/翻章都走这里;默认看定稿,无定稿看草稿
  async function openReader(n: number) {
    setReaderLoading(true); setErr(""); setShowReaderSettings(false);
    try {
      const detail = await api.getChapter(pid, n);
      setReader(detail);
      setReaderTab(detail.final_content ? "final" : "draft");
    } catch (e) { setErr(String(e)); } finally { setReaderLoading(false); }
  }

  // 上一章/下一章:仅限已生成的章节
  const generatedNums = chapters.map((c) => c.chapter_number);
  const readerIdx = reader ? generatedNums.indexOf(reader.chapter_number) : -1;
  const prevNum = readerIdx > 0 ? generatedNums[readerIdx - 1] : null;
  const nextNum = readerIdx >= 0 && readerIdx < generatedNums.length - 1
    ? generatedNums[readerIdx + 1] : null;
  const readerOutline = reader
    ? outlines.find((o) => o.chapter_number === reader.chapter_number)
    : null;

  // Esc 关闭阅读器
  useEffect(() => {
    if (!reader) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setReader(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reader]);

  async function saveEdit() {
    if (!current) return;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy("保存正文…"); setErr("");
    try {
      const updated = await api.editChapterContent(pid, current.chapter_number, editText);
      setCurrent(updated);
      setEditing(false);
      await reload();
      // 手改后同步一致性引擎:重抽取 + 重建下游摘要 + 向量库
      const { job_id } = await api.reExtractAsync(pid, current.chapter_number);
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setBusy(`同步一致性引擎:${stage}`),
      });
    } catch (e) {
      if (!ctrl.signal.aborted) setErr(String(e));
    } finally { if (!ctrl.signal.aborted) setBusy(""); }
  }

  async function generate(n: number) {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setErr(""); setGenResult(null);
    setBusy(`第 ${n} 章:排队中…`);
    try {
      const { job_id } = await api.generateChapterAsync(pid, n, genTendency);
      // 轮询任务进度(五段:草稿→定稿→检查→抽取→摘要)
      const result = await pollJob<GenerateChapterResponse>(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setBusy(`第 ${n} 章:${stage}`),
      });
      if (ctrl.signal.aborted) return;
      setGenResult(result);
      setCurrent({
        chapter_number: result.chapter_number, status: result.status,
        word_count: result.word_count, is_stale: result.is_stale,
        draft_content: result.draft_content, final_content: result.final_content,
        outline_version_used: result.outline_version_used,
      });
      await reload();
    } catch (e) {
      if (!ctrl.signal.aborted) setErr(String(e));
    } finally { if (!ctrl.signal.aborted) setBusy(""); }
  }

  return (
    <div className="two-col">
      <div className="two-col-side">
        <div className="card card-compact">
          <div className="card-head mb-2">
            <h3 className="grow">章节</h3>
            <button className="btn-sm" onClick={() => setShowTendency(!showTendency)}>
              {showTendency ? "收起" : "正文倾向"}
            </button>
          </div>
          {showTendency && (
            <div className="mb-3">
              <TendencySelector node="chapter" value={genTendency} onChange={setGenTendency} compact />
            </div>
          )}
          {outlines.map((o) => {
            const ch = byNum.get(o.chapter_number);
            const st = ch?.status ?? "empty";
            return (
              <div key={o.chapter_number} className="fact-line fact-row">
                <span className={"fact-title" + (ch ? " linkish" : "")}
                  onClick={() => ch && open(o.chapter_number)}>
                  <b>第{o.chapter_number}章</b> {o.title}
                  <span className={"badge " + (ch?.is_stale ? "err" : st === "finalized" ? "ok" : "")}>
                    {ch?.is_stale ? "大纲已变" : STATUS_CN[st] ?? st}
                  </span>
                  {ch && <span className="muted"> {ch.word_count}字</span>}
                </span>
                {ch && (
                  <button className="btn-sm" disabled={!!busy} onClick={() => openReader(o.chapter_number)}>
                    阅读
                  </button>
                )}
                <button className="btn-sm" disabled={!!busy} onClick={() => generate(o.chapter_number)}>
                  {ch ? "重写" : "生成"}
                </button>
              </div>
            );
          })}
        </div>
        {busy && <div className="card muted"><span className="spin" />{busy}</div>}
        {err && <div className="msg-err">{err}</div>}
      </div>

      <div className="two-col-main">
        {genResult && (
          <div className="card card-ok">
            <b>生成完成</b> {genResult.word_count} 字
            {genResult.consistency_issues.length
              ? <div className="mt-2">
                  <span className="badge err">一致性问题 {genResult.consistency_issues.length}</span>
                  {genResult.consistency_issues.map((i, k) => (
                    <div key={k} className="fact-line">
                      <b>[{i.severity}]</b> {i.description}
                      <div className="muted">建议: {i.suggestion}</div>
                    </div>
                  ))}
                </div>
              : <span className="badge ok">一致性检查通过</span>}
          </div>
        )}

        {current ? (
          <>
            {currentOutline && (
              <div className="card card-info">
                <b>本章蓝图</b> 第{currentOutline.chapter_number}章《{currentOutline.title}》
                <span className="badge">{currentOutline.chapter_role}</span>
                <div className="muted mt-1">{currentOutline.summary}</div>
                <div className="meta-line">
                  伏笔:{currentOutline.foreshadowing || "无"}
                </div>
              </div>
            )}
            <div className="card">
              <div className="card-head mb-2">
                <h2 className="grow">
                  第{current.chapter_number}章 正文
                  <span className="hint"> {current.word_count}字</span>
                </h2>
                {!editing ? (
                  <>
                    <button onClick={() => {
                      setEditText(current.final_content || current.draft_content);
                      setEditing(true);
                    }}>编辑正文</button>
                    <span className="muted">改文笔?去「润色」</span>
                  </>
                ) : (
                  <>
                    <button className="primary" disabled={!!busy} onClick={saveEdit}>
                      {busy && <span className="spin" />}保存(自动同步一致性引擎)
                    </button>
                    <button disabled={!!busy} onClick={() => setEditing(false)}>取消</button>
                  </>
                )}
              </div>
              {editing ? (
                <textarea
                  className="editor-area"
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                />
              ) : (
                <div className="prose">
                  <Paragraphs text={current.final_content || current.draft_content} />
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="card muted">
            左侧点「生成」写新章,点「阅读」全屏读正文,点章节标题看蓝图/改正文。生成时自动注入:
            本章蓝图、前情摘要、最近章节结尾、人物当前状态(硬约束)、到期伏笔提醒、重复用词避免清单。
          </div>
        )}
      </div>

      {(reader || readerLoading) && (
        <div className="reader-overlay" onClick={() => setReader(null)}>
          <div
            className="reader"
            data-theme={readerPrefs.theme}
            data-font={readerPrefs.font}
            data-size={readerPrefs.size}
            onClick={(e) => e.stopPropagation()}
          >
            {reader ? (
              <>
                <div className="reader-head">
                  <h2 className="reader-title">
                    第{reader.chapter_number}章 {readerOutline?.title ?? ""}
                    <span className={"badge " + (reader.is_stale ? "err" : reader.status === "finalized" ? "ok" : "")}>
                      {reader.is_stale ? "大纲已变" : STATUS_CN[reader.status] ?? reader.status}
                    </span>
                    <span className="muted"> {reader.word_count}字</span>
                  </h2>
                  {reader.draft_content && reader.draft_content !== reader.final_content && (
                    <div className="reader-tabs">
                      <span
                        className={"reader-tab" + (readerTab === "final" ? " on" : "")}
                        onClick={() => setReaderTab("final")}
                      >定稿</span>
                      <span
                        className={"reader-tab" + (readerTab === "draft" ? " on" : "")}
                        onClick={() => setReaderTab("draft")}
                      >草稿</span>
                    </div>
                  )}
                  <div className="reader-settings" ref={settingsRef}>
                    <button className="btn-sm" onClick={() => setShowReaderSettings((v) => !v)}>
                      设置
                    </button>
                    {showReaderSettings && (
                      <div className="reader-settings-pop">
                        <div className="rs-group">
                          <div className="rs-label">背景</div>
                          <div className="chips">
                            {THEME_OPTIONS.map((o) => (
                              <span
                                key={o.v}
                                className={"chip" + (readerPrefs.theme === o.v ? " on" : "")}
                                onClick={() => setReaderPrefs((p) => ({ ...p, theme: o.v }))}
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
                                className={"chip " + o.cls + (readerPrefs.font === o.v ? " on" : "")}
                                onClick={() => setReaderPrefs((p) => ({ ...p, font: o.v }))}
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
                                className={"chip" + (readerPrefs.size === o.v ? " on" : "")}
                                onClick={() => setReaderPrefs((p) => ({ ...p, size: o.v }))}
                              >{o.label}</span>
                            ))}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                  <button onClick={() => setReader(null)}>关闭</button>
                </div>
                <div className="reader-content">
                  <Paragraphs
                    text={readerTab === "final"
                      ? reader.final_content || reader.draft_content
                      : reader.draft_content}
                  />
                </div>
                <div className="reader-nav">
                  <button disabled={prevNum == null || readerLoading}
                    onClick={() => prevNum != null && openReader(prevNum)}>
                    ← 上一章
                  </button>
                  <button disabled={nextNum == null || readerLoading}
                    onClick={() => nextNum != null && openReader(nextNum)}>
                    下一章 →
                  </button>
                </div>
              </>
            ) : (
              <div className="reader-content muted"><span className="spin" />加载正文…</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
