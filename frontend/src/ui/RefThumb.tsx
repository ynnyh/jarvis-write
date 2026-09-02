// 各制片工坊(drama/clips/birthday/series)共用的参考图缩略图。
// ref_images 全站同构:{kind:'upload'|'url', src, note}——上传的走鉴权读取端点
// 转 blob URL(<img src> 带不了 Authorization 头),外链直接 src。
// blob 生命周期与 ClipShoot/DramaPanel 同款:卸载/换图时 revoke,不留给 GC。
import { useEffect, useRef, useState } from "react";

export interface RefImageLike {
  kind: "upload" | "url";
  src: string;
  note?: string;
}

export function RefThumb({ image, loadBlob, alt = "参考图", footLeft, canEdit = true, onDelete }: {
  image: RefImageLike;
  /** 上传图 → blob URL(各工坊的鉴权读取端点不同,由调用方注入);kind=url 时不调用 */
  loadBlob?: () => Promise<string>;
  alt?: string;
  /** 底部左侧说明文字:系列工坊传备注,漫剧传「外链/已上传」;不传不显示 */
  footLeft?: React.ReactNode;
  /** false 隐藏底部操作行(只读展示) */
  canEdit?: boolean;
  onDelete?: () => void;
}) {
  const [url, setUrl] = useState(image.kind === "url" ? image.src : "");
  const [bad, setBad] = useState(false);
  // loadBlob 多为调用方内联箭头(每次渲染新身份),进 deps 会每渲染重取 blob;
  // 走 latest-ref:effect 只认 image 变化,取函数永远用最新一次渲染的。
  // ref 的同步放在 effect 里(渲染期写 ref 违反 react-hooks 规则);声明在前,
  // 挂载时先于取图 effect 执行,首次读取拿到的一定是初值
  const loadRef = useRef(loadBlob);
  useEffect(() => { loadRef.current = loadBlob; });

  useEffect(() => {
    if (image.kind === "url") { setUrl(image.src); setBad(false); return; }
    let alive = true;
    let revoke: string | null = null;
    loadRef.current?.()
      .then((u) => { if (alive) { revoke = u; setUrl(u); } else URL.revokeObjectURL(u); })
      .catch(() => { if (alive) setBad(true); });
    return () => { alive = false; if (revoke) URL.revokeObjectURL(revoke); };
  }, [image.kind, image.src]);

  return (
    <div className="ref-thumb">
      {url && !bad
        ? <img src={url} alt={image.note || alt} referrerPolicy="no-referrer" onError={() => setBad(true)} />
        : <div className="ref-thumb-bad">{bad ? "读取失败" : "加载…"}</div>}
      {canEdit && onDelete && (
        <div className="ref-thumb-foot">
          {footLeft}
          <button className="btn-sm" onClick={onDelete}>✕ 删</button>
        </div>
      )}
    </div>
  );
}
