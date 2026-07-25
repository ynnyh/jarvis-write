// 设置页(桌面/网页统一,SPA 内路由 /#/settings):
//   · 关于 & 更新:显示版本;桌面版可检查更新 → 静默下载 → 提示重启生效
//   · 模型设置:三家 provider 增删改测(取代旧的独立 settings.html)
//   · 偏好:外观(跟随系统/浅色/深色,全端生效)+ 启动时自动检查更新开关(仅桌面)
// 桌面能力经 desktop.ts 优雅降级:非桌面(网页)隐藏更新相关 UI。
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ProviderSettingOut } from "../api";
import {
  isDesktop,
  checkUpdate,
  downloadAndInstallUpdate,
  restartApp,
  onUpdateProgress,
  UpdateInfo,
} from "../desktop";
import { getThemePref, setThemePref, ThemePref } from "../theme";
import { toast } from "../ui/Toaster";
import { confirmDialog } from "../ui/ConfirmDialog";

// 各家展示名与一句话说明(与旧 settings.html 一致)
const PROVIDER_NAMES: Record<string, string> = {
  deepseek: "DeepSeek",
  openai: "OpenAI",
  gemini: "Google Gemini",
};
const PROVIDER_DESCS: Record<string, string> = {
  deepseek: "推荐:国产,便宜量大,写长篇性价比高。",
  openai: "需要海外网络环境。",
  gemini: "需要海外网络环境。",
};

// 偏好:启动时自动检查更新(仅桌面有意义)。默认开。
const AUTO_CHECK_KEY = "jarvis_auto_check_update";
function getAutoCheck(): boolean {
  return localStorage.getItem(AUTO_CHECK_KEY) !== "false";
}

export default function SettingsPage() {
  return (
    <div className="settings-page">
      <div className="settings-head">
        <Link to="/" className="linkbtn">← 返回工作台</Link>
        <h1>设置</h1>
      </div>
      <AboutUpdateCard />
      <ProvidersCard />
      <PreferencesCard />
      <div className="settings-foot">
        <a
          href="/docs"
          onClick={(e) => {
            // 桌面壳不处理 target=_blank;交后端用系统浏览器打开
            if (isDesktop()) { e.preventDefault(); api.openLink(`${location.origin}/docs`).catch(() => {}); }
          }}
          target={isDesktop() ? undefined : "_blank"}
          rel="noreferrer"
        >
          API 文档 / OpenAPI →
        </a>
      </div>
    </div>
  );
}

// ============ 关于 & 更新 ============
type UpdateStage = "idle" | "checking" | "available" | "downloading" | "ready" | "latest" | "error";

