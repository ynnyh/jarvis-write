// 添加/编辑模型配置的共用表单:先选协议预设(预填 base_url/model 占位),再填名称/Key/地址/模型名;
// 高级项(timeout、max_tokens,0=跟随全局)折叠收起。编辑时 key 留空表示不修改。拆自 SettingsPage.tsx。
import { useState } from "react";
import { api, ProviderConfigOut } from "../../api";
import { toast } from "../../ui/Toaster";
import { CATEGORY_BY_KEY, normalizeCategory, PROTOCOL_CATEGORIES, QUICK_PRESETS } from "./providerCatalog";
import { errMsg } from "../../pollJob";

export function ProviderForm({ editing, onSaved, onCancel }: {
  editing?: ProviderConfigOut; onSaved: () => void; onCancel: () => void;
}) {
  const [category, setCategory] = useState(normalizeCategory(editing?.interface_format || "openai-compatible"));
  const [name, setName] = useState(editing?.name || "");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(editing?.base_url || "");
  const [model, setModel] = useState(editing?.model || "");
  const [timeout_, setTimeout_] = useState(String(editing?.timeout ?? 0));
  const [maxTokens, setMaxTokens] = useState(String(editing?.max_tokens ?? 0));
  const [busy, setBusy] = useState(false);

  const cur = CATEGORY_BY_KEY[category];
  const canSubmit = !busy && (editing ? true : !!apiKey.trim());

  async function save() {
    setBusy(true);
    try {
      const body = {
        name: name.trim(),
        interface_format: category,
        api_key: apiKey.trim() || null,
        base_url: baseUrl.trim(),
        model: model.trim(),
        timeout: Math.max(0, parseInt(timeout_, 10) || 0),
        max_tokens: Math.max(0, parseInt(maxTokens, 10) || 0),
      };
      const saved = editing
        ? await api.updateProvider(editing.id, body)
        : await api.createProvider(body);
      toast.ok(editing ? `「${body.name || editing.name}」已保存` : "配置已添加");
      if (saved.cloudflare) {
        toast.info(
          "该渠道套了 Cloudflare CDN",
          "国内网络直连可能间歇性连接失败,长文生成比单次测试更容易撞上;若频繁报「上游连续 3 次调用失败」,建议换直连渠道。",
        );
      }
      onSaved();
    } catch (e) {
      toast.err("保存失败", errMsg(e));
    } finally { setBusy(false); }
  }

  return (
    <div className="provider-row">
      <div className="card-head">
        <h3>{editing ? `编辑「${editing.name}」` : "添加配置"}</h3>
      </div>

      <label className="fl">快捷预设</label>
      <div className="chips">
        {QUICK_PRESETS.map((q) => (
          <button key={q.label} type="button" className="chip"
            onClick={() => { setCategory(q.category); setBaseUrl(q.baseUrl); setModel(q.model); }}>
            {q.label}
          </button>
        ))}
      </div>
      <div className="fld-hint" style={{ marginTop: 6 }}>
        点一下自动选好协议并填入官方 Base URL / 模型名,通常你只需再填 API Key。
      </div>

      <label className="fl" style={{ marginTop: 12 }}>协议</label>
      <div className="chips">
        {PROTOCOL_CATEGORIES.map((o) => (
          <button key={o.key} type="button"
            className={"chip" + (category === o.key ? " on" : "")}
            onClick={() => setCategory(o.key)}>
            {o.label}
          </button>
        ))}
      </div>
      {cur && <div className="fld-hint" style={{ marginTop: 6 }}>{cur.desc}</div>}

      <div className="fld-row" style={{ marginTop: 12 }}>
        <div className="fld">
          <label className="fl">配置名称</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={cur ? `${cur.label} 官方` : "给这套配置起个名字"}
          />
          <div className="fld-hint">留空则自动用模型名;同一协议配多套时建议起个能区分的名字。</div>
        </div>
        <div className="fld">
          <label className="fl">
            API Key{editing?.has_key ? `(已保存:${editing.api_key_masked},留空则不修改)` : ""}
          </label>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={editing?.has_key ? "留空保持不变" : "sk-..."}
          />
        </div>
      </div>

      <div className="fld-row">
        <div className="fld">
          <label className="fl">Base URL</label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={cur?.baseUrl}
          />
          <div className="fld-hint">用中转站就填中转地址,如 https://xxx.com/v1;留空用官方地址。</div>
        </div>
        <div className="fld">
          <label className="fl">模型名</label>
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={cur?.model}
          />
        </div>
      </div>

      <details style={{ marginTop: 12 }}>
        <summary className="fld-hint" style={{ cursor: "pointer" }}>
          高级选项(超时 / max_tokens,0 = 跟随全局)
        </summary>
        <div className="fld-row" style={{ marginTop: 8 }}>
          <div className="fld">
            <label className="fl">超时(秒)</label>
            <input
              type="text"
              value={timeout_}
              onChange={(e) => setTimeout_(e.target.value)}
              placeholder="0"
              spellCheck={false}
            />
          </div>
          <div className="fld">
            <label className="fl">max_tokens</label>
            <input
              type="text"
              value={maxTokens}
              onChange={(e) => setMaxTokens(e.target.value)}
              placeholder="0"
              spellCheck={false}
            />
          </div>
        </div>
      </details>

      <div className="provider-actions">
        <button className="primary btn-sm" onClick={save} disabled={!canSubmit}>
          {busy && <span className="spin" />}{editing ? "保存" : "添加"}
        </button>
        <button className="btn-sm" onClick={onCancel} disabled={busy}>取消</button>
      </div>
    </div>
  );
}
