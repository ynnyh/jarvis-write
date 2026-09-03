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
//     批注已落库持久(跨章不丢,useWritePanel 持有全书 marks,这里只看本章)。
//   全书批修:攒下的标记跨章生效——写一句总描述(如「所有铁锈玫瑰的描写全换掉」),
//     marks-revise-async 逐标记锁情节改写,结果按章出验收卡(MarksReviseCards),
//     逐条 diff 验收、接受即销账;失效标记自动跳过。
// 桌面:展开=右侧 320px sticky 窄栏;收起=40px 细条(有新回复显示圆点)。
// 移动:收起=底部固定输入条(有选中段带「第 N 段」chip);展开=全屏 sheet。
// 对话 UI 复用全局 rd-*/arch-directive 样式族(与 Reader 段落对话/架构研讨同构)。
import { useEffect, useRef, useState } from "react";
import { api, ChapterDetail, ChapterMark, MarksReviseResult, PolishResult, RevisePair } from "../../api";
import { splitParas } from "../../components/Reader";
import { emitChapterSaved } from "../../desktop";
import { useBreakpoint } from "../../hooks/useBreakpoint";
import { errMsg } from "../../pollJob";
import { useJob } from "../../ui/useJob";
import { applyParaReplacement } from "./paraEdit";

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
  // ②档:全书待处理标记(落库持久,父级持有;这里展示本章的、并统计全书的)
  marks: ChapterMark[];
  // 移除一条本章标记(用户点 × 或点已失效项;idx=本章标记数组下标)
  onRemoveAnnotation: (listIdx: number) => void;
  // 「按批注改」job 完成:成批定点润色结果交父级渲染 AnnotatedReviseCard 逐段验收
  onReviseResult: (pairs: RevisePair[]) => void;
  // 「全书批修」job 完成:跨章待验收替换对交父级按章渲染验收卡(MarksReviseCards)
  onMarksReviseResult: (result: MarksReviseResult) => void;
}

