// 全书批修验收卡组:「全书批修」job 产出的跨章替换对,按章分组逐章出验收卡。
// 每章复用 AnnotatedReviseCard(字符级 diff 逐条 [接受]/[拒绝],接受走 paraEdit
// 快照守卫写回),单条接受后经 onPairAccepted 销账对应标记;一章内全部处理完点
// 「完成」收起该章卡,全部收起后由父级清空整个结果。
// 懒加载各章 ChapterDetail(批修结果只有章号,正文详情按需拉一次)。
import { useEffect, useState } from "react";
import { api, ChapterDetail, MarksReviseResult } from "../../api";
import AnnotatedReviseCard from "./AnnotatedReviseCard";

interface Props {
  pid: number;
  result: MarksReviseResult;
  // 单条接受写回成功:父级更新 qk.chapter 缓存并刷新章节列表
  onSaved: (updated: ChapterDetail) => void;
  // 单条接受后销账对应标记(后端 DELETE + 本地状态移除)
  onPairAccepted: (markId: number) => void;
  // 某章卡关闭(全部处理完或用户主动收起):父级从结果里摘掉该章
  onChapterClose: (chapterNumber: number) => void;
}

export default function MarksReviseCards({ pid, result, onSaved, onPairAccepted, onChapterClose }: Props) {
  return (
    <>
      {result.chapters.map((group) => (
        <MarksReviseChapterCard
          key={group.chapter_number}
          pid={pid}
          chapterNumber={group.chapter_number}
          pairs={group.pairs}
          onSaved={onSaved}
          onPairAccepted={onPairAccepted}
          onClose={() => onChapterClose(group.chapter_number)}
        />
      ))}
    </>
  );
}

// 单章卡:拉该章详情 → 渲染 AnnotatedReviseCard(与②档单章批注改同一验收交互)
function MarksReviseChapterCard({ pid, chapterNumber, pairs, onSaved, onPairAccepted, onClose }: {
  pid: number;
  chapterNumber: number;
  pairs: MarksReviseResult["chapters"][number]["pairs"];
  onSaved: (updated: ChapterDetail) => void;
  onPairAccepted: (markId: number) => void;
  onClose: () => void;
}) {
  const [chapter, setChapter] = useState<ChapterDetail | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let cancelled = false;
    api.getChapter(pid, chapterNumber)
      .then((c) => { if (!cancelled) setChapter(c); })
      .catch((e) => { if (!cancelled) setErr(String(e)); });
    return () => { cancelled = true; };
  }, [pid, chapterNumber]);

  if (err) {
    return (
      <div className="card">
        <div className="card-head">
          <h3 className="grow">全书批修 · 第{chapterNumber}章</h3>
          <button className="btn-sm" onClick={onClose}>关闭</button>
        </div>
        <div className="msg-err mt-2">本章详情加载失败,无法验收:{err}</div>
      </div>
    );
  }
  if (!chapter) {
    return (
      <div className="card muted">
        <span className="spin spin-sm" /> 正在载入第 {chapterNumber} 章正文…
      </div>
    );
  }
  return (
    <AnnotatedReviseCard
      pid={pid}
      chapter={chapter}
      pairs={pairs}
      heading="全书批修"
      onSaved={onSaved}
      onPairAccepted={(i) => onPairAccepted(pairs[i]?.mark_id)}
      onClose={onClose}
    />
  );
}
