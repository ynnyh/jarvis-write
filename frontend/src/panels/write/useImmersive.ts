// 沉浸模式(「正文即界面」write 区):F11/命令面板/菜单切换,class 挂在 write-zone,
// 外层 chrome 由 CSS :has 隐藏;开启时监听 Esc 退出(F11 等快捷键在 useDesktopHotkeys 注册)。
// 从 WritePanel 状态中枢抽出的自区 hook(拆分技术债,让壳回归编排+布局)。
import { useCallback, useEffect, useState } from "react";

export function useImmersive() {
  const [immersive, setImmersive] = useState(false);
  useEffect(() => {
    if (!immersive) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setImmersive(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [immersive]);
  const toggleImmersive = useCallback(() => setImmersive((v) => !v), []);
  return { immersive, toggleImmersive };
}
