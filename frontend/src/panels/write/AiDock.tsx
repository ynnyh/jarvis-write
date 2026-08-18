// AI 窄栏(「正文即界面」P2/P3,docs/10 §4):write 区右侧常驻 AI 对话通道,两个模式 + ②档批注区——
//   随便聊(discuss):正文选中段时聊该段(target=段原文,可给改写建议→「采用此改写」
//     走 paraEdit 共享写回链路);无选中段=整章自由问答(后端 DISCUSS_CHAPTER,只答不改)。
//   这章整体不满意(revise-discuss):多轮对话蒸馏出修改意见 → 意见清单卡(可编辑)
//     → ③ 整章优化(锁情节只改文字,异步 job,结果交 PolishCompareCard 对照)
//     → ④ 整章重生成(情节也重来,交父级 generate 链路,旧版自动存快照)。
//     后端蒸馏为 JSON 契约时会带 suggested_level,清单卡上据此提示「AI 建议走③/④」。
//   ②档 待处理批注:正文里 🖍 攒下的多条意见在此列出,「按批注改」→ revise-annotated-async
//     成批定点润色(逐条 polish_fragment),结果交父级 AnnotatedReviseCard 逐段 diff 验收。
//     正文变动后快照对不上的批注标「失效」,不参与成批(段号已错位,写回守卫也会拦)。
// 桌面:展开=右侧 320px sticky 窄栏;收起=40px 细条(有新回复显示圆点)。
// 移动:收起=底部固定输入条(有选中段带「第 N 段」chip);展开=全屏 sheet。
// 对话 UI 复用全局 rd-*/arch-directive 样式族(与 Reader 段落对话/架构研讨同构)。
import { useEffect, useRef, useState } from "react";
import { api, ChapterDetail, PolishResult, RevisePair } from "../../api";
import { splitParas } from "../../components/Reader";
import { emitChapterSaved } from "../../desktop";
import { useBreakpoint } from "../../hooks/useBreakpoint";
import { errMsg } from "../../pollJob";
import { useJob } from "../../ui/useJob";
import { applyParaReplacement, Annotation } from "./paraEdit";

export type DockMode = "chat" | "revise";

/** 外部预填(命令面板/评分卡/状态卡等入口):nonce 变化触发写入输入框并展开 */
export interface DockPrefill { text: string; mode?: DockMode; nonce: number }

interface Msg { role: "user" | "assistant"; content: string }

interface Props {
  pid: number;
  chapterNum: number;
  current: ChapterDetail;
  // 正文选中段(Prose 上报):chat 模式的 discuss target 与「第 N 段」引用
  selectedPara: { idx: number; text: string } | null;
  genBlocked: boolean;
  genHint: string;
  collapsed: boolean;
  onCollapsedChange: (v: boolean) => void;
  prefill: DockPrefill | null;
  // 「采用此改写」写回成功:父级更新 qk.chapter 缓存并刷新章节列表
  onSaved: (updated: ChapterDetail) => void;
  // 意见清单 ④:带着修改意见走父级 generate 链路(重写)
  onRegenerate: (revision: string) => void;
  // 意见清单 ③:整章优化完成,父级在正文区顶部渲染 PolishCompareCard
  onPolishResult: (original: string, result: PolishResult) => void;
  // ②档:正文里攒下的待处理批注(父级持有;Prose 记入、这里成批发)
  annotations: Annotation[];
  // 移除一条批注(用户点 × 或点已失效项;idx=annotations 数组下标)
  onRemoveAnnotation: (listIdx: number) => void;
  // 「按批注改」job 完成:成批定点润色结果交父级渲染 AnnotatedReviseCard 逐段验收
  onReviseResult: (pairs: RevisePair[]) => void;
}

