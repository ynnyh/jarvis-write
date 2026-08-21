// 正文版本对比(「正文即界面」write 区):打开某章历史快照、选版对比、回退旧版。
// 回退联动「写回正文 + 刷新列表 + 触发一致性同步」,故 setCurrent/reload/triggerSync 由调用方注入
// (hook 不反向依赖父级);openVersions 供生成/重写完成后自动弹对比复用(见 useChapterGeneration)。
// 从 WritePanel 状态中枢抽出的自区 hook(拆分技术债)。
import { useCallback, useState } from "react";
import { api, ChapterDetail, ChapterVersionBrief, ChapterVersionDetail } from "../../api";
import { errMsg } from "../../pollJob";

interface Deps {
  setErr: (msg: string) => void;
  setCurrent: (c: ChapterDetail) => void;
  reload: () => Promise<void>;
  triggerSync: (num: number) => void;
}

export function useChapterVersions(pid: number, deps: Deps) {
  const { setErr, setCurrent, reload, triggerSync } = deps;
  // versionsFor=打开历史的章号,versions=该章快照列表,compareVer=选中对比的旧版全文
  const [versionsFor, setVersionsFor] = useState<number | null>(null);
  const [versions, setVersions] = useState<ChapterVersionBrief[] | null>(null);
  const [compareVer, setCompareVer] = useState<ChapterVersionDetail | null>(null);

  const closeVersions = useCallback(() => {
    setVersionsFor(null); setVersions(null); setCompareVer(null);
  }, []);

  // 打开某章历史版本。auto=true 时(重写刚完成)仅在确有旧版快照时才弹,并自动选中最新一版对比。
  // 返回是否真的打开了对比面板(确有旧版=true),供重写完成后决定是否提示"旧版都留着"。
  const openVersions = useCallback(async (n: number, auto = false): Promise<boolean> => {
    setErr("");
    try {
      const list = await api.listChapterVersions(pid, n);
      if (auto && !list.length) return false;  // 首次生成无旧版,不打扰
      setVersions(list); setVersionsFor(n); setCompareVer(null);
      if (auto && list.length) {
        setCompareVer(await api.getChapterVersion(pid, n, list[0].id));
      }
      return true;
    } catch (e) { setErr(errMsg(e)); return false; }
  }, [pid, setErr]);

  const selectVersion = useCallback(async (n: number, v: ChapterVersionBrief) => {
    setErr("");
    try { setCompareVer(await api.getChapterVersion(pid, n, v.id)); }
    catch (e) { setErr(errMsg(e)); }
  }, [pid, setErr]);

  // 回退到旧版:换回正文 → 自动同步一致性引擎。回退是整段替换(改动大)故不询问,
  // 但同步本身非阻塞,只显角标,不挡操作。
  const restoreVersion = useCallback(async (n: number, vid: number) => {
    setErr("");
    try {
      const updated = await api.restoreChapterVersion(pid, n, vid);
      setCurrent(updated);
      closeVersions();
      await reload();
      void triggerSync(n);
    } catch (e) {
      setErr(errMsg(e));
    }
  }, [pid, setErr, setCurrent, reload, triggerSync, closeVersions]);

  return { versionsFor, versions, compareVer, closeVersions, openVersions, selectVersion, restoreVersion };
}
