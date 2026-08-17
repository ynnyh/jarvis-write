// 目录抽屉(「正文即界面」P1,docs/10 §1):覆盖式抽屉,双端同一份,
// 替代旧永久左栏章节轨——章题按钮/Ctrl+B 唤出,Esc/点遮罩关闭。
// 复用 .m-overlay/.m-rail-drawer 样式族,桌面端补宽度与阴影(见 styles.css .catalog-drawer)。
import { useEffect } from "react";
import type { ReactNode } from "react";

interface Props {
  onClose: () => void;
  children: ReactNode;
}

export default function CatalogDrawer({ onClose, children }: Props) {
  // Esc 关闭(与遮罩点击等价)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="m-overlay" onClick={onClose}>
      <div className="m-rail-drawer catalog-drawer" onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}
