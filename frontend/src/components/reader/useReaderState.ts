// 阅读器的全部交互状态与副作用:偏好持久化、换章 vs 同章更新、Esc 分层关闭、
// 滚动位置上报,以及片段润色 / 手动改段 / 段落对话及其替换 + 一致性同步链路。拆自 Reader.tsx。
// 组件只负责渲染,状态与逻辑集中在此 hook(行为与拆分前逐字一致)。
import { useEffect, useRef, useState } from "react";
import { api, ChapterDetail, EditorAction } from "../../api";
import { pollJob, errMsg } from "../../pollJob";
import { nthParaSpan, splitParas } from "./paragraphs";
import { loadReaderPrefs, READER_PREFS_KEY, ReaderPrefs } from "./prefs";

/** 片段润色上下文:由 BookReader / WritePanel 传入以开启段落点选润色 */
export interface PolishCtx {
  pid: number;
  chapterNumber: number;
  onApplied: (updated: ChapterDetail) => void;
}

/** useReaderState 的输入:阅读器 Props 中与状态逻辑相关的子集 */
interface ReaderStateOpts {
  chapter: ChapterDetail | null;
  restoreScroll?: number | null;              // 全书模式:首次打开要恢复的滚动位置
  onScrollPos?: (chapterNum: number, scroll: number) => void; // 滚动位置上报(父级防抖持久化)
  onClose: () => void;
  polishCtx?: PolishCtx;       // 传入即开启「点选段落润色」
}

export function useReaderState({
  chapter, restoreScroll, onScrollPos, onClose, polishCtx,
}: ReaderStateOpts) {
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
      setPolishErr(errMsg(e));
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
      setPolishErr(errMsg(e));
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
      setDiscussErr(errMsg(e));
    } finally { setDiscussing(false); }
  }

  // 采用对话里的改写建议:走与润色相同的替换+同步链路(改了文字→不问同步)。
  async function adoptSuggestion() {
    if (suggestion == null) return;
    await applyReplacement(suggestion, false);
    closeDiscuss();
  }

  return {
    tab, setTab, prefs, setPrefs, showSettings, setShowSettings, tocOpen, setTocOpen,
    settingsRef, contentRef, discussLogRef,
    selPara, setSelPara, polishOpen, setPolishOpen, editOpen, setEditOpen,
    editText, setEditText, proseActions, direction, setDirection,
    polishing, polished, setPolished, applying, polishErr, setPolishErr,
    pendingSyncNum, setPendingSyncNum, syncNum, syncStage,
    discussOpen, setDiscussOpen, discussMsgs, discussInput, setDiscussInput,
    discussing, discussErr, suggestion, setSuggestion,
    closePolish, closeDiscuss, handleContentScroll,
    curText, selText, polishEnabled,
    doPolish, applyReplacement, applyPolish, startReaderSync, sendDiscuss, adoptSuggestion,
  };
}
