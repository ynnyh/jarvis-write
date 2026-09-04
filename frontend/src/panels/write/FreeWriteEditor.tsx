// write/FreeWriteEditor.tsx —— 自由改稿模式(编辑器升级档,2026-09)
// CodeMirror 6 整章编辑:给「想大段手写/整章重排」的作者一个专业编辑器;
// 段落点选气泡(Prose)仍是默认界面,本模式由内容头「自由改稿」按钮进入(懒加载,不占主包)。
// 写回与段落手改同一条链路:PUT /chapters/{n}/content → onSaved → sync-ask 询问条;
// 脏标记经 onDirtyChange 上报,父级切章/退出前弹「丢弃修改」确认(与 Prose 同语义)。
// 编辑器内核(换行/历史/搜索/任务锁)在 useChapterEditor,与双轨对照共用。
import { useEffect, useRef, useState } from "react";
import { api, ChapterDetail } from "../../api";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { useChapterEditor } from "./useChapterEditor";

interface Props {
  pid: number;
  chapter: ChapterDetail;
  // 任务锁:有生成/重写任务在跑时编辑器只读(与 Prose 同一规则)
  genBlocked: boolean;
  genHint: string;
  // 保存成功:父级更新 qk.chapter 缓存并刷新章节列表
  onSaved: (updated: ChapterDetail) => void;
  // 保存后:父级弹 sync-ask 询问条(整章手改更可能动到事实,同步询问默认给)
  onSyncAsk: (num: number) => void;
  // 脏标记上报:父级切章前据此弹「丢弃修改」确认
  onDirtyChange?: (dirty: boolean) => void;
  // 退出自由改稿,回到界面模式(脏时内部先确认)
  onExit: () => void;
}

export default function FreeWriteEditor({
  pid, chapter, genBlocked, genHint, onSaved, onSyncAsk, onDirtyChange, onExit,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [saving, setSaving] = useState(false);
  const ed = useChapterEditor(hostRef, {
    initialDoc: chapter.final_content || chapter.draft_content || "",
    genBlocked,
    onDirtyChange,
  });

  async function save() {
    const content = ed.getDoc();
    if (!content.trim()) { toast.err("保存失败", "正文不能是空的"); return; }
    setSaving(true);
    try {
      const updated = await api.editChapterContent(pid, chapter.chapter_number, content);
      ed.markSaved(content);
      onSaved(updated);
      onSyncAsk(chapter.chapter_number);
      toast.ok("本章已保存", "段落结构(空行分段)保持你编辑后的样子");
    } catch (e) { toast.err("保存失败", errMsg(e)); } finally { setSaving(false); }
  }
  // Ctrl+S 键位在编辑器建好后不可换闭包,经 hook 的 ref 每次渲染后指向最新 save
  useEffect(() => { ed.setSaveHandler(save); });

  async function exit() {
    if (ed.dirty && !await confirmDialog({
      title: "有还没保存的修改",
      body: "退出自由改稿会丢掉没保存的改动。",
      confirmText: "丢弃修改", danger: true,
    })) return;
    onDirtyChange?.(false);
    onExit();
  }

  return (
    <div className="free-write">
      <div className="free-write-head">
        <b>✍️ 自由改稿</b>
        <span className="muted">{ed.chars} 字{ed.dirty ? " · 未保存" : ""}</span>
        <span className="grow" />
        <button className="btn-sm" onClick={() => void exit()}
          title={ed.dirty ? "有未保存修改,退出前会再确认" : "回到段落点选界面"}>返回界面模式</button>
        <button className="primary btn-sm" disabled={saving || !ed.dirty || genBlocked}
          title={genBlocked ? genHint : "保存整章(Ctrl+S)"}
          onClick={() => void save()}>
          {saving ? "保存中…" : "保存本章"}
        </button>
      </div>
      {genBlocked && <div className="notice notice-warn mb-2">{genHint || "有任务在跑,编辑器暂时只读。"}</div>}
      <div ref={hostRef} className={"free-write-host" + (genBlocked ? " is-locked" : "")} />
      <p className="hint">
        Ctrl+S 保存 · Ctrl+F 搜索替换 · Ctrl+Z 撤销;空行分段与正文处处一致。
        想用「点段落 → 改这段/润色」就用上面的「返回界面模式」。
      </p>
    </div>
  );
}
