// write/DualTrackEditor.tsx —— 同文双轨(评价第 5 条,2026-09)
// 左栏 = 当前定稿(只读参照,Paragraphs 渲染);右栏 = CM6 编辑稿(空白起笔或一键载入定稿)。
// 实时逐段差异统计复用 diffParagraphs(润色对照卡同款段落级 LCS 对齐):
//   改动/新增/删除段数 + 字数迁移,改到哪一目了然,不再靠肉眼对两栏。
// 写回与自由改稿同链路:PUT /chapters/{n}/content → onSaved → sync-ask 询问条。
// 右栏与定稿完全一致时写回禁用语义(按钮给出提示),避免无意义版本快照。
// 编辑器内核在 useChapterEditor,与自由改稿共用;本组件按 chapter_number 加 key,切章即重建。
import { useEffect, useRef, useState } from "react";
import { api, ChapterDetail } from "../../api";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { Paragraphs } from "../../components/reader/paragraphs";
import { diffParagraphs } from "./charDiff";
import { useChapterEditor } from "./useChapterEditor";

interface Props {
  pid: number;
  chapter: ChapterDetail;
  genBlocked: boolean;
  genHint: string;
  onSaved: (updated: ChapterDetail) => void;
  onSyncAsk: (num: number) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onExit: () => void;
}

interface DualStats {
  leftParas: number; rightParas: number;
  same: number; changed: number; added: number; removed: number;
  leftChars: number; rightChars: number;
}

function countStats(left: string, right: string): DualStats {
  const diffs = diffParagraphs(left, right);
  const s: DualStats = {
    leftParas: 0, rightParas: 0, same: 0, changed: 0, added: 0, removed: 0,
    leftChars: left.replace(/\s/g, "").length, rightChars: right.replace(/\s/g, "").length,
  };
  for (const d of diffs) {
    if (d.oldIdx !== null) s.leftParas++;
    if (d.newIdx !== null) s.rightParas++;
    if (d.status === "same") s.same++;
    else if (d.status === "changed") s.changed++;
    else if (d.status === "added") s.added++;
    else if (d.status === "removed") s.removed++;
  }
  return s;
}

