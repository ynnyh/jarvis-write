// 架构工作区:雪花四步产出,四块均可手动编辑,也可整体重生成
// 对生成的架构不满意时,开「架构研讨」和 AI 聊清楚想法 → 蒸馏成额外要求 → 按此重新生成
import { useEffect, useRef, useState } from "react";
import { api, Architecture, Project, Tendency } from "../api";
import { pollJob } from "../pollJob";
import TendencySelector from "../components/TendencySelector";
import { confirmDialog } from "../ui/ConfirmDialog";

interface Props { project: Project; arch: Architecture | null; onChanged: () => Promise<void>; }

const BLOCKS: { key: keyof Architecture; label: string; hint: string }[] = [
  { key: "core_seed", label: "核心种子", hint: "一句话故事本质:显性冲突 + 潜在危机" },
  { key: "character_dynamics", label: "角色动力学", hint: "每个角色的创伤/追求/渴望/面具/阴影/蜕变" },
  { key: "world_building", label: "世界观", hint: "物理/社会/隐喻三维度" },
  { key: "plot_architecture", label: "情节架构", hint: "三幕式 + 主要伏笔 + 贯穿悬念" },
];

export default function ArchPanel({ project, arch, onChanged }: Props) {
  const [form, setForm] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [tendency, setTendency] = useState<Tendency>({});
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  // 组件卸载时中止轮询,防止卸载后继续 setState
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  // ---- 架构研讨对话状态(聊清不满意在哪 → 蒸馏出额外要求 → 按此重新生成) ----
  const [discussOpen, setDiscussOpen] = useState(false);
  const [discussMsgs, setDiscussMsgs] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [discussInput, setDiscussInput] = useState("");
  const [discussing, setDiscussing] = useState(false);
  const [discussErr, setDiscussErr] = useState("");
  const [directive, setDirective] = useState(""); // AI 蒸馏出的额外要求
  const discussLogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (arch) {
      setForm({
        core_seed: arch.core_seed, character_dynamics: arch.character_dynamics,
        world_building: arch.world_building, plot_architecture: arch.plot_architecture,
      });
      setDirty(false);
    }
  }, [arch]);

  // 对话流自动滚到底
  useEffect(() => {
    if (discussOpen) discussLogRef.current?.scrollTo(0, discussLogRef.current.scrollHeight);
  }, [discussMsgs, discussing, discussOpen]);

  async function regenerate(withDirective = "") {
    // 覆盖现有架构是重操作:已有架构时二次确认(未保存的手改也会被覆盖)
    if (arch) {
      const ok = await confirmDialog({
        title: withDirective ? "按研讨要求重新生成架构?" : "重新生成整个架构?",
        body: "现有四块内容(含未保存的手动修改)将被 AI 新生成的结果覆盖。已生成的大纲/正文不受影响,但可能与新架构失配。",
        confirmText: "重新生成",
        danger: true,
      });
      if (!ok) return;
    }
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy("架构生成:排队中…"); setErr(""); setMsg("");
    try {
      const { job_id } = await api.generateArchitectureAsync(project.id, tendency, withDirective);
      // 轮询任务进度(雪花四步:种子→角色→世界观→情节)
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setBusy(`架构生成中:${stage}`),
      });
      if (ctrl.signal.aborted) return;
      await onChanged();
      setMsg(withDirective
        ? "已按研讨要求重新生成架构。还不满意可以继续聊、再生成。"
        : "架构已生成。下一步:去「大纲」生成章节蓝图。");
      if (withDirective) setDiscussOpen(false); // 采纳后收起研讨面板
    } catch (e) {
      if (!ctrl.signal.aborted) setErr(String(e));
    } finally { if (!ctrl.signal.aborted) setBusy(""); }
  }

  // 架构研讨:发一句 → AI 顺着聊 + 后台蒸馏出「额外要求」(directive)
  async function sendDiscuss() {
    const text = discussInput.trim();
    if (!text || discussing) return;
    const next = [...discussMsgs, { role: "user" as const, content: text }];
    setDiscussMsgs(next);
    setDiscussInput("");
    setDiscussing(true); setDiscussErr("");
    try {
      const r = await api.discussArchitecture(project.id, next);
      setDiscussMsgs((m) => [...m, { role: "assistant", content: r.reply }]);
      setDirective(r.directive || "");
    } catch (e) {
      // 失败回退刚发出的那条,方便重发
      setDiscussMsgs((m) => m.slice(0, -1));
      setDiscussInput(text);
      setDiscussErr(String(e));
    } finally { setDiscussing(false); }
  }

  async function save() {
    setBusy("保存修改…"); setErr(""); setMsg("");
    try {
      await api.patchArchitecture(project.id, form);
      await onChanged();
      setMsg("架构修改已保存(版本+1)。注意:已生成的大纲不会自动变,大幅改动后建议重新生成蓝图。");
      setDirty(false);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2 className="grow">
            顶层架构 {arch && <span className="badge">v{arch.version}</span>}
          </h2>
          {arch && dirty && (
            <button className="primary" disabled={!!busy} onClick={save}>
              {busy && <span className="spin" />}保存手动修改
            </button>
          )}
        </div>
        <div className="card-desc mt-2">
          {arch
            ? "四块内容都可以直接改——这是你的书,AI 只是初稿。改完记得保存。"
            : "还没有架构。选好倾向后点「生成架构」,AI 按雪花写作法四步产出。"}
        </div>
        {!arch && (
          <div className="mt-3">
            <TendencySelector node="outline" value={tendency} onChange={setTendency} compact />
            <button className="primary mt-2" disabled={!!busy} onClick={() => regenerate()}>
              {busy && <span className="spin" />}生成架构
            </button>
          </div>
        )}
        {busy && <div className="muted mt-2">{busy}</div>}
        {msg && <div className="msg-ok mt-2">{msg}</div>}
        {err && <div className="msg-err mt-2">{err}</div>}
      </div>

      {arch && (
        <div className="card">
          <div className="card-head">
            <h3 className="grow">对架构不满意?和 AI 聊聊</h3>
            <button className="btn-sm" onClick={() => setDiscussOpen((v) => !v)}>
              {discussOpen ? "收起研讨" : "开始研讨"}
            </button>
          </div>
          <div className="card-desc">
            反复重生成还是不对味,多半是你脑子里的想法没传进去。把哪里不满意、想要什么聊清楚,AI 会整理成明确要求,再按此重新生成——比盲目重来靠谱。
          </div>
          {discussOpen && (
            <div className="arch-discuss mt-3">
              <div className="rd-log" ref={discussLogRef}>
                {discussMsgs.length === 0 && !discussing && (
                  <div className="muted rd-empty">
                    试试:「主角太正派了,我想要个有道德瑕疵的反英雄」「结局别大团圆」「世界观再硬核一点」
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
              {directive && (
                <div className="arch-directive">
                  <div className="rp-label">AI 整理出的调整要求(重新生成时会高优先级遵循)</div>
                  <textarea rows={Math.min(10, Math.max(3, directive.split("\n").length + 1))}
                    value={directive} onChange={(e) => setDirective(e.target.value)} />
                  <div className="rp-actions">
                    <button className="primary" disabled={!!busy} onClick={() => regenerate(directive)}>
                      {busy && <span className="spin" />}按这些要求重新生成架构
                    </button>
                    <button disabled={!!busy} onClick={() => setDirective("")}>清空,继续聊</button>
                  </div>
                </div>
              )}
              <div className="rd-input">
                <textarea
                  rows={2}
                  value={discussInput}
                  placeholder="说说哪里不满意、你想要什么…(Enter 发送,Shift+Enter 换行)"
                  disabled={discussing}
                  onChange={(e) => setDiscussInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendDiscuss(); }
                  }}
                />
                <div className="rp-actions">
                  <button className="primary" disabled={discussing || !discussInput.trim()} onClick={sendDiscuss}>
                    {discussing && <span className="spin" />}发送
                  </button>
                </div>
              </div>
              {discussErr && <div className="msg-err mt-2">{discussErr}</div>}
            </div>
          )}
        </div>
      )}

      {arch && BLOCKS.map((b) => (
        <div key={b.key} className="card arch-block">
          <h3>{b.label} <span className="hint">· {b.hint}</span></h3>
          <textarea
            rows={Math.min(14, Math.max(4, (form[b.key] ?? "").split("\n").length + 1))}
            value={form[b.key] ?? ""}
            onChange={(e) => { setForm({ ...form, [b.key]: e.target.value }); setDirty(true); }}
          />
        </div>
      ))}

      {arch && (
        <div className="card">
          <h3>整体重生成</h3>
          <div className="card-desc">
            对现有架构整体不满意时使用,会覆盖以上四块(版本+1,可在数据库回溯)。若有具体想法,建议先用上方「架构研讨」聊清楚再生成。
          </div>
          <TendencySelector node="outline" value={tendency} onChange={setTendency} compact />
          <button className="danger mt-2" disabled={!!busy} onClick={() => regenerate()}>
            {busy && <span className="spin" />}重新生成整个架构
          </button>
        </div>
      )}
    </>
  );
}
