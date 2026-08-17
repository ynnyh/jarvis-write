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
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, ChapterDetail, EditorAction } from "../../api";
import { Paragraphs, splitParas } from "../../components/Reader";
import { emitChapterSaved } from "../../desktop";
import { errMsg } from "../../pollJob";
import { applyParaReplacement, Annotation } from "./paraEdit";
import { diffChars } from "./charDiff";
import DiffText from "./DiffText";

// 润色方向 chips 兜底(editorialActions 拉不到时用,与 Reader 内置一致)
const DIRECTION_CHIPS = ["更生动", "更紧张", "更简洁", "去 AI 味"];

type BubbleMode = "pick" | "polish" | "edit" | "annotate";

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
  const [mode, setMode] = useState<BubbleMode>("pick");
  const [direction, setDirection] = useState("");
  const [polishing, setPolishing] = useState(false);
  const [polished, setPolished] = useState<string | null>(null);
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

  // 编辑部预设优化动作(润色方向 chips;拉不到时退回内置四个)
  const [proseActions, setProseActions] = useState<EditorAction[]>([]);
  useEffect(() => {
    api.editorialActions().then((a) => setProseActions(a.prose)).catch(() => undefined);
  }, []);

  const text = chapter.final_content || chapter.draft_content;
  const paras = splitParas(text);
  const selText = selPara !== null && selPara < paras.length ? paras[selPara] : null;

  // ②档:已批注段落高亮;正文变动后快照对不上的批注标为「失效」(据此提示重记)
  const markedIdx = new Set<number>();
  const staleIdx = new Set<number>();
  for (const a of annotations ?? []) {
    markedIdx.add(a.paraIdx);
    if (paras[a.paraIdx] !== a.snapshot) staleIdx.add(a.paraIdx);
  }

  // 切章:收起气泡/编辑框,清空全部跟随旧章的视图态(脏确认由父级在切章前完成)
  useEffect(() => {
    setSelPara(null); setMode("pick"); setDirection(""); setPolished(null);
    setEditText(""); setNote(""); setErr(""); setAnchorTop(null);
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
  }, [selPara, mode, polished, text]);

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

  function selectPara(i: number) {
    if (applying || polishing) return; // 存盘/润色请求进行中不换目标
    if (mode === "edit" && selText !== null && editText !== selText) return; // 手改未保存:先保存/取消,防误点丢稿
    setSelPara(i); setMode("pick"); setDirection(""); setPolished(null); setNote(""); setErr("");
    onSelectChange?.({ idx: i, text: paras[i] });
  }
  function clearSelection() {
    setSelPara(null); setMode("pick"); setDirection(""); setPolished(null); setNote(""); setErr("");
    onSelectChange?.(null);
  }

  // 改这段:方向(chips 或手输)→ polish-fragment(同步端点,spinner 在按钮上)
  async function doPolish() {
    if (selText === null) return;
    setPolishing(true); setErr("");
    try {
      const r = await api.polishFragment(pid, chapter.chapter_number, selText, direction.trim());
      setPolished(r.polished);
    } catch (e) {
      setErr(errMsg(e));
    } finally { setPolishing(false); }
  }

  // 把选中段替换为 replacement 并落库(AI 润色接受与手改保存共用,链路在 paraEdit)。
  // 只做快速存盘:润色(askSync=false)不动情节不问同步;手改(askSync=true)交父级询问。
  async function applyReplacement(replacement: string, askSync: boolean) {
    if (selPara === null || selText === null) return;
    setApplying(true); setErr("");
    try {
      const updated = await applyParaReplacement(pid, chapter, selPara, selText, replacement);
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
      <div className="prose" ref={proseRef}>
        <Paragraphs
          text={text}
          selectedIdx={selPara}
          onSelect={selectPara}
          markedIdx={markedIdx}
          staleIdx={staleIdx}
        />
      </div>

      {selPara !== null && selText !== null && anchorTop !== null && (
        <div className={"prose-bubble" + (mode === "edit" ? " prose-bubble-edit" : "")}
          style={{ top: anchorTop }}
          onClick={(e) => e.stopPropagation()}>
          {mode === "pick" && (
            <div className="prose-bubble-actions">
              <button className="btn-sm primary" disabled={genBlocked}
                title={genBlocked ? genHint : "说一句就改:只润色这一段,不动情节"}
                onClick={() => setMode("polish")}>
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

          {mode === "polish" && polished === null && (
            <>
              <div className="rp-label">第 {selPara + 1} 段 · 润色方向(只改文笔,不动情节)</div>
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
                  onClick={() => setMode("pick")}>返回</button>
              </div>
            </>
          )}

          {mode === "polish" && polished !== null && (
            <>
              <div className="rp-label">第 {selPara + 1} 段 · 润色改动(红=删,绿=增)</div>
              <div className="rp-text diff-card">
                <DiffText ops={diffChars(selText, polished)} />
              </div>
              <div className="rp-actions">
                <button className="primary btn-sm" disabled={applying}
                  onClick={() => applyReplacement(polished, false)}>
                  {applying && <span className="spin spin-sm" />}
                  {applying ? "替换中…" : "接受替换"}
                </button>
                <button className="btn-sm" disabled={applying}
                  onClick={() => { setPolished(null); setErr(""); }}>再改方向</button>
                <button className="btn-sm" disabled={applying}
                  onClick={clearSelection}>放弃</button>
              </div>
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
                <button className="btn-sm" onClick={() => setMode("pick")}>返回</button>
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
                  onClick={() => setMode("pick")}>取消</button>
              </div>
            </>
          )}

          {err && <div className="msg-err rp-err">{err}</div>}
        </div>
      )}
    </div>
  );
}
