// 架构工作区:雪花四步产出,四块均可手动编辑,也可整体重生成
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

  useEffect(() => {
    if (arch) {
      setForm({
        core_seed: arch.core_seed, character_dynamics: arch.character_dynamics,
        world_building: arch.world_building, plot_architecture: arch.plot_architecture,
      });
      setDirty(false);
    }
  }, [arch]);

  async function regenerate() {
    // 覆盖现有架构是重操作:已有架构时二次确认(未保存的手改也会被覆盖)
    if (arch) {
      const ok = await confirmDialog({
        title: "重新生成整个架构?",
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
      const { job_id } = await api.generateArchitectureAsync(project.id, tendency);
      // 轮询任务进度(雪花四步:种子→角色→世界观→情节)
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setBusy(`架构生成中:${stage}`),
      });
      if (ctrl.signal.aborted) return;
      await onChanged();
      setMsg("架构已生成。下一步:去「大纲」生成章节蓝图。");
    } catch (e) {
      if (!ctrl.signal.aborted) setErr(String(e));
    } finally { if (!ctrl.signal.aborted) setBusy(""); }
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
            <button className="primary mt-2" disabled={!!busy} onClick={regenerate}>
              {busy && <span className="spin" />}生成架构
            </button>
          </div>
        )}
        {busy && <div className="muted mt-2">{busy}</div>}
        {msg && <div className="msg-ok mt-2">{msg}</div>}
        {err && <div className="msg-err mt-2">{err}</div>}
      </div>

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
            对现有架构整体不满意时使用,会覆盖以上四块(版本+1,可在数据库回溯)。
          </div>
          <TendencySelector node="outline" value={tendency} onChange={setTendency} compact />
          <button className="danger mt-2" disabled={!!busy} onClick={regenerate}>
            {busy && <span className="spin" />}重新生成整个架构
          </button>
        </div>
      )}
    </>
  );
}
