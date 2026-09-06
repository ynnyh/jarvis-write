// 公开分享弹层:为整本书或单章创建只读链接,复制给读者;已建链接可撤销。
// 链接 = 站点 + /share/{token}(免登录只读,牛皮纸阅读页)。
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { sharesApi, ShareInfo } from "../sharesApi";
import { errMsg } from "../pollJob";
import { toast } from "./Toaster";
import { copyOrPrompt } from "./copy";

interface Props { pid: number; onClose: () => void; }

export default function ShareDialog({ pid, onClose }: Props) {
  const [rows, setRows] = useState<ShareInfo[] | null>(null);
  const [scope, setScope] = useState<"book" | "chapter">("book");
  const [chapterNum, setChapterNum] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    sharesApi.list(pid).then(setRows).catch((e) => setErr(errMsg(e)));
  }, [pid]);

  async function create() {
    setErr(""); setBusy(true);
    try {
      const ch = scope === "chapter" ? Number(chapterNum) : undefined;
      const info = await sharesApi.create(pid, scope, ch);
      setRows((r) => [info, ...(r ?? [])] as ShareInfo[]);
      setScope("book"); setChapterNum("");
      copyLink(info.token);
    } catch (e) {
      setErr(errMsg(e));
    } finally { setBusy(false); }
  }

  async function copyLink(token: string) {
    const url = `${location.origin}${location.pathname}#/share/${token}`;
    const ok = await copyOrPrompt(url, "分享链接");
    if (ok) toast.ok("链接已复制", "发给任何人即可免登录阅读;在下方可随时撤销。");
    else toast.info("请手动复制", url);
  }

  async function revoke(shareId: number) {
    try {
      await sharesApi.revoke(pid, shareId);
      setRows((r) => r?.map((x) => x.id === shareId ? { ...x, revoked: true } : x) ?? r);
      toast.ok("已撤销", "链接立即失效,读者打开会看到 404。");
    } catch (e) {
      toast.err("撤销失败", String(e instanceof Error ? e.message : e));
    }
  }

  return (
    <Dialog.Root open onOpenChange={(o) => { if (!o) onClose(); }}>
      <Dialog.Portal>
      <Dialog.Overlay className="dlg-overlay" />
      <Dialog.Content className="dlg-content share-dlg" onEscapeKeyDown={() => onClose()}>
        <Dialog.Title className="dlg-title">公开分享(免登录只读)</Dialog.Title>
        <div className="card-desc mt-2">
          生成一条公开链接,任何人点开即可在牛皮纸阅读页里读这本书——**没有 AI、没有你的 key,只读正文**;可随时撤销。
        </div>

        <div className="share-create mt-3">
          <div className="title-chips">
            <button type="button" className={"title-chip" + (scope === "book" ? " on" : "")}
              onClick={() => setScope("book")}>整本书</button>
            <button type="button" className={"title-chip" + (scope === "chapter" ? " on" : "")}
              onClick={() => setScope("chapter")}>单章</button>
          </div>
          {scope === "chapter" && (
            <input type="number" min={1} className="mt-2" value={chapterNum}
              onChange={(e) => setChapterNum(e.target.value)}
              placeholder="章号(如 1)" style={{ maxWidth: 160 }} />
          )}
          <div className="mt-2">
            <button className="primary btn-sm" disabled={busy || (scope === "chapter" && !Number(chapterNum))}
              onClick={create}>
              {busy && <span className="spin" />}生成分享链接
            </button>
          </div>
        </div>
        {err && <div className="notice notice-err mt-2">{err}</div>}

        <div className="share-list mt-3">
          {rows === null && <div className="muted"><span className="spin" />加载中…</div>}
          {rows !== null && rows.length === 0 && <div className="muted">还没有创建过分享链接。</div>}
          {rows?.map((r) => (
            <div key={r.id} className={"share-row" + (r.revoked ? " revoked" : "")}>
              <span className="share-scope">{r.scope === "book" ? "整本书" : `第${r.chapter_number}章`}</span>
              <span className="muted">{r.revoked ? "已撤销" : `${r.view_count} 次浏览`}</span>
              {!r.revoked && (
                <>
                  <button className="btn-sm" onClick={() => copyLink(r.token)}>复制链接</button>
                  <button className="btn-sm danger" onClick={() => revoke(r.id)}>撤销</button>
                </>
              )}
            </div>
          ))}
        </div>
      </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
