// ui/CommandPalette.tsx — Ctrl+K 命令面板(仅桌面宽屏挂载,见 ProjectPage),输入即过滤:
//   · 纯数字 → 「跳到第 N 章」(存在与否都显示,不存在则禁用态)
//   · 章名模糊匹配章节列表(小字显示状态)
//   · 动作词匹配(生成/重写/润色/校对/评分/沉浸/深色…),选中经 dispatchAction 执行——
//     act 类由 WritePanel 写 URL,章级动作作用于当前 ch(见 ui/actions.ts)
// 键盘:↑↓ 选择、Enter 执行、Esc 关闭;鼠标 hover/点击同样可用。
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useChapters, useOutlines } from "../hooks/queries";
import { STATUS_CN } from "../components/Reader";
import { AppAction, dispatchAction } from "./actions";

interface PaletteItem {
  key: string;
  label: string;
  sub?: string;
  disabled?: boolean;
  run: () => void;
}

// 动作词表:words 任一包含输入即命中;action 走统一 dispatch,path 直接导航(book 页签类)
interface ActionEntry { words: string[]; label: string; sub?: string; action?: AppAction; path?: string }
const ACTION_ENTRIES: ActionEntry[] = [
  { words: ["生成"], label: "生成本章", action: "generate", sub: "Ctrl+Enter" },
  { words: ["重写"], label: "和 AI 梳理本章修改意见", action: "revise" },
  { words: ["润色"], label: "整章优化(和 AI 梳理)", action: "polish" },
  { words: ["校对"], label: "校对本章", action: "proofread" },
  { words: ["评分", "审核"], label: "主编评分", action: "review" },
  { words: ["版本", "历史"], label: "历史版本", action: "versions" },
  { words: ["连写", "队列"], label: "连写队列", action: "queue" },
  { words: ["沉浸"], label: "沉浸模式", action: "immersive", sub: "F11" },
  { words: ["目录", "章节"], label: "开合目录抽屉", action: "toggle-rail", sub: "Ctrl+B" },
  { words: ["阅读", "对照"], label: "打开对照阅读窗", action: "open-read-window" },
  { words: ["开书"], label: "去开书区", action: "goto-setup" },
  { words: ["写作"], label: "去写作区", action: "goto-write" },
  { words: ["看板", "全书"], label: "去全书看板", action: "goto-book" },
  { words: ["投稿"], label: "去投稿", path: "book?tab=publish" },
  { words: ["体检"], label: "全书体检", path: "book?tab=audit" },
  { words: ["翻新"], label: "全书翻新", path: "book?tab=refresh" },
  { words: ["设置"], label: "书级设置", action: "goto-settings" },
  { words: ["帮助", "指南"], label: "使用指南", action: "goto-help" },
  { words: ["导出txt", "txt"], label: "导出 TXT", action: "export-txt" },
  { words: ["导出epub", "epub"], label: "导出 EPUB", action: "export-epub" },
  { words: ["深色"], label: "深色主题", action: "theme-dark" },
  { words: ["浅色"], label: "浅色主题", action: "theme-light" },
  { words: ["自动", "跟随系统"], label: "跟随系统主题", action: "theme-auto" },
];

interface Props { pid: number; onClose: () => void }

export default function CommandPalette({ pid, onClose }: Props) {
  const nav = useNavigate();
  const { data: outlines = [] } = useOutlines(pid);
  const { data: chapters = [] } = useChapters(pid);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const byNum = useMemo(() => new Map(chapters.map((c) => [c.chapter_number, c])), [chapters]);

  // 输入 → 结果列表:数字跳章 → 章名模糊 → 动作词
  const items: PaletteItem[] = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return [];
    const out: PaletteItem[] = [];
    const gotoChapter = (n: number) => () => nav(`/project/${pid}/write?ch=${n}`);
    if (/^\d+$/.test(query)) {
      const n = Number(query);
      const exists = outlines.some((o) => o.chapter_number === n);
      out.push({
        key: "jump",
        label: `跳到第 ${n} 章`,
        sub: exists ? undefined : "章节不存在",
        disabled: !exists,
        run: gotoChapter(n),
      });
    }
    outlines
      .filter((o) => o.title.toLowerCase().includes(query))
      .slice(0, 8)
      .forEach((o) => {
        const c = byNum.get(o.chapter_number);
        out.push({
          key: `ch-${o.chapter_number}`,
          label: `第${o.chapter_number}章《${o.title}》`,
          sub: c ? (STATUS_CN[c.status] ?? c.status) : "未生成",
          run: gotoChapter(o.chapter_number),
        });
      });
    ACTION_ENTRIES
      .filter((e) => e.words.some((w) => w.includes(query)))
      .forEach((e) => {
        out.push({
          key: `act-${e.label}`,
          label: e.label,
          sub: e.sub,
          run: () => {
            if (e.action) dispatchAction(e.action);
            else if (e.path) nav(`/project/${pid}/${e.path}`);
          },
        });
      });
    return out;
  }, [q, outlines, byNum, nav, pid]);

  // 输入变化时选择回到第一项;结果少于上次选择时收敛
  useEffect(() => { setSel(0); }, [q]);
  const selSafe = items.length ? Math.min(sel, items.length - 1) : -1;

  // ↑↓ 移动时保证选中项可见
  useEffect(() => {
    if (selSafe < 0) return;
    listRef.current
      ?.querySelectorAll(".cmdk-item")[selSafe]
      ?.scrollIntoView({ block: "nearest" });
  }, [selSafe]);

  function runItem(it: PaletteItem | undefined) {
    if (!it || it.disabled) return;
    it.run();
    onClose();
  }

  function onInputKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (items.length) setSel((selSafe + 1) % items.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (items.length) setSel((selSafe - 1 + items.length) % items.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      runItem(items[selSafe]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  }

  return (
    <div className="cmdk-overlay" onClick={onClose}>
      <div className="cmdk-panel" onClick={(e) => e.stopPropagation()}>
        <input
          className="cmdk-input"
          autoFocus
          value={q}
          placeholder="跳章 / 找章 / 执行动作…"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onInputKeyDown}
        />
        {items.length > 0 ? (
          <div className="cmdk-list" ref={listRef}>
            {items.map((it, i) => (
              <button
                key={it.key}
                type="button"
                className={"cmdk-item" + (i === selSafe ? " on" : "") + (it.disabled ? " disabled" : "")}
                onMouseEnter={() => setSel(i)}
                onClick={() => runItem(it)}
              >
                <span className="cmdk-item-label">{it.label}</span>
                {it.sub && <span className="cmdk-item-sub">{it.sub}</span>}
              </button>
            ))}
          </div>
        ) : (
          <div className="cmdk-hint">
            {q.trim()
              ? "没有匹配的章节或动作。"
              : "输入章号直接跳章;输入章名关键词找章;输入动作词执行:生成 / 重写 / 润色 / 校对 / 评分 / 沉浸 / 深色 …"}
          </div>
        )}
      </div>
    </div>
  );
}
