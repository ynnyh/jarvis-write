// 用量与花费卡:token 统计 + 官方牌价折算金额 + 峰时提示。
// 数据源 /api/usage;金额口径诚实——底价上界估算,无牌价模型不编价。
import { useEffect, useState } from "react";
import { api, UsageSummary } from "../../api";
import { errMsg } from "../../pollJob";

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function UsageCard() {
  const [d, setD] = useState<UsageSummary | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.usage().then(setD).catch((e) => setErr(errMsg(e)));
  }, []);

  if (err) return null; // 用量加载失败不打扰设置页,静默收起
  if (!d) {
    return (
      <div className="card">
        <div className="card-head"><h2>用量与花费</h2></div>
        <p className="card-desc"><span className="spin" /> 加载中…</p>
      </div>
    );
  }
  if (d.total_calls === 0) {
    return (
      <div className="card">
        <div className="card-head"><h2>用量与花费</h2></div>
        <p className="card-desc">还没有调用记录。开始创作后,这里会显示 token 用量与折算金额。</p>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-head"><h2>用量与花费</h2></div>
      <div className="usage-summary">
        <span>调用 <b>{d.total_calls}</b> 次</span>
        <span>输入 <b>{fmtTokens(d.total_prompt_tokens)}</b> tok</span>
        <span>输出 <b>{fmtTokens(d.total_completion_tokens)}</b> tok</span>
        {d.total_estimated_cost != null && (
          <span>估算花费 <b>¥{d.total_estimated_cost.toFixed(2)}</b></span>
        )}
      </div>
      <p className="card-desc">
        {d.cost_note ?? "部分模型没有可靠公开牌价,只统计 token 不折算金额。"}
        {d.unpriced_models.length > 0 &&
          ` 未折算:${d.unpriced_models.join("、")}。`}
      </p>
      {d.by_model.length > 1 && (
        <table className="usage-table">
          <thead>
            <tr><th>模型</th><th>调用</th><th>输入</th><th>输出</th><th>估算</th></tr>
          </thead>
          <tbody>
            {d.by_model.map((m) => (
              <tr key={m.model}>
                <td>{m.model}</td>
                <td>{m.calls}</td>
                <td>{fmtTokens(m.prompt_tokens)}</td>
                <td>{fmtTokens(m.completion_tokens)}</td>
                <td>{m.estimated_cost != null ? `¥${m.estimated_cost.toFixed(2)}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className={`peak-note ${d.peak_hours.is_peak ? "" : "peak-off"}`}>
        {d.peak_hours.is_peak ? "⚠ " : "ⓘ "}
        {d.peak_hours.note}
        {" "}参考:官方 v4-flash 实测写一章约 6.7 万 token ≈ 低峰 ¥0.08 / 峰时 ¥0.16,
        一杯奶茶钱够写一部 10 万字长篇。
      </div>
    </div>
  );
}