export default function AiDock({
  pid, chapterNum, current, selectedPara, genBlocked, genHint,
  collapsed, onCollapsedChange, prefill, onSaved, onRegenerate, onPolishResult,
  marks, onRemoveAnnotation, onReviseResult, onMarksReviseResult,
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
  // 全书批修:总描述输入 + 进行中的阶段文案(异步 job)
  const [marksDirective, setMarksDirective] = useState("");
  const [marksStage, setMarksStage] = useState("");
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
    const snapshot = msgs; // 失败回滚基线(发送前的完整历史)
    // 立即推入用户气泡 + 空的 AI 气泡(占位);token 逐字填进这个空气泡 = 真打字机
    const next = [...snapshot, { role: "user" as const, content: text }];
    setMsgs([...next, { role: "assistant" as const, content: "" }]);
    setInput("");
    setBusy(true); setErr("");
    // 逐字把 delta 追加到最后一个(AI)气泡;防御:末条不是 assistant 就不动
    const onToken = (delta: string) =>
      setMsgs((m) => {
        const last = m[m.length - 1];
        if (!last || last.role !== "assistant") return m;
        const copy = m.slice();
        copy[copy.length - 1] = { ...last, content: last.content + delta };
        return copy;
      });
    // 用权威 reply 收敛最后一个气泡(消除分帧/尾缓冲的细微差异)
    const settle = (reply: string) =>
      setMsgs((m) => {
        const last = m[m.length - 1];
        if (!last || last.role !== "assistant") return m;
        const copy = m.slice();
        copy[copy.length - 1] = { role: "assistant", content: reply };
        return copy;
      });
    try {
      if (mode === "chat") {
        const r = await api.discussFragmentStream(pid, chapterNum, next, selectedPara?.text ?? "", onToken);
        settle(r.reply);
        // 有选中段且 AI 给了改写 → 浮「采用此改写」块;整章问答(只答不改)不会有 suggestion
        setSuggestion(r.suggestion && selectedPara
          ? { text: r.suggestion, paraIdx: selectedPara.idx, expected: selectedPara.text }
          : null);
      } else {
        const r = await api.discussRevisionStream(pid, chapterNum, next, onToken);
        settle(r.reply);
        if (r.directive) { setDirective(r.directive); setSuggestedLevel(r.suggested_level); }
      }
      if (collapsedRef.current) setHasNew(true);
    } catch (e) {
      // 失败:回滚到发送前(去掉用户气泡 + 空 AI 气泡),回填输入框方便重发
      setMsgs(snapshot);
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

  // ②档「按批注改」:把本章未失效标记成批发去 revise-annotated-async(逐条定点润色),
  // 结果 pairs 交父级 AnnotatedReviseCard 逐段验收(接受走 paraEdit 快照守卫写回)。
  async function runRevise() {
    const fresh = chapterMarks.filter((m) => !chapterStaleIdx.has(m.para_idx));
    if (!fresh.length || reviseStage) return;
    setReviseStage("排队中"); setErr("");
    try {
      const r = await runJob<{ pairs: RevisePair[] }>(
        () => api.reviseAnnotatedAsync(pid, chapterNum,
          fresh.map((m) => ({ para_idx: m.para_idx, original: m.snapshot, note: m.note }))),
        { kind: `revise-annotated-${pid}-${chapterNum}`, onStage: (s) => setReviseStage(s) });
      if (r) onReviseResult(r.pairs);
    } catch (e) {
      setErr(errMsg(e));
    } finally { setReviseStage(""); }
  }

  // 全书批修:一句总描述驱动全书 open 标记成批改写(跨章),结果按章出验收卡。
  // 标记在验收接受后才销账;失效标记后端自动跳过并计入 stale。
  async function runMarksRevise() {
    if (marksDirective.trim().length < 2 || !bookMarkCount || marksStage) return;
    setMarksStage("排队中"); setErr("");
    try {
      const r = await runJob<MarksReviseResult>(
        () => api.marksReviseAsync(pid, marksDirective.trim()),
        { kind: `marks-revise-${pid}`, onStage: (s) => setMarksStage(s) });
      if (r) {
        setMarksDirective("");
        onMarksReviseResult(r);
      }
    } catch (e) {
      setErr(errMsg(e));
    } finally { setMarksStage(""); }
  }

  // ②档:本章标记视图 + 失效判定(段号处文本与快照对不上=失效),与全书统计分开算。
  const paras = splitParas(current.final_content || current.draft_content);
  const chapterMarks = marks.filter((m) => m.chapter_number === chapterNum);
  const chapterStaleIdx = new Set<number>();
  for (const m of chapterMarks) {
    if (paras[m.para_idx] !== m.snapshot) chapterStaleIdx.add(m.para_idx);
  }
  const freshCount = chapterMarks.filter((m) => !chapterStaleIdx.has(m.para_idx)).length;
  // 全书统计:批修按钮的底气(N 章 M 处);本章标记是否失效不影响全书批修
  // (失效条目后端会跳过并在结果里说明)。
  const bookChapterCount = new Set(marks.map((m) => m.chapter_number)).size;
  const bookMarkCount = marks.length;

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

      {marks.length > 0 && (
        <div className="ai-dock-annos">
          <div className="rp-label">
            {chapterMarks.length > 0
              ? <>待处理批注 本章{chapterMarks.length} 条 · 全书共 {bookChapterCount} 章 {bookMarkCount} 处</>
              : <>本章暂无批注 · 全书还有 {bookChapterCount} 章 {bookMarkCount} 处标记</>}
          </div>
          {chapterMarks.length > 0 && (
            <ul className="anno-list">
              {chapterMarks.map((m, i) => {
                const stale = chapterStaleIdx.has(m.para_idx);
                return (
                  <li key={m.id} className={"anno-item" + (stale ? " stale" : "")}>
                    <span className="anno-seg">第 {m.para_idx + 1} 段</span>
                    <span className="anno-note">{m.note}</span>
                    {stale && <span className="anno-stale-tag" title="正文已变动,原文对不上,不参与成批">失效</span>}
                    <button className="anno-del" title="移除这条批注"
                      onClick={() => onRemoveAnnotation(i)}>×</button>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="rp-actions">
            <button className="primary btn-sm"
              disabled={!freshCount || !!reviseStage || genBlocked}
              title={genBlocked ? genHint : "把本章未失效的批注一次性成批定点修改,逐段验收"}
              onClick={runRevise}>
              {reviseStage && <span className="spin spin-sm" />}
              按批注改{freshCount ? `(${freshCount})` : ""}
            </button>
            {chapterMarks.length > freshCount && (
              <span className="hint">失效 {chapterMarks.length - freshCount} 条不参与</span>
            )}
          </div>
          {reviseStage && (
            <div className="muted ai-dock-level">
              成批修改中({reviseStage}),可切到别处,进度看右上角任务
            </div>
          )}

          {/* 全书批修:一句总描述统一指挥所有标记(跨章);验收卡按章出,接受即销账 */}
          <div className="rp-label mt-2">全书批修(跨章):一句话说明要把这些标记处改成什么样</div>
          <textarea
            rows={2}
            value={marksDirective}
            placeholder="如:所有铁锈玫瑰的描写全部换成全新意象;扎胸膛的自残动作一律删掉"
            onChange={(e) => setMarksDirective(e.target.value)}
          />
          <div className="rp-actions">
            <button className="primary btn-sm"
              disabled={marksDirective.trim().length < 2 || !bookMarkCount || !!marksStage}
              title={`对全书 ${bookChapterCount} 章 ${bookMarkCount} 处标记做锁情节定点改写,逐条 diff 验收后写回`}
              onClick={runMarksRevise}>
              {marksStage && <span className="spin spin-sm" />}
              全书批修({bookChapterCount} 章 {bookMarkCount} 处)
            </button>
          </div>
          {marksStage && (
            <div className="muted ai-dock-level">
              全书批修中({marksStage}),可切到别处,进度看右上角任务
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
        {msgs.map((m, i) => {
          // 空的 AI 气泡 + 仍在忙 = 首个 token 还没到,原地转圈(打字机占位)
          const waiting = m.role === "assistant" && !m.content && busy;
          return (
            <div key={i} className={"rd-msg rd-" + m.role}>
              <div className={"rd-bubble" + (waiting ? " muted" : "")}>
                {waiting ? <><span className="spin spin-sm" />编辑正在写…</> : m.content}
              </div>
            </div>
          );
        })}
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
          {marks.length > 0 && <span className="chip">批注 {marks.length}</span>}
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
        💬{marks.length > 0 && <span className="ai-dock-mini-badge">{marks.length}</span>}
        {hasNew && <span className="ai-dot" />}
      </button>
    );
  }
  return <aside className="ai-dock">{body}</aside>;
}
