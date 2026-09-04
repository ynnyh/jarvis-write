import { useState } from "react";
import type { PasteSet } from "../../dramaApi";
import { CopyBtn, selectAll } from "../../ui/copy";
import { PASTE_PLATFORM_KEY } from "./dramaShared";

/** 一键粘贴框:按用户的生图站给「整段能直接粘」的版本。
 *
 *  为什么不让用户自己拼:我们出的是三轨(中文/英文/负面),而生图站长相不一——
 *  只有一个描述框的站(GPT-image / 豆包 / 通义)没处放负面词,照原样复制等于把
 *  负面词丢了。拼装规则在后端(paste.py),导出手册用的是同一份,不会两边跑偏。
 *
 *  生视频那套粘贴版结构完全相同(video.py),所以这个组件同时伺候两边,
 *  只是换个平台偏好键与标题。
 */
export function PasteBox({ paste, stale, rows = 5, storeKey = PASTE_PLATFORM_KEY, title = "一键粘贴 · 你用的生图站" }: {
  paste?: PasteSet | null; stale?: boolean; rows?: number; storeKey?: string; title?: string;
}) {
  const [plat, setPlat] = useState(
    () => localStorage.getItem(storeKey) || "oneframe",
  );
  if (!paste) return null;
  const keys = Object.keys(paste);
  if (!keys.length) return null;
  const key = paste[plat] ? plat : keys[0];
  const v = paste[key];
  if (!v.main.trim()) return null;
  return (
    <div className="media-field paste-box">
      <div className="card-head mb-2">
        <span className="muted">{title}</span>
        <select value={key} onChange={(e) => {
          setPlat(e.target.value);
          localStorage.setItem(storeKey, e.target.value);
        }}>
          {keys.map((k) => <option key={k} value={k}>{paste[k].label}</option>)}
        </select>
        <span className="grow" />
        <CopyBtn text={v.main} label="复制整段" />
        {v.negative.trim() && <CopyBtn text={v.negative} label="复制负面词" />}
      </div>
      <textarea rows={rows} readOnly value={v.main} onFocus={selectAll} />
      {v.negative.trim() && (
        <>
          <div className="card-head mb-2 mt-2"><span className="muted">负面词(粘到负面词框)</span></div>
          <textarea rows={2} readOnly value={v.negative} onFocus={selectAll} />
        </>
      )}
      <p className="hint">{v.hint}</p>
      {stale && <p className="hint">提示词改过还没保存——这里是已保存版本,点「保存」后同步。</p>}
    </div>
  );
}


