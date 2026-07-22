// 修改指令面板:输入 → LLM 预览(可再编辑/勾选) → 应用
import {
  DirectiveApplyResult, DirectiveItem, DirectivePreview, Outline,
} from "../../api";

interface Props {
  busy: string;
  directiveText: string;
  preview: DirectivePreview | null;
  drafts: DirectiveItem[];
  dirPicked: Set<number>;
  dirResult: DirectiveApplyResult | null;
  getOld: (n: number) => Outline | undefined;
  onDirectiveTextChange: (text: string) => void;
  onRunParse: () => void;
  onTogglePick: (n: number, checked: boolean) => void;
  onDraftChange: (n: number, summary: string) => void;
  onApply: () => void;
  onClose: () => void;
  onGotoStep?: (step: "write") => void;
}

export default function DirectivePanel({
  busy, directiveText, preview, drafts, dirPicked, dirResult, getOld,
  onDirectiveTextChange, onRunParse, onTogglePick, onDraftChange, onApply, onClose, onGotoStep,
}: Props) {
  return (
    <div className="mt-3">
      <div className="muted">用一句话描述结构性修改,AI 改写受影响章的大纲,你确认后才生效。</div>
      <textarea rows={2} value={directiveText}
        placeholder="如:不要男二,让他的戏份并给女主 / 把反派改成男主的哥哥"
        onChange={(e) => onDirectiveTextChange(e.target.value)} />
      {!preview && !dirResult && (
        <button className="primary mt-2" disabled={!!busy || !directiveText.trim()} onClick={onRunParse}>
          {busy && <span className="spin" />}分析影响
        </button>
      )}

      {preview && (
        <div className="card card-warn mt-3">
          <b>影响预览</b>
          <div className="card-desc mt-1">{preview.analysis}</div>
          {preview.suggest_retire.length > 0 && (
            <div className="msg-err mt-2">
              建议到「看板→人物」将以下角色退场:{preview.suggest_retire.join("、")}(退场后生成不再注入)
            </div>
          )}
          {preview.items.length === 0 ? (
            <>
              <div className="msg-ok mt-2">没有章节受该指令影响。</div>
              <div className="actions mt-2"><button onClick={onClose}>关闭</button></div>
            </>
          ) : (
            <>
              {drafts.map((d) => {
                const old = getOld(d.chapter_number);
                return (
                  <div key={d.chapter_number} className="fact-line fact-check">
                    <input type="checkbox" checked={dirPicked.has(d.chapter_number)}
                      onChange={(e) => onTogglePick(d.chapter_number, e.target.checked)} />
                    <div className="grow">
                      <b>第{d.chapter_number}章</b>{" "}
                      {d.new_title && old && d.new_title !== old.title
                        ? <span>{old.title} → <b>{d.new_title}</b></span>
                        : <span>{old?.title}</span>}
                      <div className="muted mt-1">旧:{old?.summary || "—"}</div>
                      <textarea rows={3} className="mt-1" value={d.new_summary}
                        onChange={(e) => onDraftChange(d.chapter_number, e.target.value)} />
                      <div className="muted">{d.change_reason}</div>
                    </div>
                  </div>
                );
              })}
              <div className="actions mt-2">
                <button className="primary" disabled={!!busy || !dirPicked.size} onClick={onApply}>
                  {busy && <span className="spin" />}应用修改({dirPicked.size} 章)
                </button>
                <button disabled={!!busy} onClick={onClose}>取消</button>
              </div>
              <div className="muted mt-1">应用后将保存大纲新版本,并把已有正文的章节标记为「与新大纲不符」。</div>
            </>
          )}
        </div>
      )}

      {dirResult && (
        <div className="card card-ok mt-3">
          <b>✓ 已应用修改</b>
          <div className="muted mt-1">
            {dirResult.updated.length
              ? `已更新第 ${dirResult.updated.join("、")} 章大纲`
              : "内容无实质变化,未产生新版本"}
            {dirResult.stale_chapters.length > 0 &&
              `;第 ${dirResult.stale_chapters.join("、")} 章正文已标记失配——可到「写作」重生成这些章节(或用「编辑本章」保存大改后的级联入口批量重生成)`}
          </div>
          <div className="actions mt-2">
            <button onClick={onClose}>完成</button>
            {onGotoStep && dirResult.stale_chapters.length > 0 && (
              <button className="primary" onClick={() => onGotoStep("write")}>去写作重生成 →</button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
