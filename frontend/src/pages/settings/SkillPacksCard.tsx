// SkillPacksCard — 创作 Skill 包管理卡(docs/21)。
// 官方内置包随版本分发:可开关、可改条目、版本化可回退——「内置」不等于「焊死」。
// 启用的包按 scope+node 注入对应生成工序(如动漫分镜、整集提示词渲染),
// 注入预算与节点过滤在后端 engines/skills/packs;本卡只是投影。
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, SkillEntry, SkillPack } from "../../api";
import { errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";

const SCOPE_CN: Record<string, string> = {
  anime: "动画短剧", series: "系列短片", drama: "漫剧",
  clips: "短片", novel: "小说", inspire: "灵感",
};
const NODE_CN: Record<string, string> = {
  idea: "出点子", outline: "大纲", draft: "正文", polish: "润色",
  shots: "分镜", render: "提示词渲染",
};
const KIND_CN: Record<string, string> = {
  directive: "指令", param: "参数", ban: "排除清单", format: "渲染工艺",
};

function EntryLine({ e }: { e: SkillEntry }) {
  return (
    <div className="skill-entry">
      <span className="skill-entry-meta">
        {NODE_CN[e.node] ?? e.node} · {KIND_CN[e.kind] ?? e.kind}
      </span>
      {e.directive && <p>{e.directive}</p>}
      {e.params && (
        <p>{Object.entries(e.params).map(([k, v]) => `${k}:${v}`).join(";")}</p>
      )}
      {e.ban_list && e.ban_list.length > 0 && <p>禁止:{e.ban_list.join("、")}</p>}
    </div>
  );
}

function PackRow({ pack }: { pack: SkillPack }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<unknown>, okMsg: string) {
    setBusy(true);
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ["skillPacks"] });
      toast.ok(okMsg, "下一次生成即生效");
    } catch (e) {
      toast.err("操作失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  function startEdit() {
    setDraft(JSON.stringify(pack.entries, null, 2));
    setEditing(true);
    setOpen(true);
  }

  function saveEntries() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(draft);
    } catch {
      toast.err("JSON 解析失败", "条目内容得是合法 JSON 数组");
      return;
    }
    if (!Array.isArray(parsed) || parsed.length === 0) {
      toast.err("条目格式不对", "应该是非空数组,每个条目含 node/kind 等字段");
      return;
    }
    void run(() => api.updateSkillPack(pack.id, { entries: parsed as SkillEntry[] }),
      `《${pack.name}》已更新`);
    setEditing(false);
  }

  return (
    <div className="skill-pack" data-testid={`skill-pack-${pack.pack_key}`}>
      <div className="row">
        <label className="default-pick grow">
          <input
            type="checkbox"
            checked={pack.enabled}
            disabled={busy}
            onChange={(e) =>
              void run(() => api.updateSkillPack(pack.id, { enabled: e.target.checked }),
                e.target.checked ? `《${pack.name}》已启用` : `《${pack.name}》已停用`)
            }
          />
          <b>{pack.name}</b>
          <span className="skill-pack-meta">v{pack.version}{pack.is_builtin ? " · 官方" : ""}</span>
        </label>
        <button className="btn-sm" onClick={() => setOpen(!open)}>
          {open ? "收起" : "条目与历史"}
        </button>
      </div>
      <p className="card-desc">
        {pack.description || "(无说明)"}
        {" "}· 适用:{pack.scope.map((s) => SCOPE_CN[s] ?? s).join("/")}
      </p>
      {open && (
        <div className="skill-pack-body">
          {!editing && (
            <>
              {pack.entries.map((e, i) => <EntryLine key={i} e={e} />)}
              <div className="actions">
                <button className="btn-sm" disabled={busy} onClick={startEdit}>编辑条目</button>
              </div>
            </>
          )}
          {editing && (
            <div className="form-grid">
              <label className="field field-full">
                <span>条目 JSON(node/kind 白名单见后端;改完保存自动留版本)</span>
                <textarea rows={Math.min(20, draft.split("\n").length + 1)} value={draft}
                  onChange={(e) => setDraft(e.target.value)} />
              </label>
              <div className="form-actions">
                <button className="btn-sm primary" disabled={busy} onClick={saveEntries}>保存</button>
                <button className="btn-sm" disabled={busy} onClick={() => setEditing(false)}>取消</button>
              </div>
            </div>
          )}
          {pack.history.length > 0 && (
            <div className="skill-hist">
              <span className="skill-entry-meta">历史版本:</span>
              {[...pack.history].reverse().map((h) => (
                <button key={h.version} className="btn-sm" disabled={busy}
                  title="回退到该版内容(回退本身也存为新版本)"
                  onClick={() => void run(() => api.restoreSkillPack(pack.id, h.version),
                    `《${pack.name}》已回退 v${h.version} 内容`)}>
                  回到 v{h.version}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function SkillPacksCard() {
  const q = useQuery({ queryKey: ["skillPacks"], queryFn: () => api.listSkillPacks() });
  if (q.isLoading || !q.data || q.data.length === 0) return null;
  return (
    <div className="card">
      <div className="card-head"><h2>创作 Skill 包</h2></div>
      <p className="card-desc">
        成套的创作工艺包:勾选启用后按工序注入生成(如动漫分镜、整集提示词渲染)。
        官方包也能改——条目可编辑、可回退版本,不满意改到合心意为止。
      </p>
      {q.data.map((p) => <PackRow key={p.id} pack={p} />)}
    </div>
  );
}
