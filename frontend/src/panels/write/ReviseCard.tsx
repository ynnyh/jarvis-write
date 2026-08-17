// write 区中栏重写意见卡(act=revise):原行内重写框的提升版,针对当前章。
// 评分卡的「按此重写」、localStorage revise-draft 交接都落到这张卡(预填意见文本)。
import { ChapterDetail, EditorAction } from "../../api";
import ReviseEditor from "./ReviseEditor";

interface Props {
  pid: number;
  chapter: ChapterDetail;
  text: string;
  proseActions: EditorAction[];
  genBlocked: boolean;
  genHint: string;
  onTextChange: (text: string) => void;
  onSubmit: () => void;
  onClose: () => void;
}

export default function ReviseCard({
  pid, chapter, text, proseActions, genBlocked, genHint, onTextChange, onSubmit, onClose,
}: Props) {
  return (
    <div className="card">
      <div className="card-head mb-2">
        <h3 className="grow">重写第{chapter.chapter_number}章</h3>
        <button className="btn-sm" onClick={onClose}>收起</button>
      </div>
      <ReviseEditor
        pid={pid}
        chapterNumber={chapter.chapter_number}
        isStale={chapter.is_stale}
        text={text}
        proseActions={proseActions}
        genBlocked={genBlocked}
        genHint={genHint}
        onTextChange={onTextChange}
        onSubmit={onSubmit}
        onCancel={onClose}
      />
    </div>
  );
}
