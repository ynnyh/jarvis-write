// 关于 & 更新卡:显示版本;桌面版检查更新 → 静默下载 → 提示重启生效。拆自 SettingsPage.tsx。
import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import {
  isDesktop,
  checkUpdate,
  downloadAndInstallUpdate,
  restartApp,
  onUpdateProgress,
  setUpdateProxy,
  getUpdateProxy,
  UpdateInfo,
} from "../../desktop";
import { toast } from "../../ui/Toaster";
import { confirmDialog } from "../../ui/ConfirmDialog";

type UpdateStage = "idle" | "checking" | "available" | "downloading" | "ready" | "latest" | "error";

export function AboutUpdateCard() {
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
    if (ok) restartApp().catch((e) => toast.err("重启失败", e instanceof Error ? e.message : String(e)));
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
                    <div key={i}>{l.replace(/^-\s*/, "• ") || " "}</div>
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