export default function DualTrackEditor({
  pid, chapter, genBlocked, genHint, onSaved, onSyncAsk, onDirtyChange, onExit,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [saving, setSaving] = useState(false);
  const [stats, setStats] = useState<DualStats | null>(null);
  // 左栏参照:进入本模式那一刻的定稿快照(写回成功后随之更新,对照的是最新定稿)
  const initialLeft = chapter.final_content || chapter.draft_content || "";
  const leftRef = useRef(initialLeft);
  const [leftText, setLeftText] = useState(initialLeft);
  const statsTimer = useRef<number | undefined>(undefined);

  const ed = useChapterEditor(hostRef, {
    initialDoc: "", // 右栏空白起笔:对照左稿写新版;要基于定稿改,点「载入当前定稿」
    genBlocked,
    onDirtyChange,
    onDocChanged: (text) => {
      window.clearTimeout(statsTimer.current);
      statsTimer.current = window.setTimeout(() => setStats(countStats(leftRef.current, text)), 200);
    },
  });

  async function save() {
    const content = ed.getDoc();
    if (!content.trim()) { toast.err("写回失败", "右栏还是空的"); return; }
    if (content === leftRef.current) { toast.info("右栏与定稿相同", "没有要写回的改动;在右栏改点东西再写回。"); return; }
    setSaving(true);
    try {
      const updated = await api.editChapterContent(pid, chapter.chapter_number, content);
      ed.markSaved(content);
      leftRef.current = content;
      setLeftText(content);
      // 左稿换了,统计基准同步:对最新定稿应显示「无差异」(右栏就是新定稿)
      window.clearTimeout(statsTimer.current);
      setStats(countStats(content, ed.getDoc()));
      onSaved(updated);
      onSyncAsk(chapter.chapter_number);
      toast.ok("已写回定稿", "旧定稿自动留了版本快照,可随时在历史版本回滚");
    } catch (e) { toast.err("写回失败", errMsg(e)); } finally { setSaving(false); }
  }
  useEffect(() => { ed.setSaveHandler(save); });

  // 定稿在别处更新(重写任务收尾、其他窗口写回)时跟进左栏参照:
  // 右栏无未写回改动才刷新,有改动则保持进入时的快照,差异统计的基准不被中途换掉。
  useEffect(() => {
    const latest = chapter.final_content || chapter.draft_content || "";
    if (!ed.dirty && latest !== leftRef.current) {
      leftRef.current = latest;
      setLeftText(latest);
      setStats(countStats(latest, ed.getDoc()));
    }
  });

  // 卸载时清掉挂起的统计防抖计时器(挂载一次;不能挂在逐帧 effect 里,会误清在途防抖)
  useEffect(() => () => window.clearTimeout(statsTimer.current), []);

  async function exit() {
    if (ed.dirty && !await confirmDialog({
      title: "有还没写回的修改",
      body: "退出双轨对照会丢掉右栏没写回的内容。",
      confirmText: "丢弃修改", danger: true,
    })) return;
    onDirtyChange?.(false);
    onExit();
  }

  async function loadLeft() {
    if (ed.dirty && !await confirmDialog({
      title: "右栏有未写回的内容",
      body: "载入定稿会整体替换右栏当前内容。",
      confirmText: "替换右栏", danger: true,
    })) return;
    ed.setDoc(leftRef.current);
    setStats(null); // 与基准一致,无差异
  }

  const noDiff = stats && stats.changed === 0 && stats.added === 0 && stats.removed === 0;

  return (
    <div className="dual-track">
      <div className="free-write-head">
        <b>⇔ 同文双轨</b>
        <span className="muted">
          {stats
            ? <>左稿 {stats.leftParas} 段 · 右稿 {stats.rightParas} 段 ·
               改动 {stats.changed} / 新增 {stats.added} / 删除 {stats.removed} 段 ·
               字数 {stats.leftChars} → {stats.rightChars}</>
            : "右栏空着:对照左稿写新版,或点「载入当前定稿」在定稿基础上改"}
          {ed.dirty ? " · 未写回" : ""}
        </span>
        <span className="grow" />
        <button className="btn-sm" disabled={genBlocked} title={genBlocked ? genHint : "把左栏定稿整体灌入右栏,基于它改"}
          onClick={() => void loadLeft()}>载入当前定稿</button>
        <button className="btn-sm" onClick={() => void exit()}
          title={ed.dirty ? "有未写回内容,退出前会再确认" : "回到段落点选界面"}>返回界面模式</button>
        <button className="primary btn-sm" disabled={saving || !ed.dirty || genBlocked}
          title={genBlocked ? genHint : "把右栏写回本章定稿(Ctrl+S)"}
          onClick={() => void save()}>
          {saving ? "写回中…" : "写回定稿"}
        </button>
      </div>
      {genBlocked && <div className="notice notice-warn mb-2">{genHint || "有任务在跑,右栏暂时只读。"}</div>}
      <div className="dual-track-panes">
        <div className="dual-left">
          <div className="dual-pane-title">📘 定稿(参照)</div>
          <div className="dual-left-body">
            <Paragraphs text={leftText} />
          </div>
        </div>
        <div className="dual-right">
          <div className="dual-pane-title">✏️ 新稿(可编辑){noDiff && <span className="badge">与定稿无差异</span>}</div>
          <div ref={hostRef} className={"free-write-host" + (genBlocked ? " is-locked" : "")} />
        </div>
      </div>
      <p className="hint">
        Ctrl+S 写回 · Ctrl+F 搜索替换 · Ctrl+Z 撤销;左栏定稿在写回成功后更新。
        想让 AI 出候选稿再逐段采纳,用「整章优化」;这里适合对照定稿亲手大改。
      </p>
    </div>
  );
}
