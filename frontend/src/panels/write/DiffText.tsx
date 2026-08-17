// 字符级 diff 的行内渲染(「正文即界面」P3,docs/10 §6):把 charDiff 的 DiffOp[] 铺成
// 一行行内文本——same 原色、del 删除线红(.diff-old)、ins 绿字(.diff-new),
// 视觉沿用校对卡基调。①②段内 diff 卡与③④整章 diff 模式共用这一份。
import { DiffOp } from "./charDiff";

export default function DiffText({ ops }: { ops: DiffOp[] }) {
  return (
    <span className="diff-inline">
      {ops.map((op, i) =>
        op.type === "same"
          ? <span key={i}>{op.text}</span>
          : <span key={i} className={op.type === "del" ? "diff-old" : "diff-new"}>{op.text}</span>,
      )}
    </span>
  );
}
