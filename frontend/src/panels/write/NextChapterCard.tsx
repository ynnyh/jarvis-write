// 章尾下一章卡(「正文即界面」P1,docs/10 §2):正文之后唯一常驻的「推进」入口。
// 有未写章 → 「让 AI 写」下一章;全部写完 → 引导去书房导出/投稿。
// 订单衔接(docs/20 确认链 L5→L6):下一章已有确认订单时,主按钮变「按单写 (vN)」
// 并给「查看/编辑订单」次入口——确认过的订单是硬约束,生成入口要如实反映它。
// 当前选中章未写(empty)时不渲染——空态由正文区大卡承担(见 WritePanel)。
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { qk } from "../../hooks/queries";
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
  // 下一章的订单状态(有确认订单 → 生成入口明示「按单」)
  const order = useQuery({
    queryKey: qk.chapterOrder(pid, nextNum ?? 0),
    queryFn: () => api.getChapterOrder(pid, nextNum ?? 0),
    enabled: nextNum !== null,
  });
  const orderConfirmed = order.data?.order?.status === "confirmed";
  const orderVersion = order.data?.order?.version ?? 0;

  return (
    <div className="card next-chapter">
      {nextNum !== null ? (
        <>
          <span className="grow">
            下一章:第 {nextNum} 章{nextTitle ? `《${nextTitle}》` : ""}
            {orderConfirmed && (
              <span className="ck-state" style={{ marginLeft: 8 }}>按订单 v{orderVersion}</span>
            )}
          </span>
          {orderConfirmed && (
            <button
              title={`第 ${nextNum} 章有确认过的订单(六单约束);想按蓝图行写,先在订单卡撤回确认`}
              onClick={() => nav(`/project/${pid}/write?ch=${nextNum}`)}>
              查看/编辑订单
            </button>
          )}
          <button className="primary" disabled={genBlocked}
            title={genBlocked ? genHint : orderConfirmed
              ? `按确认订单 v${orderVersion} 生成本章,${estimateText(pid)},完成后自动选中`
              : `按蓝图生成本章,${estimateText(pid)},完成后自动选中`}
            onClick={() => onGenerate(nextNum)}>
            {orderConfirmed ? `按单写 (v${orderVersion})` : "让 AI 写"}
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
