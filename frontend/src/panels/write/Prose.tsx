// write 区主场正文(「正文即界面」P1/P3,docs/10 §3/§6):常驻渲染可点选段落(复用 Reader 的
// Paragraphs/splitParas),点选后段落下沿浮出气泡卡,不遮挡正文——
//   💬 改这段:输方向(或点 chips)→ polish-fragment → 卡内字符级 diff(红删绿增,charDiff/DiffText)
//     → [接受替换](spliceParagraph + PUT content 写回;润色不动情节,不问同步)/[再改方向]/[放弃]。
//   ✍️ 手改:该段就地变为自适应高度编辑框,保存走同一条写回链路;是否同步一致性引擎
//     交给父级中栏的 sync-ask 询问条(onSyncAsk,旧整章编辑的 pendingSync 机制上移至此)。
//   🖍 批注(②档):只记一句意见(段号+原文快照),不马上改;攒够后在 AI 栏「按批注改」成批处理。
//     已批注段在正文高亮(markedIdx),正文变动后快照对不上者标失效(staleIdx)。
// 段落寻址=下标 + 原文快照守卫(spliceParagraph),正文被别处改动时报错不写回。
// 任一时刻最多一个段落处于气泡/编辑态;手改未保存经 onDirtyChange 上报,父级切章前弹确认。
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ChapterDetail, CharacterCard, CraftMode, EditorAction } from "../../api";
import { Paragraphs, splitParas } from "../../components/Reader";
import { useCharacters } from "../../hooks/queries";
import { emitChapterSaved } from "../../desktop";
import { errMsg } from "../../pollJob";
import { applyParaReplacement, applySelectionReplacement, appendParagraph, Annotation } from "./paraEdit";
import { buildEntityIndex, segmentParagraph, entitySummary, isEntitySeg } from "./entityLink";
import EntityCard from "./EntityCard";
import { diffChars } from "./charDiff";
import DiffText from "./DiffText";

// 润色方向 chips 兜底(editorialActions 拉不到时用,与 Reader 内置一致)
const DIRECTION_CHIPS = ["更生动", "更紧张", "更简洁", "去 AI 味"];

type BubbleMode = "pick" | "polish" | "edit" | "annotate";
// polish 工作台内的子工具:润色(改文笔不改情节)/ 描写 / 扩写 / 脑暴(给点子不改正文)。
// describe/expand 与润色共用 diff→接受→写回链路,brainstorm 只给点子不落库。
type CraftTool = "polish" | CraftMode;
const TOOL_LABEL: Record<CraftTool, string> = {
  polish: "润色", describe: "描写", expand: "扩写", brainstorm: "脑暴",
};
const TOOL_HINT: Record<CraftTool, string> = {
  polish: "只改文笔,不动情节",
  describe: "在不改情节的前提下补感官细节、增强画面感",
  expand: "在不改情节的前提下把这段放慢写透、适度拉长",
  brainstorm: "围绕这段给几条可往下写/改得更好的点子,不改正文",
};

// 选区某端点所在的、作为 .prose 直接子节点的 <p>(两端须落在同一段才认作段内选区)
function paraPOf(container: HTMLElement, node: Node): HTMLElement | null {
  let el: HTMLElement | null =
    node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as HTMLElement);
  while (el && el.parentElement !== container) el = el.parentElement;
  return el && el.tagName === "P" ? el : null;
}

// 选区端点在该段文本中的字符偏移:量取「段首→该点」的文本长度,不依赖段内节点结构
function offsetInPara(pEl: HTMLElement, container: Node, offset: number): number {
  const r = document.createRange();
  r.selectNodeContents(pEl);
  r.setEnd(container, offset);
  return r.toString().length;
}

