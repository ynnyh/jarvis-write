// 设置页(桌面/网页统一,SPA 内路由 /#/settings):
//   · 关于 & 更新:显示版本;桌面版可检查更新 → 静默下载 → 提示重启生效
//   · 账号:修改登录密码(仅 server 多用户模式)
//   · 应用锁:启动密码设/改/移除(仅 local 桌面单机模式)
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
  setUpdateProxy,
  getUpdateProxy,
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
      <AccountCard />
      <AppLockCard />
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

  // 更新代理:加载时回显已保存值;空串=直连
  const [proxy, setProxy] = useState("");
  const [proxyBusy, setProxyBusy] = useState(false);

  useEffect(() => {
    api.getVersion()
      .then((v) => setVersion(v.app_version && v.app_version !== "dev" ? v.app_version : v.commit))
      .catch(() => setVersion(""));
  }, []);

  useEffect(() => {
    if (!desktop) return;
    getUpdateProxy().then(setProxy).catch(() => {});
  }, [desktop]);

  async function saveProxy() {
    setProxyBusy(true);
    try {
      await setUpdateProxy(proxy.trim());
      toast.ok(proxy.trim() ? "更新代理已保存" : "已恢复直连", "下次检查/下载更新生效");
    } catch (e) {
      toast.err("保存失败", e instanceof Error ? e.message : String(e));
    } finally {
      setProxyBusy(false);
    }
  }

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

          <label className="fl">更新代理</label>
          <div className="input-row">
            <input
              type="text"
              value={proxy}
              onChange={(e) => setProxy(e.target.value)}
              placeholder="http://127.0.0.1:7890"
              spellCheck={false}
            />
            <button className="btn-sm" onClick={saveProxy} disabled={proxyBusy}>
              {proxyBusy && <span className="spin" />}保存
            </button>
          </div>
          <p className="card-desc" style={{ marginBottom: 0 }}>
            检查与下载更新走此代理;留空为直连;填错会自动回退直连。
          </p>
        </div>
      )}
    </div>
  );
}

