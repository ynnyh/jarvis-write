// 进化卡(docs/36):三档自治旋钮 + 复盘记忆列表(可见可删)。
// off=不采集不注入(硬承诺:连证据都不读);confirm=候选逐条拍板;auto=直接生效仍可删。
// 记忆是用户资产:单条可删、可一键清空,删了立即不影响下一次注入。
import { useEffect, useState } from "react";
import { api, EvolutionLevel, EvolutionMemoryItem, EvolutionMemoryStatus } from "../../api";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";

const LEVELS: { v: EvolutionLevel; label: string; note: string }[] = [
  { v: "off", label: "关闭", note: "不采集、不注入:系统不会读你的书,也不记住任何东西。" },
  { v: "confirm", label: "确认后生效", note: "复盘提炼出候选的文风与教训,你逐条拍板;确认过的才会带进下一本书。" },
  { v: "auto", label: "自动生效", note: "复盘结果直接生效(仍然可见、可删)。想省事选这档,想亲自把关选上一档。" },
];

const KIND_CN: Record<string, string> = { style: "文风", lesson: "教训", preference: "偏好" };
const STATUS_CN: Record<string, string> = { pending: "待确认", confirmed: "已生效", dismissed: "已忽略" };

export function EvolutionCard() {
  const [level, setLevel] = useState<EvolutionLevel>("off");
  const [items, setItems] = useState<EvolutionMemoryItem[]>([]);
  const [err, setErr] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);

  async function load() {
    try {
      const [cfg, list] = await Promise.all([
        api.getEvolutionConfig(),
        api.listEvolutionMemories(),
      ]);
      setLevel(cfg.level);
      setItems(list.items);
      setErr("");
    } catch (e) {
      setErr(errMsg(e));
    }
  }
  useEffect(() => { load(); }, []);

  async function pick(v: EvolutionLevel) {
    if (v === level) return;
    const prev = level;
    setLevel(v);
    try {
      await api.saveEvolutionConfig(v);
      toast.ok(v === "off" ? "已关闭:停止采集与注入" : "进化已开启");
    } catch (e) {
      setLevel(prev);
      toast.err("保存失败", errMsg(e));
    }
  }

  async function setStatus(item: EvolutionMemoryItem, status: EvolutionMemoryStatus) {
    setBusyId(item.id);
    try {
      const updated = await api.patchEvolutionMemory(item.id, status as "confirmed" | "dismissed");
      setItems((prev) => prev.map((it) => (it.id === item.id ? updated : it)));
    } catch (e) {
      toast.err("操作失败", errMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  async function removeOne(item: EvolutionMemoryItem) {
    const ok = await confirmDialog({
      title: "删掉这条记忆?",
      body: item.content,
      confirmText: "删除",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteEvolutionMemory(item.id);
      setItems((prev) => prev.filter((it) => it.id !== item.id));
    } catch (e) {
      toast.err("删除失败", errMsg(e));
    }
  }

  async function clearAll() {
    const ok = await confirmDialog({
      title: `清空全部 ${items.length} 条记忆?`,
      body: "系统会彻底忘掉从你的书里学到的所有东西,下一本书不再带上。",
      confirmText: "清空",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.clearEvolutionMemories();
      setItems([]);
      toast.ok("已清空");
    } catch (e) {
      toast.err("清空失败", errMsg(e));
    }
  }

  return (
    <div className="card">
      <div className="card-head"><h2>进化</h2></div>
      <p className="card-desc">
        让系统越用越懂你:复盘你写过的书,提炼文风与教训,开新书时自动带上。
        主动权在你——档位随时可调,记忆逐条可删。
      </p>
      <div className="chips" role="group" aria-label="进化档位">
        {LEVELS.map((o) => (
          <button key={o.v} type="button"
            className={"chip" + (level === o.v ? " on" : "")}
            onClick={() => pick(o.v)}>
            {o.label}
          </button>
        ))}
      </div>
      <p className="card-desc">{LEVELS.find((o) => o.v === level)?.note}</p>
      {err && <p className="card-desc" style={{ color: "var(--err)" }}>加载失败:{err}</p>}

      {items.length > 0 && (
        <div className="evo-mems">
          <div className="evo-mems-head">
            <span className="fl">学到的记忆({items.length})</span>
            <button className="btn-sm danger" onClick={clearAll}>清空全部</button>
          </div>
          {items.map((it) => (
            <div key={it.id} className="evo-mem">
              <div className="evo-mem-head">
                <span className="badge">{KIND_CN[it.kind] ?? it.kind}</span>
                <span className={"badge " + (it.status === "confirmed" ? "ok" : "mute")}>
                  {STATUS_CN[it.status] ?? it.status}
                </span>
                <span className="evo-mem-actions">
                  {it.status !== "confirmed" && (
                    <button className="btn-sm" disabled={busyId === it.id}
                      onClick={() => setStatus(it, "confirmed")}>生效</button>
                  )}
                  {it.status !== "dismissed" && (
                    <button className="btn-sm" disabled={busyId === it.id}
                      onClick={() => setStatus(it, "dismissed")}>忽略</button>
                  )}
                  <button className="btn-sm danger" disabled={busyId === it.id}
                    onClick={() => removeOne(it)}>删除</button>
                </span>
              </div>
              <div className="evo-mem-content">{it.content}</div>
              {it.evidence && <div className="evo-mem-evidence muted">出处:{it.evidence}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
