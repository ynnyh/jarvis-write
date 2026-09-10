// 全书节奏曲线面板(D5:全书一条张力总线 + 每章切段)。
//
// 解决的问题:「该精彩时精彩、该压抑时压抑」是曲线问题不是点问题。此前全书
// 节奏只体现在「章节定位」这类标签上,没有密度曲线——每章都可能工整,整体是
// 一条平线,读者因此弃书(比 AI 味更致命)。
//
// 这个面板把总线的形状画出来:哪几章蓄力、哪几章该爆,一眼可见;并对「平线」
// 直接给出警告文案。曲线是确定性算出来的(后端不花 LLM),所以每次打开都实时。
import { useEffect, useState } from "react";
import { api, TensionCurve } from "../../api";
import { errMsg } from "../../pollJob";

interface Props {
  pid: number;
}

// 张力档 → 中文标签(与后端 _TENSION_LABELS 同口径,这里只用于 tooltip 简述)
const LEVEL_LABEL: Record<number, string> = {
  1: "蓄力", 2: "收紧", 3: "推进", 4: "高点", 5: "爆发",
};

// 张力档 → 条形高度百分比(1 档也给一点高度,不然看起来像缺数据)
const BAR_H: Record<number, number> = { 1: 20, 2: 36, 3: 52, 4: 76, 5: 100 };
const BAR_COLOR: Record<number, string> = {
  1: "#888780", 2: "#7F77DD", 3: "#534AB7", 4: "#BA7517", 5: "#A32D2D",
};

export default function TensionCurvePanel({ pid }: Props) {
  const [data, setData] = useState<TensionCurve | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.tensionCurve(pid)
      .then((d) => { if (alive) { setData(d); setErr(""); } })
      .catch((e) => { if (alive) setErr(errMsg(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [pid]);

  if (loading) {
    return (
      <div className="card card-compact mt-2">
        <div className="hint"><span className="spin spin-sm" /> 正在计算全书节奏曲线…</div>
      </div>
    );
  }
  if (err) {
    return (
      <div className="card card-compact mt-2">
        <div className="hint">节奏曲线取数失败:{err}</div>
      </div>
    );
  }
  if (!data || !data.tension.length) return null;

  const { tension, roles, report } = data;
  const peakIdx = tension.indexOf(Math.max(...tension));

  return (
    <div className="card card-compact mt-2">
      <label className="fl">全书节奏曲线</label>
      <div className="hint mb-1">
        每章的目标张力(1 蓄力 → 5 爆发)。写正文时这一章的坐标会注入 prompt,
        章内分场以它为基准做起伏——<b>全书一条平线是读者弃书的首要原因</b>,
        比单章质量更致命。
      </div>

      {report.flat && (
        <div className="hint mb-1" style={{ color: "var(--color-text-warning, #BA7517)" }}>
          节奏偏平(极差仅 {report.span} 档):全书缺少明显的高低落差,建议重新生成卷纲
          或调整各章定位/悬念密度,让高潮章真正高起来。
        </div>
      )}

      <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 96,
        padding: "8px 4px 0", overflowX: "auto" }}>
        {tension.map((v, i) => (
          <div key={i} style={{ flex: "0 0 14px", display: "flex",
            flexDirection: "column", alignItems: "center", gap: 2 }}
            title={`第${i + 1}章 · ${LEVEL_LABEL[v] ?? v}(${v}/5)${roles[i] ? " · " + roles[i] : ""}`}>
            <div style={{ width: 10, height: `${BAR_H[v] ?? 30}%`,
              background: BAR_COLOR[v] ?? "#534AB7", borderRadius: 2 }} />
            <span style={{ fontSize: 9, color: "var(--color-text-tertiary, #888780)" }}>
              {i + 1}
            </span>
          </div>
        ))}
      </div>

      <div className="hint mt-2" style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
        <span>共 {report.chapters} 章</span>
        <span>极差 {report.span} 档</span>
        <span>均值 {report.mean}</span>
        <span>峰值:第 {peakIdx + 1} 章</span>
        <span>最长同值 {report.longest_run} 章</span>
      </div>
      <div className="hint mt-1">
        峰后回落的末章是余韵,不是失误;峰值若落在最后一章,反而会让读者读完无处落脚。
      </div>
    </div>
  );
}
