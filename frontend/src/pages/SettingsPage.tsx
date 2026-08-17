// 设置页(桌面/网页统一,SPA 内路由 /#/settings):
//   · 关于 & 更新:显示版本;桌面版可检查更新 → 静默下载 → 提示重启生效
//   · 账号:修改登录密码(仅 server 多用户模式)
//   · 应用锁:启动密码设/改/移除(仅 local 桌面单机模式)
//   · 模型设置:每用户多套命名配置,增删改测 + 一键切换默认/快档(取代旧的独立 settings.html)
//   · 偏好:外观(跟随系统/浅色/深色,全端生效)+ 启动时自动检查更新开关、
//     关闭窗口时最小化到托盘开关(后两者仅桌面)
// 桌面能力经 desktop.ts 优雅降级:非桌面(网页)隐藏更新相关 UI。
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ProviderConfigOut } from "../api";
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
import { getCloseToTrayPref, setCloseToTrayPref } from "../ui/CloseGuard";
import { toast } from "../ui/Toaster";
import { confirmDialog } from "../ui/ConfirmDialog";

// 模型接入(cc-switch 风格):后端有三类 wire 协议——openai-compatible / anthropic /
// gemini,存库的 interface_format 用这三个大类之一;deepseek/openai 是 openai-compatible
// 的历史别名,只在回显存量配置时并入通用卡。

// 协议大类:新建配置只在这三类里选。desc/baseUrl/model 与后端 settings.py 的 _PRESETS 对齐。
interface ProtocolCategory {
  key: string; label: string; desc: string; baseUrl: string; model: string;
}
const PROTOCOL_CATEGORIES: ProtocolCategory[] = [
  {
    key: "openai-compatible", label: "OpenAI 兼容",
    desc: "最通用的一张卡:OpenAI 官方、各类中转站(token 站 / API 超市)、本地 Ollama,以及卖 DeepSeek / Kimi / 通义 / GLM 等模型的服务——只要是 OpenAI /chat/completions 协议都选它,填对 Base URL 和模型名即可。",
    baseUrl: "https://api.openai.com/v1", model: "gpt-4o",
  },
  {
    key: "anthropic", label: "Anthropic (Claude)",
    desc: "Claude 原生 Messages API。用官方或支持 Anthropic 协议的渠道选它;卖 Claude 的 OpenAI 兼容中转站请改用「OpenAI 兼容」卡。",
    baseUrl: "https://api.anthropic.com", model: "claude-sonnet-4-20250514",
  },
  {
    key: "gemini", label: "Gemini",
    desc: "仅 Google 官方原生 API。卖 Gemini 模型的中转站请走「OpenAI 兼容」卡。",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta", model: "gemini-2.0-flash",
  },
];
const CATEGORY_BY_KEY: Record<string, ProtocolCategory> = Object.fromEntries(
  PROTOCOL_CATEGORIES.map((c) => [c.key, c]));

