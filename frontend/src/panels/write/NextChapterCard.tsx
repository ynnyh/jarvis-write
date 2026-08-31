// 章尾下一章卡(「正文即界面」P1,docs/10 §2):正文之后唯一常驻的「推进」入口。
// 有未写章 → 「让 AI 写」下一章;全部写完 → 引导去书房导出/投稿。
// 当前选中章未写(empty)时不渲染——空态由正文区大卡承担(见 WritePanel)。
import { useNavigate } from "react-router-dom";
import { estimateText } from "./genDuration";

interface Props {
  pid: number;
  // 下一个未写章号(大纲中第一个还没有正文的章);null=全部写完
  nextNum: number | null;
  nextTitle?: string;
  genBlocked: boolean;
  genHint: string;
  onGenerate: (n: number) => void;
}

export default function NextChapterCard({
  pid, nextNum, nextTitle, genBlocked, genHint, onGenerate,
}: Props) {
  const nav = useNavigate();
  return (
    <div className="card next-chapter">
      {nextNum !== null ? (
        <>
          <span className="grow">
            下一章:第 {nextNum} 章{nextTitle ? `《${nextTitle}》` : ""}
          </span>
          <button className="primary" disabled={genBlocked}
            title={genBlocked ? genHint : `按蓝图生成本章,${estimateText(pid)},完成后自动选中`}
            onClick={() => onGenerate(nextNum)}>
            让 AI 写
          </button>
        </>
      ) : (
        <>
          <span className="grow muted">全部写完,这本书的章节都有了。</span>
          <button onClick={() => nav(`/project/${pid}/book?tab=publish`)}>
            去书房导出/投稿
          </button>
        </>
      )}
    </div>
  );
}