export default function AiDock({
  pid, chapterNum, current, selectedPara, genBlocked, genHint,
  collapsed, onCollapsedChange, prefill, onSaved, onRegenerate, onPolishResult,
  annotations, onRemoveAnnotation, onReviseResult,
}: Props) {
  const { isMobile } = useBreakpoint();
  const { run: runJob } = useJob();
  const [mode, setMode] = useState<DockMode>("chat");
  // 两个模式各自一份对话历史(切模式清空,见 switchMode)
  const [chatMsgs, setChatMsgs] = useState<Msg[]>([]);
  const [reviseMsgs, setReviseMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  // chat:AI 给出的改写建议(含采纳定位用的段落快照)
  const [suggestion, setSuggestion] = useState<{ text: string; paraIdx: number; expected: string } | null>(null);
  const [applying, setApplying] = useState(false);
  // revise:蒸馏出的修改意见 + AI 建议档位(polish=③ / regenerate=④ / null=没把握)
  const [directive, setDirective] = useState("");
  const [suggestedLevel, setSuggestedLevel] = useState<string | null>(null);
  // ③ 整章优化进行中的阶段文案(异步 job,可切走,任务中心可见)
  const [polishStage, setPolishStage] = useState("");
  // ② 按批注改进行中的阶段文案(异步 job)
  const [reviseStage, setReviseStage] = useState("");
  // 收起期间来了新回复:细条/输入条上显示圆点
  const [hasNew, setHasNew] = useState(false);
  const collapsedRef = useRef(collapsed);
  useEffect(() => { collapsedRef.current = collapsed; }, [collapsed]);
  useEffect(() => { if (!collapsed) setHasNew(false); }, [collapsed]);
  const logRef = useRef<HTMLDivElement>(null);

  // 切章:清空全部跟随旧章的对话/意见/建议状态
  useEffect(() => {
    setChatMsgs([]); setReviseMsgs([]); setInput(""); setErr("");
    setSuggestion(null); setDirective(""); setSuggestedLevel(null); setPolishStage("");
  }, [pid, chapterNum]);

  // 外部预填:写入输入框(+切模式)并展开窄栏
  useEffect(() => {
    if (!prefill) return;
    if (prefill.mode) switchMode(prefill.mode);
    setInput(prefill.text);
    onCollapsedChange(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill?.nonce]);

  // 对话流自动滚到底
  const msgs = mode === "chat" ? chatMsgs : reviseMsgs;
  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [msgs, busy]);

  // 切模式:两个通道各自持有历史与蒸馏产物(chatMsgs/suggestion ↔ reviseMsgs/directive),
  // 只切「显示哪一个」——不再清空,避免 revise 蒸馏到一半误点「随便聊」把意见清单全丢(切回即恢复)。
  // 仅重置随手输入框与错误条(属当前编辑动作的瞬态);整体清空只在切章时(见上方 [pid, chapterNum] effect)。
  function switchMode(m: DockMode) {
    if (m === mode) return;
    setMode(m);
    setInput(""); setErr("");
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    const setMsgs = mode === "chat" ? setChatMsgs : setReviseMsgs;
    const next = [...msgs, { role: "user" as const, content: text }];
    setMsgs(next);
    setInput("");
    setBusy(true); setErr("");
    try {
      if (mode === "chat") {
        const r = await api.discussFragment(pid, chapterNum, next, selectedPara?.text ?? "");
        setMsgs((m) => [...m, { role: "assistant", content: r.reply }]);
        // 有选中段且 AI 给了改写 → 浮「采用此改写」块;整章问答(只答不改)不会有 suggestion
        setSuggestion(r.suggestion && selectedPara
          ? { text: r.suggestion, paraIdx: selectedPara.idx, expected: selectedPara.text }
          : null);
      } else {
        const r = await api.discussRevision(pid, chapterNum, next);
        setMsgs((m) => [...m, { role: "assistant", content: r.reply }]);
        if (r.directive) { setDirective(r.directive); setSuggestedLevel(r.suggested_level); }
      }
      if (collapsedRef.current) setHasNew(true);
    } catch (e) {
      // 失败回退刚发出的那条,方便重发
      setMsgs((m) => m.slice(0, -1));
      setInput(text);
      setErr(errMsg(e));
    } finally { setBusy(false); }
  }

  // 「采用此改写」:与 Prose 气泡同一条 paraEdit 写回链路(段落序号 + 原文快照守卫)
  async function adoptSuggestion() {
    if (!suggestion || applying) return;
    setApplying(true); setErr("");
    try {
      const updated = await applyParaReplacement(
        pid, current, suggestion.paraIdx, suggestion.expected, suggestion.text);
      if (!updated) {
        setErr("原文已与最新章节内容对不上,请重新选中该段后再聊");
        return;
      }
      onSaved(updated);
      // 桌面多窗口:广播给对照阅读窗;浏览器 no-op
      void emitChapterSaved(pid, chapterNum);
      setSuggestion(null);
    } catch (e) {
      setErr(errMsg(e));
    } finally { setApplying(false); }
  }

  // 意见清单 ③:整章优化(异步 job;结果交父级渲染对照卡,默认带「去AI味」同原 PolishPanel)
  async function runPolish() {
    if (!directive.trim() || polishStage) return;
    setPolishStage("排队中"); setErr("");
    try {
      const r = await runJob<PolishResult>(
        () => api.polishChapterAsync(pid, chapterNum, { polish_style: ["去AI味"] }, directive.trim()),
        { kind: `polish-${pid}-${chapterNum}`, onStage: (s) => setPolishStage(s) });
      if (r) onPolishResult(current.final_content || current.draft_content, r);
    } catch (e) {
      setErr(errMsg(e));
    } finally { setPolishStage(""); }
  }

  // ②档「按批注改」:把未失效批注成批发去 revise-annotated-async(逐条定点润色),
  // 结果 pairs 交父级 AnnotatedReviseCard 逐段验收(接受走 paraEdit 快照守卫写回)。
  async function runRevise() {
    const fresh = annotations.filter((a) => !staleIdx.has(a.paraIdx));
    if (!fresh.length || reviseStage) return;
    setReviseStage("排队中"); setErr("");
    try {
      const r = await runJob<{ pairs: RevisePair[] }>(
        () => api.reviseAnnotatedAsync(pid, chapterNum,
          fresh.map((a) => ({ para_idx: a.paraIdx, original: a.snapshot, note: a.note }))),
        { kind: `revise-annotated-${pid}-${chapterNum}`, onStage: (s) => setReviseStage(s) });
      if (r) onReviseResult(r.pairs);
    } catch (e) {
      setErr(errMsg(e));
    } finally { setReviseStage(""); }
  }

  // ②档:当前正文分段,用于判定批注是否失效(段号处文本与快照对不上=失效)
  const paras = splitParas(current.final_content || current.draft_content);
  const staleIdx = new Set<number>();
  for (const a of annotations) {
    if (paras[a.paraIdx] !== a.snapshot) staleIdx.add(a.paraIdx);
  }
  const freshCount = annotations.filter((a) => !staleIdx.has(a.paraIdx)).length;

  const levelHint = suggestedLevel === "polish"
    ? "AI 建议走③ 整章优化(它的判断:只改文字就够了)"
    : suggestedLevel === "regenerate"
      ? "AI 建议走④ 整章重生成(它的判断:情节也得动)"
      : null;

  // 展开态主体:桌面窄栏与移动 sheet 共用同一份
  const body = (
    <>
      <div className="ai-dock-head">
        <div className="chips">
          <button type="button" className={"chip" + (mode === "chat" ? " on" : "")}
            onClick={() => switchMode("chat")}>随便聊</button>
          <button type="button" className={"chip" + (mode === "revise" ? " on" : "")}
            onClick={() => switchMode("revise")}>这章整体不满意</button>
        </div>
        <button className="btn-sm" onClick={() => onCollapsedChange(true)}>收起</button>
      </div>

      {annotations.length > 0 && (
        <div className="ai-dock-annos">
          <div className="rp-label">
            待处理批注 {annotations.length} 条 · 攒够后一次成批改(②)
          </div>
          <ul className="anno-list">
            {annotations.map((a, i) => {
              const stale = staleIdx.has(a.paraIdx);
              return (
                <li key={i} className={"anno-item" + (stale ? " stale" : "")}>
                  <span className="anno-seg">第 {a.paraIdx + 1} 段</span>
                  <span className="anno-note">{a.note}</span>
                  {stale && <span className="anno-stale-tag" title="正文已变动,原文对不上,不参与成批">失效</span>}
                  <button className="anno-del" title="移除这条批注"
                    onClick={() => onRemoveAnnotation(i)}>×</button>
                </li>
              );
            })}
          </ul>
          <div className="rp-actions">
            <button className="primary btn-sm"
              disabled={!freshCount || !!reviseStage || genBlocked}
              title={genBlocked ? genHint : "把未失效的批注一次性成批定点修改,逐段验收"}
              onClick={runRevise}>
              {reviseStage && <span className="spin spin-sm" />}
              按批注改{freshCount ? `(${freshCount})` : ""}
            </button>
            {annotations.length > freshCount && (
              <span className="hint">失效 {annotations.length - freshCount} 条不参与</span>
            )}
          </div>
          {reviseStage && (
            <div className="muted ai-dock-level">
              成批修改中({reviseStage}),可切到别处,进度看右上角任务
            </div>
          )}
        </div>
      )}

      {mode === "chat" && selectedPara && (
        <div className="hint ai-dock-ctx">
          正在聊第 {selectedPara.idx + 1} 段;取消正文选区即转为聊整章
        </div>
      )}

      <div className="rd-log ai-dock-log" ref={logRef}>
        {msgs.length === 0 && !busy && (
          <div className="muted rd-empty">
            {mode === "chat"
              ? (selectedPara
                ? "就这一段随便问:为什么这么写、换个人称行不行、帮我改得更狠一点…"
                : "关于这一章随便问:梗概/人物/节奏哪里不对、某个伏笔收没收…(只答不改)")
              : "说说这章哪里不对,比如:「开头铺垫太长」「主角这里不该哭」「结尾太突然,想留个钩子」— 聊完我整理成修改意见,你选怎么改。"}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={"rd-msg rd-" + m.role}>
            <div className="rd-bubble">{m.content}</div>
          </div>
        ))}
        {busy && (
          <div className="rd-msg rd-assistant">
            <div className="rd-bubble muted"><span className="spin spin-sm" />编辑正在想…</div>
          </div>
        )}
      </div>

      {mode === "chat" && suggestion && (
        <div className="rd-suggestion">
          <div className="rp-label">AI 给出的改写(采用后替换第 {suggestion.paraIdx + 1} 段)</div>
          <div className="rp-text rp-new">{suggestion.text}</div>
          <div className="rp-actions">
            <button className="primary btn-sm" disabled={applying} onClick={adoptSuggestion}>
              {applying && <span className="spin spin-sm" />}
              {applying ? "替换中…" : "采用此改写"}
            </button>
            <button className="btn-sm" disabled={applying}
              onClick={() => setSuggestion(null)}>不用,继续聊</button>
          </div>
        </div>
      )}

      {mode === "revise" && directive && (
        <div className="arch-directive">
          <div className="rp-label">AI 整理出的修改意见(可直接编辑,③④ 都会高优先级遵循)</div>
          <textarea
            rows={Math.min(8, Math.max(3, directive.split("\n").length + 1))}
            value={directive}
            onChange={(e) => setDirective(e.target.value)}
          />
          {levelHint && <div className="hint ai-dock-level">{levelHint}</div>}
          <div className="rp-actions">
            <button className="primary btn-sm"
              disabled={!!polishStage || !directive.trim()}
              title="锁情节、只改文笔,便宜;跑完在正文区出原文/润色稿对照卡"
              onClick={runPolish}>
              {polishStage && <span className="spin spin-sm" />}③ 整章优化
            </button>
            <button className="btn-sm"
              disabled={genBlocked || !!polishStage || !directive.trim()}
              title={genBlocked ? genHint : "情节也重来,最贵;重写前旧版自动存快照,可回退"}
              onClick={() => onRegenerate(directive.trim())}>
              ④ 整章重生成
            </button>
            <button className="btn-sm" disabled={!!polishStage}
              onClick={() => { setDirective(""); setSuggestedLevel(null); }}>清空,继续聊</button>
          </div>
          {polishStage && (
            <div className="muted ai-dock-level">
              整章优化中({polishStage}),约 2-6 分钟;可切到别处,进度看右上角任务
            </div>
          )}
        </div>
      )}

      <div className="rd-input">
        <textarea
          rows={2}
          value={input}
          placeholder={mode === "chat"
            ? "问点什么…(Enter 发送,Shift+Enter 换行)"
            : "说说哪里不满意、想要什么…(Enter 发送,Shift+Enter 换行)"}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); }
          }}
        />
        <div className="rp-actions">
          <button className="primary btn-sm" disabled={busy || !input.trim()}
            onClick={() => void send()}>
            {busy && <span className="spin spin-sm" />}发送
          </button>
        </div>
      </div>
      {err && <div className="msg-err mt-2">{err}</div>}
    </>
  );

  // ---- 移动端:底部输入条 ⇄ 全屏 sheet ----
  if (isMobile) {
    if (collapsed) {
      return (
        <button type="button" className="m-ai-bar" onClick={() => onCollapsedChange(false)}>
          <span className="grow m-ai-bar-label">💬 和 AI 说点什么…</span>
          {annotations.length > 0 && <span className="chip">批注 {annotations.length}</span>}
          {selectedPara && <span className="chip on">第 {selectedPara.idx + 1} 段</span>}
          {hasNew && <span className="ai-dot" />}
        </button>
      );
    }
    return (
      <div className="m-sheet-overlay">
        <div className="m-sheet ai-dock-sheet">{body}</div>
      </div>
    );
  }

  // ---- 桌面:40px 细条 ⇄ 右侧 320px 窄栏 ----
  if (collapsed) {
    return (
      <button type="button" className="ai-dock-mini" title="展开 AI 栏"
        onClick={() => onCollapsedChange(false)}>
        💬{annotations.length > 0 && <span className="ai-dock-mini-badge">{annotations.length}</span>}
        {hasNew && <span className="ai-dot" />}
      </button>
    );
  }
  return <aside className="ai-dock">{body}</aside>;
}