interface Props {
  pid: number;
  chapter: ChapterDetail;
  // 任务锁:有生成/重写任务在跑时禁用气泡动作(与旧编辑正文/StageBar 同一规则)
  genBlocked: boolean;
  genHint: string;
  // 写回成功:父级更新 qk.chapter 缓存并刷新章节列表
  onSaved: (updated: ChapterDetail) => void;
  // 手改保存后:父级在中栏弹 sync-ask 询问条(章号)
  onSyncAsk: (num: number) => void;
  // 手改脏标记上报:父级切章前据此弹「丢弃修改」确认(沿用旧 confirmDiscardEdit 语义)
  onDirtyChange?: (dirty: boolean) => void;
  // 选中段上报(AI 窄栏的「第 N 段」引用与 discuss target 用它;取消选择/切章上报 null)
  onSelectChange?: (sel: { idx: number; text: string } | null) => void;
  // ②档批注(docs/10 §4):当前待处理批注(用于在正文高亮已批注段/失效段)
  annotations?: Annotation[];
  // 记下一条批注:段号 + 原文快照 + 一句话意见(父级 append 进 annotations)
  onAnnotate?: (idx: number, snapshot: string, note: string) => void;
}

export default function Prose({
  pid, chapter, genBlocked, genHint, onSaved, onSyncAsk, onDirtyChange, onSelectChange,
  annotations, onAnnotate,
}: Props) {
  // 当前选中段落(null=无选择);气泡模式:pick=两个动作入口 / polish=润色 / edit=手改
  const [selPara, setSelPara] = useState<number | null>(null);
  // 段内任意选区(拖选):null=整段为目标;{from,to}=段内子串为目标,内联操作只动这段选中文字
  const [selRange, setSelRange] = useState<{ from: number; to: number } | null>(null);
  const [mode, setMode] = useState<BubbleMode>("pick");
  const [direction, setDirection] = useState("");
  const [polishing, setPolishing] = useState(false);
  const [polished, setPolished] = useState<string | null>(null);
  // polish 工作台的子工具 + 脑暴点子结果(polished 复用为润色/描写/扩写三者的改写稿)
  const [tool, setTool] = useState<CraftTool>("polish");
  const [ideas, setIdeas] = useState<string[] | null>(null);
  // 章尾续写(ghost text):ghost=AI 续出的一段(null=未续/已收起);ghostBase=发起时正文快照(接受守卫)
  const [ghost, setGhost] = useState<string | null>(null);
  const [ghosting, setGhosting] = useState(false);
  const [ghostErr, setGhostErr] = useState("");
  const ghostBaseRef = useRef<string>("");
  const ghostAcceptRef = useRef<HTMLButtonElement>(null);
  const [editText, setEditText] = useState("");
  // ②档:批注意见输入(annotate 模式)
  const [note, setNote] = useState("");
  // 替换/保存存盘中(快;不含一致性同步)
  const [applying, setApplying] = useState(false);
  const [err, setErr] = useState("");
  // 气泡/编辑框的纵向锚点:相对 .prose-wrap 的 top(px),由选中段实测得出
  const [anchorTop, setAnchorTop] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const proseRef = useRef<HTMLDivElement>(null);
  // 键盘/读屏:点选段落(或从子模式返回)后把焦点移入气泡首个动作,焦点不滞留原处。
  // pendingFocusRef 门控——只在这些主动切换时移焦,resize 等重渲染不抢焦点。
  const firstActionRef = useRef<HTMLButtonElement>(null);
  const pendingFocusRef = useRef(false);

  // 编辑部预设优化动作(润色方向 chips;拉不到时退回内置四个)
  const [proseActions, setProseActions] = useState<EditorAction[]>([]);
  useEffect(() => {
    api.editorialActions().then((a) => setProseActions(a.prose)).catch(() => undefined);
  }, []);

  // 正文实体链接(对标 Codex):全书人物名/别名建索引,正文里命中处高亮,hover 浮出人物卡。
  const { data: characters } = useCharacters(pid);
  const entityIndex = useMemo(() => buildEntityIndex(characters ?? []), [characters]);
  // 当前悬浮展示的实体卡(null=不显示);top/left 相对 .prose-wrap 定位(与气泡同一 containing block)
  const [entityPop, setEntityPop] = useState<{ entity: CharacterCard; top: number; left: number } | null>(null);
  // hover 意图:离开高亮词后延迟隐藏,给鼠标移进卡片(选词/滚动)的余地;移进卡片则取消隐藏
  const hideTimer = useRef<number | null>(null);
  const cancelHideEntity = () => {
    if (hideTimer.current) { clearTimeout(hideTimer.current); hideTimer.current = null; }
  };
  const scheduleHideEntity = () => {
    cancelHideEntity();
    hideTimer.current = window.setTimeout(() => setEntityPop(null), 140);
  };
  function showEntity(entity: CharacterCard, el: HTMLElement) {
    cancelHideEntity();
    const wrap = wrapRef.current;
    if (!wrap) return;
    const w = wrap.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    setEntityPop({ entity, top: r.bottom - w.top + 4, left: Math.max(0, r.left - w.left) });
  }
  useEffect(() => () => cancelHideEntity(), []); // 卸载清定时器

  // 段落文本渲染:命中实体的子串包成 .entity-link(hover 出卡 + title 兜底),其余原样。
  // 无命中时返回纯字符串(不包裹),既省 DOM 也不改变段落文本本身,选区偏移与写回不受影响。
  function linkify(t: string): ReactNode {
    if (!entityIndex.length) return t;
    const segs = segmentParagraph(t, entityIndex);
    if (segs.length === 1 && !isEntitySeg(segs[0])) return t;
    return segs.map((s, k) =>
      isEntitySeg(s) ? (
        <span key={k} className={"entity-link" + (s.entity.retired ? " retired" : "")}
          title={entitySummary(s.entity)}
          onMouseEnter={(e) => showEntity(s.entity, e.currentTarget)}
          onMouseLeave={scheduleHideEntity}>
          {s.text}
        </span>
      ) : (
        s.text
      ),
    );
  }

  const text = chapter.final_content || chapter.draft_content;
  const paras = splitParas(text);
  const selText = selPara !== null && selPara < paras.length ? paras[selPara] : null;
  // 内联操作的目标文字:有选区=段内子串,否则=整段(润色调用与接受写回都以此为准)
  const targetText = selRange && selText !== null ? selText.slice(selRange.from, selRange.to) : selText;

  // ②档:已批注段落高亮;正文变动后快照对不上的批注标为「失效」(据此提示重记)
  const markedIdx = new Set<number>();
  const staleIdx = new Set<number>();
  for (const a of annotations ?? []) {
    markedIdx.add(a.paraIdx);
    if (paras[a.paraIdx] !== a.snapshot) staleIdx.add(a.paraIdx);
  }

  // 切章:收起气泡/编辑框,清空全部跟随旧章的视图态(脏确认由父级在切章前完成)
  useEffect(() => {
    setSelPara(null); setSelRange(null); setMode("pick"); setDirection(""); setPolished(null);
    setEditText(""); setNote(""); setErr(""); setAnchorTop(null);
    setTool("polish"); setIdeas(null);
    setGhost(null); setGhosting(false); setGhostErr("");
    setEntityPop(null); cancelHideEntity();
    onSelectChange?.(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapter.chapter_number]);

  // 手改脏标记:编辑框内容与原文不一致即算脏(上报父级做切章确认)
  const editDirty = mode === "edit" && selText !== null && editText !== selText;
  useEffect(() => {
    onDirtyChange?.(editDirty);
    return () => onDirtyChange?.(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editDirty]);

  // 选中段/气泡内容变化后重测锚点:气泡贴段落下沿,编辑框盖在段落原位
  useLayoutEffect(() => {
    if (selPara === null || !proseRef.current) { setAnchorTop(null); return; }
    const p = proseRef.current.querySelectorAll("p")[selPara];
    if (!p) { setAnchorTop(null); return; }
    setAnchorTop(mode === "edit" ? p.offsetTop : p.offsetTop + p.offsetHeight + 6);
  }, [selPara, mode, polished, ideas, tool, text]);

  // 窗口尺寸变化(侧栏开合/移动端旋转)时气泡跟着段落走
  useEffect(() => {
    if (selPara === null) return;
    const onResize = () => {
      const p = proseRef.current?.querySelectorAll("p")[selPara];
      if (p) setAnchorTop(mode === "edit" ? p.offsetTop : p.offsetTop + p.offsetHeight + 6);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [selPara, mode]);

  // 焦点移入气泡「改这段」:仅在 selectPara/backToPick 主动请求时(pendingFocus)执行,
  // 且待气泡实际渲染出来(anchorTop 就位、按钮已挂载)后触发,避免 resize 等重渲染抢焦点。
  useEffect(() => {
    if (pendingFocusRef.current && selPara !== null && mode === "pick" && firstActionRef.current) {
      firstActionRef.current.focus();
      pendingFocusRef.current = false;
    }
  }, [selPara, mode, anchorTop]);

  function selectPara(i: number) {
    if (applying || polishing) return; // 存盘/润色请求进行中不换目标
    // 段内正拖选着文字:让 onMouseUp 的选区流处理,别被整段点选覆盖
    // (键盘 Enter/Space 触发时选区是收拢的,不受此守卫影响)
    const s = window.getSelection();
    if (s && !s.isCollapsed && s.toString().trim() && proseRef.current?.contains(s.anchorNode)) return;
    if (mode === "edit" && selText !== null && editText !== selText) return; // 手改未保存:先保存/取消,防误点丢稿
    setSelPara(i); setSelRange(null); setMode("pick"); setDirection(""); setPolished(null); setNote(""); setErr("");
    setTool("polish"); setIdeas(null);
    pendingFocusRef.current = true; // 键盘点选后焦点移入气泡「改这段」
    onSelectChange?.({ idx: i, text: paras[i] });
  }
  // 从子模式(润色方向/批注/手改)返回时也把焦点收回气泡首个动作,键盘操作不断链
  function backToPick() {
    pendingFocusRef.current = true;
    setMode("pick");
  }
  function clearSelection() {
    setSelPara(null); setSelRange(null); setMode("pick"); setDirection(""); setPolished(null); setNote(""); setErr("");
    setTool("polish"); setIdeas(null);
    onSelectChange?.(null);
  }

  // 拖选任意文字(限单段内):进 polish 模式对选区润色,接受时走 applySelectionReplacement 子串写回。
  // 跨段/越界/纯空白选择一律忽略(不弹内联气泡),整段点选与键盘选择走 selectPara,互不干扰。
  function openSelection() {
    if (applying || polishing) return;
    if (mode === "edit" && selText !== null && editText !== selText) return; // 手改未保存:不抢
    const container = proseRef.current;
    const sel = window.getSelection();
    if (!container || !sel || sel.isCollapsed || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    const pEl = paraPOf(container, range.startContainer);
    if (!pEl || pEl !== paraPOf(container, range.endContainer)) return; // 跨段选区:不处理
    const idx = Array.from(container.querySelectorAll<HTMLElement>("p")).indexOf(pEl);
    if (idx < 0 || idx >= paras.length) return;
    let from = offsetInPara(pEl, range.startContainer, range.startOffset);
    let to = offsetInPara(pEl, range.endContainer, range.endOffset);
    if (from > to) [from, to] = [to, from];
    if (!paras[idx].slice(from, to).trim()) return; // 纯空白选择忽略
    setSelPara(idx); setSelRange({ from, to }); setMode("polish");
    setDirection(""); setPolished(null); setNote(""); setErr("");
    setTool("polish"); setIdeas(null);
    onSelectChange?.({ idx, text: paras[idx].slice(from, to) });
  }

  // 改这段:方向(chips 或手输)→ polish-fragment(同步端点,spinner 在按钮上)。
  // 目标为 targetText:整段选中时=整段,拖选时=段内选中的那段文字。
  async function doPolish() {
    if (targetText === null) return;
    setPolishing(true); setErr("");
    try {
      const r = await api.polishFragment(pid, chapter.chapter_number, targetText, direction.trim());
      setPolished(r.polished);
    } catch (e) {
      setErr(errMsg(e));
    } finally { setPolishing(false); }
  }

  // 切换 polish 工作台子工具(润色/描写/扩写/脑暴):清掉上个工具的结果与输入,回到该工具的输入屏
  function selectTool(t: CraftTool) {
    setTool(t); setPolished(null); setIdeas(null); setDirection(""); setErr("");
  }

  // 描写/扩写/脑暴:调 craft-fragment。describe/expand 回改写稿(存 polished,走 diff 接受);
  // brainstorm 回点子列表(存 ideas,不写回)。目标同为 targetText(整段或段内选区)。
  async function doCraft() {
    if (targetText === null || tool === "polish") return; // tool 收窄为 CraftMode
    setPolishing(true); setErr("");
    try {
      const r = await api.craftFragment(pid, chapter.chapter_number, targetText, tool, direction.trim());
      if (tool === "brainstorm") setIdeas(r.ideas ?? []);
      else setPolished(r.rewrite ?? "");
    } catch (e) {
      setErr(errMsg(e));
    } finally { setPolishing(false); }
  }
  // 有段内选区 → 子串写回(applySelectionReplacement);否则整段写回(applyParaReplacement)。
  // 只做快速存盘:润色(askSync=false)不动情节不问同步;手改(askSync=true)交父级询问。
  async function applyReplacement(replacement: string, askSync: boolean) {
    if (selPara === null || selText === null) return;
    setApplying(true); setErr("");
    try {
      const updated = selRange
        ? await applySelectionReplacement(
            pid, chapter, selPara, selRange.from, selRange.to, selText, replacement)
        : await applyParaReplacement(pid, chapter, selPara, selText, replacement);
      if (updated === null) {
        setErr("这段的原文已对不上(正文可能已被别处修改),请取消后重新选择");
        return;
      }
      onSaved(updated);
      // 桌面多窗口:广播给对照阅读窗;浏览器 no-op
      void emitChapterSaved(pid, chapter.chapter_number);
      clearSelection();
      if (askSync) onSyncAsk(chapter.chapter_number);
    } catch (e) {
      setErr(errMsg(e));
    } finally { setApplying(false); }
  }

  async function saveEdit() {
    const v = editText.trim();
    if (!v || v === selText) { setMode("pick"); return; } // 没改=收起编辑框
    await applyReplacement(v, true);
  }

  // 章尾续写:请求 AI 顺着本章正文续一段(灰字 ghost)。发起时快照正文,接受时守卫。
  async function fetchGhost() {
    if (ghosting || applying || !text.trim()) return;
    setGhosting(true); setGhostErr(""); setGhost(null);
    ghostBaseRef.current = text;
    try {
      const r = await api.continueChapter(pid, chapter.chapter_number);
      setGhost(r.continuation);
    } catch (e) {
      setGhostErr(errMsg(e));
    } finally { setGhosting(false); }
  }
  // 接受 ghost:作为新段落追加到章末并落库(守卫:正文自发起后未被别处改动)
  async function acceptGhost() {
    if (ghost === null || applying) return;
    setApplying(true); setGhostErr("");
    try {
      const updated = await appendParagraph(pid, chapter, ghost, ghostBaseRef.current);
      if (updated === null) {
        setGhostErr("正文在续写期间变化了,请重新续写"); return;
      }
      onSaved(updated);
      void emitChapterSaved(pid, chapter.chapter_number);
      setGhost(null); // 追加完成:收起 ghost,可再点「续写一段」接着写
    } catch (e) {
      setGhostErr(errMsg(e));
    } finally { setApplying(false); }
  }
  function dismissGhost() { setGhost(null); setGhostErr(""); }
  // ghost 出现即把焦点移到「接受」,让 Tab/Esc 落在章尾区域
  useEffect(() => { if (ghost !== null) ghostAcceptRef.current?.focus(); }, [ghost]);

  const busy = polishing || applying;
  const chips = proseActions.length
    ? proseActions.map((a) => ({ label: a.label, value: a.directive }))
    : DIRECTION_CHIPS.map((c) => ({ label: c, value: c }));

  return (
    <div className="prose-wrap" ref={wrapRef}
      onClick={(e) => {
        // 点正文空白处取消选择(点段落本身已 stopPropagation;气泡内点击不冒泡到这里)
        if (e.target === proseRef.current && mode === "pick" && !busy) clearSelection();
      }}>
      <div className="prose" ref={proseRef} onMouseUp={openSelection}>
        <Paragraphs
          text={text}
          selectedIdx={selPara}
          onSelect={selectPara}
          markedIdx={markedIdx}
          staleIdx={staleIdx}
          renderText={linkify}
        />
      </div>

      {/* 章尾续写(ghost text):AI 顺着本章往下写一段,Tab 接受追加。
          段落选中/编辑时隐藏,不与气泡抢注意力;正文为空时不显示。 */}
      {selPara === null && text.trim().length > 0 && (
        <div className="prose-tail"
          onKeyDown={(e) => {
            if (ghost === null || applying) return;
            if (e.key === "Tab") { e.preventDefault(); void acceptGhost(); }
            else if (e.key === "Escape") { e.preventDefault(); dismissGhost(); }
          }}>
          {ghost === null ? (
            <button className="btn-sm prose-continue" disabled={ghosting || genBlocked}
              title={genBlocked ? genHint : "AI 顺着本章往下续写一段(接受后作为新段落追加)"}
              onClick={fetchGhost}>
              {ghosting ? <><span className="spin spin-sm" />AI 续写中…</> : "✨ 续写一段"}
            </button>
          ) : (
            <div className="prose-ghost-wrap">
              <div className="prose-ghost">{ghost}</div>
              <div className="rp-actions">
                <button className="primary btn-sm" ref={ghostAcceptRef} disabled={applying}
                  onClick={acceptGhost}>
                  {applying && <span className="spin spin-sm" />}接受追加(Tab)
                </button>
                <button className="btn-sm" disabled={applying} onClick={fetchGhost}>换一段</button>
                <button className="btn-sm" disabled={applying} onClick={dismissGhost}>放弃(Esc)</button>
              </div>
            </div>
          )}
          {ghostErr && <div className="msg-err rp-err">{ghostErr}</div>}
        </div>
      )}

      {selPara !== null && selText !== null && anchorTop !== null && (
        <div className={"prose-bubble" + (mode === "edit" ? " prose-bubble-edit" : "")}
          style={{ top: anchorTop }}
          onClick={(e) => e.stopPropagation()}>
          {mode === "pick" && (
            <div className="prose-bubble-actions">
              <button className="btn-sm primary" disabled={genBlocked} ref={firstActionRef}
                title={genBlocked ? genHint : "说一句就改:润色/描写/扩写/找点子,都不动情节"}
                onClick={() => { selectTool("polish"); setMode("polish"); }}>
                💬 改这段
              </button>
              <button className="btn-sm" disabled={genBlocked}
                title={genBlocked ? genHint : "就地修改这一段;保存后可选同步一致性引擎"}
                onClick={() => { setEditText(selText); setMode("edit"); }}>
                ✍️ 手改
              </button>
              {onAnnotate && (
                <button className="btn-sm"
                  title="记下一条意见,攒够后在 AI 栏「按批注改」一次性成批修改"
                  onClick={() => { setNote(""); setMode("annotate"); }}>
                  🖍 批注
                </button>
              )}
              <button className="btn-sm" onClick={clearSelection}>取消选择</button>
            </div>
          )}

          {mode === "polish" && (
            <>
              {/* 工具行:润色/描写/扩写/脑暴(选区与整段都可用;切换即清上个工具的结果) */}
              <div className="chips rp-tools">
                {(["polish", "describe", "expand", "brainstorm"] as CraftTool[]).map((t) => (
                  <button key={t} type="button" disabled={busy}
                    className={"chip" + (tool === t ? " on" : "")}
                    title={TOOL_HINT[t]}
                    onClick={() => selectTool(t)}>
                    {TOOL_LABEL[t]}
                  </button>
                ))}
              </div>

              {/* 润色:方向输入 + 方向 chips */}
              {tool === "polish" && polished === null && (
                <>
                  <div className="rp-label">
                    {selRange ? `选中 ${(targetText ?? "").length} 字` : `第 ${selPara + 1} 段`} · 润色方向(只改文笔,不动情节)
                  </div>
                  <input
                    type="text"
                    value={direction}
                    placeholder="如:更紧张一些 / 去掉 AI 腔"
                    autoFocus
                    onChange={(e) => setDirection(e.target.value)}
                  />
                  <div className="chips rp-chips">
                    {chips.map((c) => (
                      <button key={c.label} type="button"
                        className={"chip" + (direction === c.value ? " on" : "")}
                        onClick={() => setDirection(c.value)}>
                        {c.label}
                      </button>
                    ))}
                  </div>
                  <div className="rp-actions">
                    <button className="primary btn-sm" disabled={polishing} onClick={doPolish}>
                      {polishing && <span className="spin spin-sm" />}开始润色
                    </button>
                    <button className="btn-sm" disabled={polishing}
                      onClick={selRange ? clearSelection : backToPick}>{selRange ? "取消" : "返回"}</button>
                  </div>
                </>
              )}

              {/* 描写 / 扩写:可选补充要求 → 生成改写稿(与润色共用下方 diff 接受屏) */}
              {(tool === "describe" || tool === "expand") && polished === null && (
                <>
                  <div className="rp-label">
                    {selRange ? `选中 ${(targetText ?? "").length} 字` : `第 ${selPara + 1} 段`} · {TOOL_LABEL[tool]}({TOOL_HINT[tool]})
                  </div>
                  <input
                    type="text"
                    value={direction}
                    placeholder="可选:补充要求,如 多写环境声音 / 侧重心理"
                    autoFocus
                    onChange={(e) => setDirection(e.target.value)}
                  />
                  <div className="rp-actions">
                    <button className="primary btn-sm" disabled={polishing} onClick={doCraft}>
                      {polishing && <span className="spin spin-sm" />}生成{TOOL_LABEL[tool]}
                    </button>
                    <button className="btn-sm" disabled={polishing}
                      onClick={selRange ? clearSelection : backToPick}>{selRange ? "取消" : "返回"}</button>
                  </div>
                </>
              )}

              {/* 脑暴:可选方向 → 想点子(下方展示点子列表,不改正文) */}
              {tool === "brainstorm" && ideas === null && (
                <>
                  <div className="rp-label">
                    {selRange ? "就选中文字" : `就第 ${selPara + 1} 段`} · 头脑风暴(给几条点子,不改正文)
                  </div>
                  <input
                    type="text"
                    value={direction}
                    placeholder="可选:想让 AI 往哪个方向想"
                    autoFocus
                    onChange={(e) => setDirection(e.target.value)}
                  />
                  <div className="rp-actions">
                    <button className="primary btn-sm" disabled={polishing} onClick={doCraft}>
                      {polishing && <span className="spin spin-sm" />}想点子
                    </button>
                    <button className="btn-sm" disabled={polishing}
                      onClick={selRange ? clearSelection : backToPick}>{selRange ? "取消" : "返回"}</button>
                  </div>
                </>
              )}

              {/* 改写结果:润色/描写/扩写共用的字符级 diff 接受屏(红删绿增) */}
              {tool !== "brainstorm" && polished !== null && (
                <>
                  <div className="rp-label">
                    {selRange ? "选中文字" : `第 ${selPara + 1} 段`} · {TOOL_LABEL[tool]}改动(红=删,绿=增)
                  </div>
                  <div className="rp-text diff-card">
                    <DiffText ops={diffChars(targetText ?? selText, polished)} />
                  </div>
                  <div className="rp-actions">
                    <button className="primary btn-sm" disabled={applying}
                      onClick={() => applyReplacement(polished, false)}>
                      {applying && <span className="spin spin-sm" />}
                      {applying ? "替换中…" : "接受替换"}
                    </button>
                    <button className="btn-sm" disabled={applying}
                      onClick={() => { setPolished(null); setErr(""); }}>
                      {tool === "polish" ? "再改方向" : "重新生成"}
                    </button>
                    <button className="btn-sm" disabled={applying}
                      onClick={clearSelection}>放弃</button>
                  </div>
                </>
              )}

              {/* 脑暴结果:点子列表(仅参考,不写回) */}
              {tool === "brainstorm" && ideas !== null && (
                <>
                  <div className="rp-label">
                    {selRange ? "就选中文字" : `就第 ${selPara + 1} 段`} · AI 的点子(仅供参考,不改正文)
                  </div>
                  {ideas.length ? (
                    <ul className="rp-ideas">
                      {ideas.map((idea, k) => <li key={k}>{idea}</li>)}
                    </ul>
                  ) : (
                    <div className="rp-label">没想出点子,换个方向再试试。</div>
                  )}
                  <div className="rp-actions">
                    <button className="btn-sm" onClick={() => { setIdeas(null); setErr(""); }}>再想想</button>
                    <button className="btn-sm" onClick={clearSelection}>关闭</button>
                  </div>
                </>
              )}
            </>
          )}

          {mode === "annotate" && onAnnotate && (
            <>
              <div className="rp-label">
                批注第 {selPara + 1} 段(只记意见,不马上改;攒够后在 AI 栏「按批注改」)
              </div>
              <input
                type="text"
                value={note}
                placeholder="如:这里节奏太快 / 人物反应不合理"
                autoFocus
                onChange={(e) => setNote(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && note.trim()) {
                    onAnnotate(selPara, selText, note.trim());
                    clearSelection();
                  }
                }}
              />
              <div className="rp-actions">
                <button className="primary btn-sm" disabled={!note.trim()}
                  onClick={() => { onAnnotate(selPara, selText, note.trim()); clearSelection(); }}>
                  记下批注
                </button>
                <button className="btn-sm" onClick={backToPick}>返回</button>
              </div>
            </>
          )}

          {mode === "edit" && (
            <>
              <div className="rp-label">手改第 {selPara + 1} 段(只动这一段;保存后可选同步一致性引擎)</div>
              <textarea
                className="prose-edit-area"
                rows={Math.min(12, Math.max(3, Math.ceil(editText.length / 40)))}
                value={editText}
                autoFocus
                onChange={(e) => setEditText(e.target.value)}
              />
              <div className="rp-actions">
                <button className="primary btn-sm"
                  disabled={applying || !editText.trim() || editText === selText}
                  onClick={saveEdit}>
                  {applying && <span className="spin spin-sm" />}
                  {applying ? "保存中…" : "保存"}
                </button>
                <button className="btn-sm" disabled={applying}
                  onClick={backToPick}>取消</button>
              </div>
            </>
          )}

          {err && <div className="msg-err rp-err">{err}</div>}
        </div>
      )}

      {/* 实体链接 hover 卡:贴高亮词下沿浮出;移进卡片可选词/翻看,离开延迟隐藏 */}
      {entityPop && (
        <div className="entity-pop" style={{ top: entityPop.top, left: entityPop.left }}
          onMouseEnter={cancelHideEntity} onMouseLeave={scheduleHideEntity}
          onClick={(e) => e.stopPropagation()}>
          <EntityCard c={entityPop.entity} />
        </div>
      )}
    </div>
  );
}
