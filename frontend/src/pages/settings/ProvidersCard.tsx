// 模型设置卡(cc-switch 风格):每用户多套命名配置,可增删改、一键切换默认/快档(各全用户唯一)。
// 拆自 SettingsPage.tsx。
import { useEffect, useState } from "react";
import { api, ProviderConfigOut } from "../../api";
import { ProviderForm } from "./ProviderForm";
import { ProviderRow } from "./ProviderRow";
import { errMsg } from "../../pollJob";

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
    </div>
  );
}
