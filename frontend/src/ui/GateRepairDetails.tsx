// 门禁定点修复明细(分级回炉,docs/08 §5.4):可折叠展示 AI 定点改了哪些句子、
// 哪些修复没应用及原因。GenResultCard(生成结果)与 ReviewCard(审校回显)共用;
// 旧快照无 repairs 键,渲染为空不显示。
import { GateRepairDetail } from "../api";

export default function GateRepairDetails({ repairs }: { repairs?: GateRepairDetail }) {
  const applied = repairs?.applied ?? [];
  const failed = repairs?.failed ?? [];
  if (!applied.length && !failed.length) return null;
  return (
    <details className="issue-ev mt-1">
      <summary>
        定点修复明细:{applied.length} 处已改{failed.length ? `,${failed.length} 处未应用` : ""}
      </summary>
      {applied.map((r, k) => (
        <div key={k} className="fact-line">
          <span className="badge ok">已改</span>“{r.original}”→“{r.replacement}”
        </div>
      ))}
      {failed.map((r, k) => (
        <div key={k} className="fact-line muted">未应用:“{r.original}”({r.reason})</div>
      ))}
    </details>
  );
}
