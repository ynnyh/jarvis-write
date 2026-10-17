// 书级复盘弹窗(docs/36 §4):读证据 → AI 提炼 → 候选记忆卡逐条拍板(confirm 档)。
// 从项目列表页的「复盘」按钮打开。off 档在这里给出引导,不提供入口——
// 开关的承诺是「关闭就停止采集」,所以关着时连复盘都不许发起。
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, EvolutionLevel, EvolutionMemoryItem, EvolutionMemoryStatus, RetrospectiveResult,
} from "../api";
import { errMsg } from "../pollJob";
import { toast } from "../ui/Toaster";

const KIND_CN: Record<string, string> = { style: "文风", lesson: "教训", preference: "偏好" };

interface Props { pid: number; title: string; onClose: () => void; }

type Phase =
  | { s: "loading" }                                   // 查档位中
  | { s: "off" }                                       // 开关未开,给引导
  | { s: "idle" }                                      // 可发起
  | { s: "running" }                                   // 复盘进行中
  | { s: "done"; result: RetrospectiveResult; items: EvolutionMemoryItem[] }
  | { s: "error"; message: string };

export default function RetrospectiveDialog({ pid, title, onClose }: Props) {
  const [phase, setPhase] = useState<Phase>({ s: "loading" });
  const [level, setLevel] = useState<EvolutionLevel>("confirm");

  useEffect(() => {
    api.getEvolutionConfig()
      .then((cfg) => {
        setLevel(cfg.level);
        setPhase(cfg.level === "off" ? { s: "off" } : { s: "idle" });
      })
      .catch((e) => setPhase({ s: "error", message: errMsg(e) }));
  }, [pid]);

  async function run() {
    setPhase({ s: "running" });
    try {
      const result = await api.runRetrospective(pid);
      setPhase({ s: "done", result, items: result.memories });
    } catch (e) {
      setPhase({ s: "error", message: errMsg(e) });
    }
  }

  async function setStatus(item: EvolutionMemoryItem, status: EvolutionMemoryStatus) {
    try {
      const updated = await api.patchEvolutionMemory(item.id, status as "confirmed" | "dismissed");
      setPhase((p) => (p.s === "done"
        ? { ...p, items: p.items.map((it) => (it.id === item.id ? updated : it)) }
        : p));
    } catch (e) {
      toast.err("操作失败", errMsg(e));
    }
  }

  const busy = phase.s === "loading" || phase.s === "running";

  return (
    <div className="dlg-overlay" onClick={() => !busy && onClose()}>
      <div className="dlg-content" onClick={(e) => e.stopPropagation()}>
        <h2 className="dlg-title">复盘 · 承《{title}》</h2>

        {phase.s === "loading" && (
          <p className="dlg-body muted"><span className="spin spin-sm" /> 读取进化设置…</p>
        )}

        {phase.s === "off" && (
          <>
            <p className="dlg-body">
              进化开关还没打开。到 <Link to="/settings" onClick={onClose}>设置 → 进化</Link> 选
              「确认后生效」或「自动生效」,系统才能复盘这本书、记住你的文风与教训;
              保持关闭则什么都不会采集。
            </p>
            <div className="dlg-actions">
              <button onClick={onClose}>知道了</button>
            </div>
          </>
        )}

        {phase.s === "idle" && (
          <>
            <p className="dlg-body">
              AI 会通读这部书<b>你确认过的定稿</b>、你动手删改的痕迹和写作中下过的指令,
              提炼出 3-8 条带出处的「文风 / 教训 / 偏好」,下一本开书时自动带上。
              {level === "confirm"
                ? "提炼结果逐条等你拍板,确认过的才生效。"
                : "提炼结果直接生效,之后仍可在 设置 → 进化 里删除。"}
            </p>
            <div className="dlg-actions">
              <button onClick={onClose}>先不复盘</button>
              <button className="primary" onClick={run}>开始复盘</button>
            </div>
          </>
        )}

        {phase.s === "running" && (
          <p className="dlg-body muted">
            <span className="spin spin-sm" /> 正在通读定稿、比对删改、提炼教训…大约一两分钟,别关窗口
          </p>
        )}

        {phase.s === "done" && (
          <>
            <p className="dlg-body">
              {phase.result.summary || "复盘完成。"}
              {phase.result.memories.length === 0 && (
                <span> 这次的教训都已经记过,没有新条目。</span>
              )}
            </p>
            <div className="evo-mems">
              {phase.items.map((it) => (
                <div key={it.id} className="evo-mem">
                  <div className="evo-mem-head">
                    <span className="badge">{KIND_CN[it.kind] ?? it.kind}</span>
                    {level === "confirm" && it.status === "pending" && (
                      <span className="evo-mem-actions">
                        <button className="btn-sm primary" onClick={() => setStatus(it, "confirmed")}>生效</button>
                        <button className="btn-sm" onClick={() => setStatus(it, "dismissed")}>忽略</button>
                      </span>
                    )}
                  </div>
                  <div className="evo-mem-content">{it.content}</div>
                  {it.evidence && <div className="evo-mem-evidence muted">出处:{it.evidence}</div>}
                </div>
              ))}
            </div>
            <div className="dlg-actions">
              <button className="primary" onClick={onClose}>完成</button>
            </div>
          </>
        )}

        {phase.s === "error" && (
          <>
            <p className="dlg-body" style={{ color: "var(--err)" }}>{phase.message}</p>
            <div className="dlg-actions">
              <button onClick={onClose}>关闭</button>
              <button className="primary" onClick={run}>再试一次</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
