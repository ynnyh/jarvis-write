// 更新提醒:轮询后端 /api/version,发现部署的 commit 与本地 bundle 烤入的不一致
// (说明浏览器还是旧缓存)就在顶部提示刷新,并可弹窗查看本次更新内容。
// 本地开发两边都是 "dev",自动跳过,不打扰。
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { api } from "../api";
import { APP_COMMIT } from "../version";

interface VersionInfo {
  commit: string;
  changelog: { title: string; body: string };
}

const POLL_MS = 5 * 60 * 1000; // 每 5 分钟查一次,部署后无需用户手动刷新即可感知

export default function UpdateBanner() {
  const [update, setUpdate] = useState<VersionInfo | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [showLog, setShowLog] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const check = () => {
      api.getVersion()
        .then((v) => {
          if (cancelled) return;
          // 两边都得是真实 commit 且不同才算有更新;"dev"/空一律跳过
          if (
            v.commit && v.commit !== "dev" &&
            APP_COMMIT !== "dev" && v.commit !== APP_COMMIT
          ) {
            setUpdate(v);
          }
        })
        .catch(() => { /* 查不到就不提示,绝不打扰 */ });
    };
    check();
    const t = setInterval(check, POLL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (!update || dismissed) return null;

  return (
    <>
      <div className="update-banner">
        <span>发现新版本,刷新页面即可获取新功能。</span>
        <button className="primary btn-sm" onClick={() => window.location.reload()}>
          刷新获取新版
        </button>
        {update.changelog.title && (
          <button className="btn-sm" onClick={() => setShowLog(true)}>查看更新内容</button>
        )}
        <button className="btn-sm" onClick={() => setDismissed(true)}>稍后</button>
      </div>

      <Dialog.Root open={showLog} onOpenChange={setShowLog}>
        <Dialog.Portal>
          <Dialog.Overlay className="dlg-overlay" />
          <Dialog.Content className="dlg-content">
            <Dialog.Title className="dlg-title">
              更新内容{update.changelog.title ? ` · ${update.changelog.title}` : ""}
            </Dialog.Title>
            <Dialog.Description className="dlg-body update-log">
              {update.changelog.body.split("\n").map((line, i) => (
                <div key={i}>{line.replace(/^- /, "• ") || "\u00a0"}</div>
              ))}
            </Dialog.Description>
            <div className="dlg-actions">
              <button onClick={() => setShowLog(false)}>关闭</button>
              <button className="primary" onClick={() => window.location.reload()}>
                刷新获取新版
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