// ============ 账号(修改密码) ============
// 仅 server 多用户模式渲染;local(桌面单机)免登录,后端也明确拒绝改密。
function AccountCard() {
  // null=尚未探测到运行模式(先不渲染,避免桌面版闪一下)
  const [isLocal, setIsLocal] = useState<boolean | null>(null);
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.mode()
      .then((m) => setIsLocal(m.is_local))
      .catch(() => setIsLocal(false)); // 探测失败按 server 处理
  }, []);

  // 即时校验:输入过程中即提示,不满足则禁用提交(长度规则与注册一致:至少 6 位)
  const fieldErr =
    newPw && newPw.length < 6 ? "新密码至少 6 位"
    : confirmPw && confirmPw !== newPw ? "两次输入的新密码不一致"
    : "";
  const canSubmit = !!oldPw && newPw.length >= 6 && confirmPw === newPw && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    try {
      await api.changePassword(oldPw, newPw);
      toast.ok("密码已修改", "下次登录请使用新密码");
      setOldPw(""); setNewPw(""); setConfirmPw("");
    } catch (e) {
      toast.err("修改失败", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (isLocal === null || isLocal) return null;

  return (
    <div className="card">
      <div className="card-head"><h2>账号</h2></div>
      <p className="card-desc">
        修改登录密码,需先验证旧密码。修改后已登录的其他设备不会被强制退出。
      </p>
      <form onSubmit={submit}>
        <label className="fl">旧密码</label>
        <input
          type="password"
          value={oldPw}
          autoComplete="current-password"
          onChange={(e) => setOldPw(e.target.value)}
        />
        <div className="fld-row">
          <div className="fld">
            <label className="fl">新密码</label>
            <input
              type="password"
              value={newPw}
              autoComplete="new-password"
              onChange={(e) => setNewPw(e.target.value)}
              placeholder="至少 6 位"
            />
          </div>
          <div className="fld">
            <label className="fl">确认新密码</label>
            <input
              type="password"
              value={confirmPw}
              autoComplete="new-password"
              onChange={(e) => setConfirmPw(e.target.value)}
            />
          </div>
        </div>
        {fieldErr && <div className="test-line err">{fieldErr}</div>}
        <div className="provider-actions">
          <button className="primary btn-sm" type="submit" disabled={!canSubmit}>
            {busy && <span className="spin" />}修改密码
          </button>
        </div>
      </form>
    </div>
  );
}

// ============ 应用锁(仅 local 桌面单机模式) ============
// 休闲锁:启动 app 需输密码才能进主界面,防家人/同事随手翻开,不抵御直接读数据文件。
// server 模式有自己的账号体系,不渲染(与上面的 AccountCard 互斥)。
function AppLockCard() {
  // null=非 local 模式或尚未探测到(不渲染)
  const [hasLock, setHasLock] = useState<boolean | null>(null);
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [removePw, setRemovePw] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.mode()
      .then((m) => { if (m.is_local) setHasLock(m.has_lock); })
      .catch(() => { /* 探测失败按 server 处理:不渲染 */ });
  }, []);

  // 即时校验:输入过程中即提示,不满足则禁用提交(长度规则与注册一致:至少 6 位)
  const fieldErr =
    newPw && newPw.length < 6 ? "锁密码至少 6 位"
    : confirmPw && confirmPw !== newPw ? "两次输入的密码不一致"
    : "";
  const canSubmit =
    (hasLock === false || !!oldPw) && newPw.length >= 6 && confirmPw === newPw && !busy;

  async function submitSet(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    try {
      await api.appLockSet(newPw, hasLock ? oldPw : undefined);
      toast.ok(hasLock ? "锁密码已修改" : "应用锁已开启", "下次启动应用时需输入密码");
      setHasLock(true);
      setOldPw(""); setNewPw(""); setConfirmPw("");
    } catch (e) {
      toast.err(hasLock ? "修改失败" : "设置失败", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function submitRemove(e: React.FormEvent) {
    e.preventDefault();
    if (!removePw || busy) return;
    setBusy(true);
    try {
      await api.appLockRemove(removePw);
      toast.ok("应用锁已移除", "下次启动应用不再需要密码");
      setHasLock(false);
      setRemovePw("");
    } catch (e) {
      toast.err("移除失败", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (hasLock === null) return null;

  return (
    <div className="card">
      <div className="card-head"><h2>应用锁</h2></div>
      <p className="card-desc">
        开启后每次启动应用都需输入密码才能进入,防止他人随手翻开你的作品。
        (防君子不防黑客:挡不住直接读取本机数据文件。)
      </p>

      <form onSubmit={submitSet}>
        {hasLock && (
          <>
            <label className="fl">旧密码</label>
            <input
              type="password"
              value={oldPw}
              autoComplete="current-password"
              onChange={(e) => setOldPw(e.target.value)}
            />
          </>
        )}
        <div className="fld-row">
          <div className="fld">
            <label className="fl">{hasLock ? "新锁密码" : "锁密码"}</label>
            <input
              type="password"
              value={newPw}
              autoComplete="new-password"
              onChange={(e) => setNewPw(e.target.value)}
              placeholder="至少 6 位"
            />
          </div>
          <div className="fld">
            <label className="fl">{hasLock ? "确认新密码" : "确认密码"}</label>
            <input
              type="password"
              value={confirmPw}
              autoComplete="new-password"
              onChange={(e) => setConfirmPw(e.target.value)}
            />
          </div>
        </div>
        {fieldErr && <div className="test-line err">{fieldErr}</div>}
        <div className="provider-actions">
          <button className="primary btn-sm" type="submit" disabled={!canSubmit}>
            {busy && <span className="spin" />}{hasLock ? "修改锁密码" : "设置应用锁"}
          </button>
        </div>
      </form>

      {hasLock && (
        <form onSubmit={submitRemove}>
          <label className="fl">移除应用锁(输入当前锁密码确认)</label>
          <input
            type="password"
            value={removePw}
            autoComplete="current-password"
            onChange={(e) => setRemovePw(e.target.value)}
          />
          <div className="provider-actions">
            <button className="btn-sm danger" type="submit" disabled={!removePw || busy}>
              移除应用锁
            </button>
          </div>
          <p className="card-desc">
            忘记锁密码?在锁屏页点「忘记密码?」可重置(将移除应用锁,数据不受影响)。
          </p>
        </form>
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
