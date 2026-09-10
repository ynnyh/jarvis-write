// 模型设置卡(cc-switch 风格):每用户多套命名配置,可增删改、一键切换默认/快档(各全用户唯一)。
// 拆自 SettingsPage.tsx。
import { useEffect, useState } from "react";
import { api, ModelRoles, ProviderConfigOut } from "../../api";
import { ProviderForm } from "./ProviderForm";
import { ProviderRow } from "./ProviderRow";
import { errMsg } from "../../pollJob";

// 角色分配现状条(D7):把「钱花在哪一刀上」摊开给用户看。
// 三个角色各自的档位与模型名,写手/审校没分开时给可操作的提示。
function ModelRolesStrip() {
  const [roles, setRoles] = useState<ModelRoles | null>(null);
  useEffect(() => {
    api.modelRoles().then(setRoles).catch(() => undefined);
  }, []);
  if (!roles || !roles.available) return null;

  const items: { key: string; label: string; hint: string; cfg: ModelRoles["writer"] }[] = [
    { key: "writer", label: "创作", hint: "草稿/定稿/逐场生成——钱该花在这里", cfg: roles.writer },
    { key: "auditor", label: "审校", hint: "主审/一致性/场景验收——与创作分模型才有客观性", cfg: roles.auditor },
    { key: "worker", label: "杂活", hint: "摘要/抽取/去味——不该占强档", cfg: roles.worker },
  ];

  return (
    <div style={{ marginTop: 12, paddingTop: 12, borderTop: "0.5px solid var(--color-border-tertiary)" }}>
      <div className="hint" style={{ marginBottom: 6 }}>当前模型角色分配</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8 }}>
        {items.map((it) => (
          <div key={it.key} style={{
            background: "var(--color-background-secondary)",
            borderRadius: "var(--border-radius-md)", padding: "8px 10px",
          }}>
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>{it.label}</div>
            <div style={{ fontSize: 13, fontWeight: 500, marginTop: 2, overflow: "hidden",
              textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              title={`${it.cfg.name}${it.cfg.model ? " · " + it.cfg.model : ""}`}>
              {it.cfg.name}
            </div>
            <div style={{ fontSize: 11, color: "var(--color-text-tertiary)" }}>{it.hint}</div>
          </div>
        ))}
      </div>
      {roles.advice && (
        <div className="hint" style={{ marginTop: 8, color: "var(--color-text-warning, #BA7517)" }}>
          {roles.advice}
        </div>
      )}
    </div>
  );
}

export function ProvidersCard() {
  const [list, setList] = useState<ProviderConfigOut[] | null>(null);
  const [err, setErr] = useState("");
  // adding=展开添加表单;editingId=正在行内编辑的配置 id
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);

  async function load() {
    try { setList(await api.listProviders()); }
    catch (e) { setErr(errMsg(e)); }
  }
  useEffect(() => { void load(); }, []);

  if (err) {
    return (
      <div className="card card-warn">
        <div className="card-head"><h2>模型设置</h2></div>
        <p className="card-desc">加载失败:{err}</p>
      </div>
    );
  }
  if (!list) {
    return (
      <div className="card">
        <div className="card-head"><h2>模型设置</h2></div>
        <p className="card-desc"><span className="spin" /> 加载中…</p>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-head">
        <h2>模型设置</h2>
        {!adding && (
          <button className="btn-sm" onClick={() => { setAdding(true); setEditingId(null); }}>
            + 添加配置
          </button>
        )}
      </div>
      <p className="card-desc">
        配置至少一套模型的 API Key 即可开始创作,同一个协议可以配多套(官方、中转站各一套)。
        「默认」为生成时优先使用的配置,「快档」用于轻量快任务;各只能设一套。
      </p>

      {adding && (
        <ProviderForm
          onSaved={() => { setAdding(false); void load(); }}
          onCancel={() => setAdding(false)}
        />
      )}

      {list.length === 0 && !adding && (
        <p className="card-desc">还没有模型配置,点右上角「添加配置」开始。</p>
      )}

      <div className="provider-list">
        {list.map((p) => (
          editingId === p.id ? (
            <ProviderForm
              key={p.id}
              editing={p}
              onSaved={() => { setEditingId(null); void load(); }}
              onCancel={() => setEditingId(null)}
            />
          ) : (
            <ProviderRow
              key={p.id}
              p={p}
              onChanged={load}
              onEdit={() => { setEditingId(p.id); setAdding(false); }}
            />
          )
        ))}
      </div>

      <ModelRolesStrip />
    </div>
  );
}
