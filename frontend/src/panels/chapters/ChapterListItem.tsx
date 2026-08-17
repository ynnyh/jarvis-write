// 目录行(「正文即界面」P1 瘦身版):章号/标题/状态徽标/连写 checkbox/生成中 spinner。
// 行内操作(生成/阅读/通过/放行/行内重写框)全部删除——点行=打开该章(未写的章也可选中,
// 打开后由正文区空态大卡承担「让 AI 写这一章」)。
import { ChapterBrief, Outline } from "../../api";
import { STATUS_BADGE, STATUS_CN } from "../../components/Reader";

interface Props {
  outline: Outline;
  chapter: ChapterBrief | undefined;
  // 连写勾选(已写好且未过期的章禁勾,与旧章节轨一致)
  queueMode: boolean;
  queuePicked: boolean;
  generating: boolean;
  genStage: string;
  onToggleQueue: (checked: boolean) => void;
  onOpen: () => void;
}

export default function ChapterListItem({
  outline: o, chapter: ch, queueMode, queuePicked, generating, genStage,
  onToggleQueue, onOpen,
}: Props) {
  const st = ch?.status ?? "empty";
  return (
    <div className="fact-line fact-row">
      {queueMode && (
        <input type="checkbox" className="queue-check"
          checked={queuePicked}
          disabled={!!ch && !ch.is_stale}
          title={ch && !ch.is_stale ? "已写好的章不用排队" : undefined}
          onChange={(e) => onToggleQueue(e.target.checked)} />
      )}
      <button type="button" className="fact-title linkish" onClick={onOpen}>
        <b>第{o.chapter_number}章</b> {o.title}
        <span className={"badge " + (ch?.is_stale ? "err" : STATUS_BADGE[st] ?? "")}>
          {ch?.is_stale ? "大纲已变" : STATUS_CN[st] ?? st}
        </span>
        {ch && <span className="muted"> {ch.word_count}字</span>}
        {generating && (
          <span className="gen-stage"><span className="spin" />{genStage}</span>
        )}
      </button>
    </div>
  );
}