function AboutUpdateCard() {
  const desktop = isDesktop();
  // 版本号:桌面走 /api/version 的 app_version(打包烤入);拿不到回落 commit/dev
  const [version, setVersion] = useState<string>("");
  const [stage, setStage] = useState<UpdateStage>("idle");
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [errMsg, setErrMsg] = useState("");
  const [progress, setProgress] = useState<{ done: number; total: number }>({ done: 0, total: 0 });
  const unlistenRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    api.getVersion()
      .then((v) => setVersion(v.app_version && v.app_version !== "dev" ? v.app_version : v.commit))
      .catch(() => setVersion(""));
  }, []);

  // 桌面版:进入设置页自动检查一次(也让 Rust 侧「前端已接管」置真,原生框让位)
  useEffect(() => {
    if (!desktop) return;
    void doCheck();
    return () => { unlistenRef.current?.(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function doCheck() {
    setStage("checking"); setErrMsg("");
    try {
      const r = await checkUpdate();
      if (r.available) { setInfo(r); setStage("available"); }
      else { setStage("latest"); }
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : String(e));
      setStage("error");
    }
  }

  async function doDownload() {
    setStage("downloading"); setProgress({ done: 0, total: 0 });
    // 订阅进度
    unlistenRef.current = await onUpdateProgress((done, total) =>
      setProgress({ done, total }));
    try {
      await downloadAndInstallUpdate();
      unlistenRef.current?.();
      setStage("ready");
      toast.ok("新版本已就绪", "重启应用即可生效");
    } catch (e) {
      unlistenRef.current?.();
      setErrMsg(e instanceof Error ? e.message : String(e));
      setStage("error");
    }
  }

  async function doRestart() {
    const ok = await confirmDialog({
      title: "立即重启以完成更新?",
      body: "应用会关闭并重新打开。未保存的操作请先处理。",
      confirmText: "立即重启",
    });
    if (ok) restartApp().catch((e) => toast.err("重启失败", String(e)));
  }

  const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : null;

  return (
    <div className="card">
      <div className="card-head">
        <h2>关于 & 更新</h2>
      </div>
      <p className="card-desc">
        当前版本 <strong>{version ? `v${version}` : "—"}</strong>
        {version === "dev" && "(开发构建)"}
      </p>

      {!desktop && (
        <p className="card-desc" style={{ marginBottom: 0 }}>
          网页版随访问自动加载最新;桌面版可在此检查并一键更新。
        </p>
      )}

      {desktop && (
        <div className="update-box">
          {stage === "checking" && <span className="muted">正在检查更新…</span>}

          {stage === "latest" && (
            <div className="update-line ok">
              <span>✓ 已是最新版本</span>
              <button className="btn-sm" onClick={doCheck}>重新检查</button>
            </div>
          )}

          {stage === "error" && (
            <div className="update-line err">
              <span>检查/更新失败:{errMsg}</span>
              <button className="btn-sm" onClick={doCheck}>重试</button>
            </div>
          )}

          {stage === "available" && info && (
            <div className="update-avail">
              <div className="update-line">
                <span>发现新版本 <strong>v{info.version}</strong>(当前 v{info.current})</span>
                <button className="primary btn-sm" onClick={doDownload}>下载并安装</button>
              </div>
              {info.notes && (
                <div className="update-notes">
                  {info.notes.split("\n").map((l, i) => (
                    <div key={i}>{l.replace(/^-\s*/, "• ") || " "}</div>
                  ))}
                </div>
              )}
            </div>
          )}

          {stage === "downloading" && (
            <div className="update-progress">
              <span className="muted">正在下载并安装…{pct !== null ? ` ${pct}%` : ""}</span>
              <div className="pbar"><div className="pbar-fill" style={{ width: pct !== null ? `${pct}%` : "40%" }} /></div>
            </div>
          )}

          {stage === "ready" && (
            <div className="update-line ok">
              <span>✓ 新版本已就绪,重启后生效</span>
              <button className="primary btn-sm" onClick={doRestart}>立即重启</button>
            </div>
          )}

          {stage === "idle" && (
            <button className="btn-sm" onClick={doCheck}>检查更新</button>
          )}
        </div>
      )}
    </div>
  );
}

// ============ 模型设置 ============
function ProvidersCard() {
  const [list, setList] = useState<ProviderSettingOut[] | null>(null);
  const [err, setErr] = useState("");

  async function load() {
    try { setList(await api.listProviders()); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
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
      <div className="card-head"><h2>模型设置</h2></div>
      <p className="card-desc">
        配置至少一家模型的 API Key 即可开始创作。设为默认的模型会在生成时优先使用;
        只配一家时可不设,系统会自动用它。
      </p>
      <div className="provider-list">
        {list.map((p) => (
          <ProviderRow key={p.provider} p={p} onChanged={load} />
        ))}
      </div>
    </div>
  );
}

function ProviderRow({ p, onChanged }: { p: ProviderSettingOut; onChanged: () => void }) {
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(p.base_url);
  const [model, setModel] = useState(p.model);
  const [isDefault, setIsDefault] = useState(p.is_default);
  const [busy, setBusy] = useState(false);
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function save() {
    setBusy(true); setTestMsg(null);
    try {
      await api.saveProvider(p.provider, {
        api_key: apiKey || null,
        base_url: baseUrl,
        model,
        is_default: isDefault,
      });
      toast.ok(`${PROVIDER_NAMES[p.provider] || p.provider} 已保存`);
      setApiKey("");
      onChanged();
    } catch (e) {
      toast.err("保存失败", e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  async function test() {
    setBusy(true); setTestMsg(null);
    try {
      const r = await api.testProvider(p.provider);
      if (r.ok) setTestMsg({ ok: true, text: `✓ 连通(${r.model}):${r.reply}` });
      else setTestMsg({ ok: false, text: `✗ 连接失败:${r.error}` });
    } catch (e) {
      setTestMsg({ ok: false, text: `✗ ${e instanceof Error ? e.message : String(e)}` });
    } finally { setBusy(false); }
  }

  async function remove() {
    setBusy(true); setTestMsg(null);
    try {
      let r = await api.deleteProvider(p.provider);
      if (!r.deleted && r.needs_confirm) {
        const ok = await confirmDialog({
          title: `删除 ${PROVIDER_NAMES[p.provider] || p.provider} 配置?`,
          body: (r.reason || "该配置当前连接正常。") + "\n删除后将回落到默认/环境配置。",
          confirmText: "确认删除",
          danger: true,
        });
        if (!ok) { setBusy(false); return; }
        r = await api.deleteProvider(p.provider, true);
      }
      if (r.deleted) { toast.ok("已删除"); onChanged(); }
    } catch (e) {
      toast.err("删除失败", e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  return (
    <div className="provider-row">
      <div className="card-head">
        <h3>{PROVIDER_NAMES[p.provider] || p.provider}</h3>
        <span className={`badge ${p.has_key ? "ok" : "err"}`}>
          {p.has_key ? "已配置" : "未配置"}
        </span>
        {p.is_default && <span className="badge">默认模型</span>}
      </div>
      <p className="provider-desc">{PROVIDER_DESCS[p.provider] || ""}</p>

      <label className="fl">
        API Key{p.has_key ? `(已保存:${p.api_key_masked},留空则不修改)` : ""}
      </label>
      <input
        type="password"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder={p.has_key ? "留空保持不变" : "sk-..."}
      />

      <div className="fld-row">
        <div className="fld">
          <label className="fl">Base URL</label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={p.default_base_url}
          />
          <div className="fld-hint">用中转站就填中转地址,如 https://xxx.com/v1</div>
        </div>
        <div className="fld">
          <label className="fl">模型名</label>
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={p.default_model}
          />
        </div>
      </div>

      <label className="default-pick">
        <input
          type="checkbox"
          checked={isDefault}
          onChange={(e) => setIsDefault(e.target.checked)}
        />
        设为默认(生成时优先用它)
      </label>

      <div className="provider-actions">
        <button className="primary btn-sm" onClick={save} disabled={busy}>保存</button>
        <button className="btn-sm" onClick={test} disabled={busy}>测试连接</button>
        {p.has_key && (
          <button className="btn-sm danger" onClick={remove} disabled={busy}>删除</button>
        )}
      </div>

      {testMsg && (
        <div className={`test-line ${testMsg.ok ? "ok" : "err"}`}>{testMsg.text}</div>
      )}
    </div>
  );
}

// ============ 偏好 ============
// 外观选择全端生效(写 localStorage 并即改 <html data-theme>);
// 自动更新开关仅桌面有意义(网页随访问自动最新),非桌面不渲染。
const THEME_OPTIONS: { v: ThemePref; label: string }[] = [
  { v: "auto", label: "跟随系统" },
  { v: "light", label: "浅色" },
  { v: "dark", label: "深色" },
];

function PreferencesCard() {
  const [theme, setTheme] = useState<ThemePref>(getThemePref);
  const [autoCheck, setAutoCheck] = useState(getAutoCheck());

  function pickTheme(v: ThemePref) {
    setThemePref(v);
    setTheme(v);
  }

  function toggle(v: boolean) {
    setAutoCheck(v);
    localStorage.setItem(AUTO_CHECK_KEY, v ? "true" : "false");
  }

  return (
    <div className="card">
      <div className="card-head"><h2>偏好</h2></div>
      <div className="appearance-row">
        <span className="fl">外观</span>
        <div className="chips">
          {THEME_OPTIONS.map((o) => (
            <button key={o.v} type="button"
              className={"chip" + (theme === o.v ? " on" : "")}
              onClick={() => pickTheme(o.v)}>
              {o.label}
            </button>
          ))}
        </div>
      </div>
      {isDesktop() && (
        <label className="default-pick">
          <input type="checkbox" checked={autoCheck} onChange={(e) => toggle(e.target.checked)} />
          启动时自动检查更新
        </label>
      )}
    </div>
  );
}
