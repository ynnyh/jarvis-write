// StyleProfileCard — 文风画像卡(docs/20 同批「文风可视化+进化」)。
// 六维(视角/句式节奏/对话密度/修辞/基调/钩法)可视化,每维可直接编辑——
// 你看到的就是每次生成实际注入的内容;「重新分析」从来源作品(续集)或本书
// 已定稿章节重跑画像(全书均匀分层采样,常数开销);每次保存/分析/回退都留版本
// 历史,可回退。一份真相:所有读写都走 projects.style_profile,卡片只是投影。
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, StyleDimensionsOut } from "../api";
import { pollJob, errMsg } from "../pollJob";
import { toast } from "./Toaster";

export default function StyleProfileCard({ pid }: { pid: number }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  const q = useQuery({
    queryKey: ["styleDimensions", pid],
    queryFn: () => api.getStyleDimensions(pid),
  });

  async function refresh() {
    await qc.invalidateQueries({ queryKey: ["styleDimensions", pid] });
  }

  async function save() {
    if (!q.data) return;
    setBusy(true);
    try {
      await api.saveStyleDimensions(pid, draft);
      setDraft({});
      setEditing(false);
      await refresh();
      toast.ok("文风画像已更新", "下一次生成就会按新画像执行");
    } catch (e) {
      toast.err("保存失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function reanalyze() {
    setBusy(true);
    try {
      const { job_id } = await api.reanalyzeStyleDimensionsAsync(pid);
      await pollJob(job_id, { onStage: () => setBusy(true) });
      await refresh();
      toast.ok("文风画像已重新分析", "六维按最新采样更新,旧版在历史里可回退");
    } catch (e) {
      toast.err("重新分析失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function restore(version: number) {
    setBusy(true);
    try {
      await api.restoreStyleDimensions(pid, version);
      await refresh();
      toast.ok(`已回退到 v${version}`, "回退本身也留了版本,随时可再切回");
    } catch (e) {
      toast.err("回退失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  if (q.isLoading || !q.data) return null;
  const d: StyleDimensionsOut = q.data;
  const dimText = (k: string) => draft[k] ?? d.dims[k]?.text ?? "";
  const hasAny = d.dim_defs.some((def) => (d.dims[def.key]?.text ?? "").trim());
  const dirty = Object.keys(draft).some((k) => (draft[k] ?? "") !== (d.dims[k]?.text ?? ""));

  return (
    <div className="card card-compact mt-2" data-testid="style-profile-card">
      <div className="style-card-head">
        <b>文风画像{d.version > 0 ? ` · v${d.version}` : ""}</b>
        <span className="grow" />
        {!editing && (
          <button className="btn-sm" onClick={() => {
            setDraft(Object.fromEntries(d.dim_defs.map((def) => [def.key, d.dims[def.key]?.text ?? ""])));
            setEditing(true);
          }}>编辑</button>
        )}
        <button className="btn-sm" disabled={busy}
          title="续集:对前作重跑分析;普通书:从本书已定稿章节重新提炼(均匀分层采样,开销恒定)"
          onClick={() => { void reanalyze(); }}>
          {busy ? "分析中…" : "重新分析"}
        </button>
      </div>
      <div className="hint mb-1">
        这份画像是每次生成正文时实际注入的笔法指令——看得见、改得动、随时进化。
        {hasAny ? "" : " 还没有画像:点「重新分析」提炼,或直接编辑各维度。"}
      </div>

      <div className="style-dims">
        {d.dim_defs.map((def) => {
          const dim = d.dims[def.key];
          return (
            <div className={"style-dim" + ((dim?.text ?? "").trim() ? "" : " empty")} key={def.key}>
              <div className="style-dim-head">
                <b>{def.label}</b>
                {dim?.source && <span className="chip">{dim.source}</span>}
                {!editing && (dim?.text ?? "").trim() === "" && (
                  <span className="muted">未提炼</span>
                )}
              </div>
              {editing ? (
                <textarea rows={3} className="style-dim-edit"
                  placeholder={def.hint}
                  value={dimText(def.key)}
                  onChange={(e) => setDraft((cur) => ({ ...cur, [def.key]: e.target.value }))} />
              ) : (
                <p className="style-dim-text">{dim?.text || <span className="muted">{def.hint}</span>}</p>
              )}
            </div>
          );
        })}
      </div>

      {editing && (
        <div className="actions mt-2">
          <button className="primary btn-sm" disabled={busy || !dirty} onClick={() => { void save(); }}>
            {busy && <span className="spin spin-sm" />}保存(旧版进历史)
          </button>
          <button className="btn-sm" disabled={busy} onClick={() => { setDraft({}); setEditing(false); }}>取消</button>
        </div>
      )}

      {d.history.length > 0 && (
        <div className="mt-2">
          <button className="btn-sm" onClick={() => setHistoryOpen(!historyOpen)}>
            {historyOpen ? "收起历史" : `历史版本(${d.history.length})`}
          </button>
          {historyOpen && (
            <div className="style-history mt-1">
              {[...d.history].reverse().map((h) => (
                <div className="style-hist-row" key={h.version}>
                  <span>v{h.version}</span>
                  <span className="muted">{(h.at || "").slice(0, 16).replace("T", " ")}</span>
                  <span className="muted">
                    {d.dim_defs.filter((def) => (h.dims?.[def.key]?.text ?? "").trim()).length}/6 维有内容
                  </span>
                  <button className="btn-sm" disabled={busy} onClick={() => { void restore(h.version); }}>
                    回退到此版
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
