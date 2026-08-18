// 单套配置的展示卡片:名称 + 协议/默认/快档徽标 + 打码 key + base_url/model + 操作按钮。
// 拆自 SettingsPage.tsx。
import { useState } from "react";
import { api, ProviderConfigOut } from "../../api";
import { toast } from "../../ui/Toaster";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { CATEGORY_BY_KEY, FORMAT_LABEL, normalizeCategory } from "./providerCatalog";
import { errMsg } from "../../pollJob";

export function ProviderRow({ p, onChanged, onEdit }: {
  p: ProviderConfigOut; onChanged: () => void; onEdit: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const cat = CATEGORY_BY_KEY[normalizeCategory(p.interface_format)];

  async function test() {
    setBusy(true); setTestMsg(null);
    try {
      const r = await api.testProvider(p.id);
      if (r.ok) setTestMsg({ ok: true, text: `✓ 连通(${r.model}):${r.reply}` });
      else setTestMsg({ ok: false, text: `✗ 连接失败:${r.error}` });
    } catch (e) {
      setTestMsg({ ok: false, text: `✗ ${errMsg(e)}` });
    } finally { setBusy(false); }
  }

  async function remove() {
    setBusy(true); setTestMsg(null);
    try {
      // 删除是一锤子买卖:无论后端是否要求二次确认,先统一确认一遍
      const ok = await confirmDialog({
        title: `删除「${p.name}」?`,
        body: "删除后需重新配置 API Key 才能使用该配置。",
        confirmText: "确认删除",
        danger: true,
      });
      if (!ok) { setBusy(false); return; }
      let r = await api.deleteProvider(p.id);
      if (!r.deleted && r.needs_confirm) {
        const ok2 = await confirmDialog({
          title: `删除「${p.name}」?`,
          body: (r.reason || "该配置当前连接正常。") + "\n删除后将回落到其他默认配置。",
          confirmText: "确认删除",
          danger: true,
        });
        if (!ok2) { setBusy(false); return; }
        r = await api.deleteProvider(p.id, true);
      }
      if (r.deleted) { toast.ok("已删除"); onChanged(); }
    } catch (e) {
      toast.err("删除失败", errMsg(e));
    } finally { setBusy(false); }
  }

  // 一键设为默认/快档:PUT 全量字段,只翻转目标标记(后端会清掉其他配置的同名标记)
  async function setFlag(flag: "is_default" | "is_default_fast") {
    setBusy(true); setTestMsg(null);
    try {
      await api.updateProvider(p.id, {
        name: p.name,
        interface_format: p.interface_format,
        base_url: p.base_url,
        model: p.model,
        timeout: p.timeout,
        max_tokens: p.max_tokens,
        [flag]: true,
      });
      toast.ok(flag === "is_default" ? `「${p.name}」已设为默认` : `「${p.name}」已设为快档`);
      onChanged();
    } catch (e) {
      toast.err("设置失败", errMsg(e));
    } finally { setBusy(false); }
  }

  return (
    <div className="provider-row">
      <div className="card-head">
        <h3>{p.name}</h3>
        <span className="badge">{FORMAT_LABEL[p.interface_format] || p.interface_format}</span>
        <span className={`badge ${p.has_key ? "ok" : "err"}`}>
          {p.has_key ? "已配置" : "未配置"}
        </span>
        {p.is_default && <span className="badge">默认</span>}
        {p.is_default_fast && <span className="badge">快档</span>}
      </div>
      <p className="provider-desc">
        {cat?.desc || ""}
        {p.has_key && ` Key:${p.api_key_masked}。`}
        {p.base_url || p.model
          ? ` ${p.base_url || cat?.baseUrl || ""} · ${p.model || cat?.model || ""}`
          : ""}
        {(p.timeout > 0 || p.max_tokens > 0) &&
          ` · 超时 ${p.timeout > 0 ? `${p.timeout}s` : "跟随全局"} · max_tokens ${p.max_tokens > 0 ? p.max_tokens : "跟随全局"}`}
      </p>

      <div className="provider-actions">
        <button className="btn-sm" onClick={onEdit} disabled={busy}>编辑</button>
        <button className="btn-sm" onClick={test} disabled={busy}>测试连接</button>
        {!p.is_default && (
          <button className="btn-sm" onClick={() => setFlag("is_default")} disabled={busy}>
            设为默认
          </button>
        )}
        {!p.is_default_fast && (
          <button className="btn-sm" onClick={() => setFlag("is_default_fast")} disabled={busy}>
            设为快档
          </button>
        )}
        <button className="btn-sm danger" onClick={remove} disabled={busy}>删除</button>
      </div>

      {testMsg && (
        <div className={`test-line ${testMsg.ok ? "ok" : "err"}`}>{testMsg.text}</div>
      )}
    </div>
  );
}