// 快捷预设:点一下把「大类 + Base URL + 模型名」一并填好(纯前端便利,存库仍是大类 key)。
// 用户通常只需再填 API Key;想接别的厂商,选对应大类手填地址即可。
interface QuickPreset { label: string; category: string; baseUrl: string; model: string; }
const QUICK_PRESETS: QuickPreset[] = [
  { label: "DeepSeek", category: "openai-compatible", baseUrl: "https://api.deepseek.com", model: "deepseek-chat" },
  { label: "OpenAI", category: "openai-compatible", baseUrl: "https://api.openai.com/v1", model: "gpt-4o" },
  { label: "Kimi", category: "openai-compatible", baseUrl: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  { label: "通义千问", category: "openai-compatible", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { label: "智谱 GLM", category: "openai-compatible", baseUrl: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-plus" },
  { label: "Claude", category: "anthropic", baseUrl: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" },
  { label: "Gemini", category: "gemini", baseUrl: "https://generativelanguage.googleapis.com/v1beta", model: "gemini-2.0-flash" },
];

// 徽标文案:覆盖所有可能存库的 interface_format(含历史别名),让存量配置也显示合理标签。
const FORMAT_LABEL: Record<string, string> = {
  "openai-compatible": "OpenAI 兼容",
  anthropic: "Anthropic (Claude)",
  gemini: "Gemini",
  deepseek: "OpenAI 兼容",
  openai: "OpenAI 兼容",
};

// 历史别名归一到大类 key:存量 deepseek/openai 配置在表单里并入「OpenAI 兼容」大类。
function normalizeCategory(fmt: string): string {
  return fmt === "deepseek" || fmt === "openai" ? "openai-compatible" : fmt;
}

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
    // 订阅进度。单调防御:正常事件递增;旧版本曾有两路下载并发、双计数器交错
    // 推送导致进度条来回跳(闪烁)。Rust 侧已加下载互斥,这里再兜一层——回退
    // 超过 1MB 的事件视为异源计数直接忽略(仅放行下载重开时的近 0 起点)。
    unlistenRef.current = await onUpdateProgress((done, total) =>
      setProgress((prev) =>
        done < prev.done && done > 1024 * 1024 ? prev : { done, total },
      ));
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
              <span className="muted">{pct !== null ? `正在下载并安装… ${pct}%` : "正在连接更新服务器…"}</span>
              <div className="pbar">
                {pct !== null
                  ? <div className="pbar-fill" style={{ width: `${pct}%` }} />
                  : <div className="pbar-fill indet" />}
              </div>
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
// cc-switch 风格:每用户多套命名配置,可增删改、一键切换默认/快档(各全用户唯一)。
function ProvidersCard() {
  const [list, setList] = useState<ProviderConfigOut[] | null>(null);
  const [err, setErr] = useState("");
  // adding=展开添加表单;editingId=正在行内编辑的配置 id
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);

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

// 单套配置的展示卡片:名称 + 协议/默认/快档徽标 + 打码 key + base_url/model + 操作按钮
function ProviderRow({ p, onChanged, onEdit }: {
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
      setTestMsg({ ok: false, text: `✗ ${e instanceof Error ? e.message : String(e)}` });
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
      toast.err("删除失败", e instanceof Error ? e.message : String(e));
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
      toast.err("设置失败", e instanceof Error ? e.message : String(e));
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

// 添加/编辑共用的表单:先选协议预设(预填 base_url/model 占位),再填名称/Key/地址/模型名;
// 高级项(timeout、max_tokens,0=跟随全局)折叠收起。编辑时 key 留空表示不修改。
function ProviderForm({ editing, onSaved, onCancel }: {
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
      if (editing) await api.updateProvider(editing.id, body);
      else await api.createProvider(body);
      toast.ok(editing ? `「${body.name || editing.name}」已保存` : "配置已添加");
      onSaved();
    } catch (e) {
      toast.err("保存失败", e instanceof Error ? e.message : String(e));
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

// ============ 偏好 ============
// 外观选择全端生效(写 localStorage 并即改 <html data-theme>);
// 自动更新 / 关闭进托盘开关仅桌面有意义(网页随访问自动最新、没有窗口 X),非桌面不渲染。
const THEME_OPTIONS: { v: ThemePref; label: string }[] = [
  { v: "auto", label: "跟随系统" },
  { v: "light", label: "浅色" },
  { v: "dark", label: "深色" },
];

function PreferencesCard() {
  const [theme, setTheme] = useState<ThemePref>(getThemePref);
  const [autoCheck, setAutoCheck] = useState(getAutoCheck());
  const [closeToTray, setCloseToTray] = useState(getCloseToTrayPref());

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
      {isDesktop() && (
        <label className="default-pick">
          <input
            type="checkbox"
            checked={closeToTray}
            onChange={(e) => { setCloseToTrayPref(e.target.checked); setCloseToTray(e.target.checked); }}
          />
          关闭窗口时最小化到托盘
        </label>
      )}
      {isDesktop() && closeToTray && (
        <p className="card-desc" style={{ marginBottom: 0 }}>
          开启后点 X 不询问、直接进托盘;有后台任务运行时仍会先询问,避免误杀任务。
        </p>
      )}
    </div>
  );
}
