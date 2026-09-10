// 成书体检报告看板(docs/15 §7.3):把散在各页签的质量信号收成一份报告。
//
// 设计取向:这是「用事实,不用形容词」回答「这本书现在什么状态」的地方。
// 因此每一块都遵守同一条纪律——**没数据就说没数据**,不画一条贴地的线冒充正常
// (后端 notes 已经写明原因,前端只负责如实显示)。
import { useEffect, useState } from "react";
import { api, HealthReportOut } from "../../api";
import { errMsg } from "../../pollJob";
import { CopyBtn } from "../../ui/copy";

// 纯 CSS 折线/条形:报告是稀疏数据,不值得引图表库。
// 逐章条形图:值域 [lo, hi] 映射到高度,给一眼看趋势的直觉。
function Bars({
  values, lo, hi, className,
}: { values: number[]; lo: number; hi: number; className?: string }) {
  const span = hi - lo || 1;
  return (
    <div className={"bh-bars " + (className ?? "")}>
      {values.map((v, i) => {
        const h = Math.max(4, Math.round(((v - lo) / span) * 100));
        return <i key={i} style={{ height: `${h}%` }} title={String(v)} />;
      })}
    </div>
  );
}

function Pct({ value }: { value: number }) {
  return <>{Math.round(value * 100)}%</>;
}

export default function HealthBoard({ pid, onGotoChapter }: {
  pid: number; onGotoChapter?: (n: number) => void;
}) {
  const [data, setData] = useState<HealthReportOut | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    (async () => {
      setErr("");
      try { setData(await api.healthReport(pid)); } catch (e) { setErr(errMsg(e)); }
    })();
  }, [pid]);

  if (err) return <div className="msg-err">{err}</div>;
  if (!data) return <div className="muted"><span className="spin" />体检中…</div>;

  const flavorScores = data.flavor_curve.map((p) => p.score);
  const tensionMeans = data.tension_curve.map((p) => p.mean);

  // 下载:服务端渲染好的 Markdown 直接存盘(与页面上看到的是同一份数据)
  async function download() {
    try {
      const md = await api.healthReportMarkdown(pid);
      const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${data?.title || "book"}-体检报告.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) { setErr(errMsg(e)); }
  }

  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2>成书体检</h2>
          <span className="muted">确定性指标聚合,零 LLM,不花钱</span>
          <div className="grow" />
          <CopyBtn text={data.markdown} label="复制报告" />
          <button type="button" className="btn-sm" onClick={download}>下载 .md</button>
        </div>
        <div className="bh-stats mt-2">
          <div className="bh-stat">
            <b>{data.chapters_written}</b>
            <span>已成章 / 蓝图 {data.chapters_planned}</span>
          </div>
          <div className="bh-stat">
            <b><Pct value={data.completion} /></b>
            <span>完成度</span>
          </div>
          <div className="bh-stat">
            <b>{data.total_words.toLocaleString()}</b>
            <span>总字数(均 {data.avg_chapter_words}/章)</span>
          </div>
          <div className="bh-stat">
            <b>{data.mean_flavor ?? "—"}</b>
            <span>AI 味均值</span>
          </div>
          <div className="bh-stat">
            <b>{data.open_issues}</b>
            <span>未解决问题</span>
          </div>
          <div className="bh-stat">
            <b><Pct value={data.debt_ratio} /></b>
            <span>伏笔债务比</span>
          </div>
        </div>
      </div>

      {/* ---- 质感曲线 ---- */}
      <div className="card">
        <div className="card-head">
          <h2>质感曲线</h2>
          <span className="muted">逐章 AI 味指数(越低越好,与概览看板同口径)</span>
        </div>
        {flavorScores.length ? (
          <div className="mt-2">
            <Bars values={flavorScores} lo={0}
              hi={Math.max(...flavorScores, 1)} className="bh-flavor" />
            <div className="bh-axis">
              <span>第{data.flavor_curve[0].chapter}章</span>
              <span>第{data.flavor_curve[data.flavor_curve.length - 1].chapter}章</span>
            </div>
            {!!data.worst_flavor.length && (
              <div className="mt-2 muted">
                最该复核:
                {data.worst_flavor.map((w) => (
                  <button key={w.chapter} type="button" className="bh-link ml-2"
                    onClick={() => onGotoChapter?.(w.chapter)}>
                    第{w.chapter}章({w.score})
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : <div className="muted mt-2">还没有正文。</div>}
      </div>

      {/* ---- 节奏曲线 ---- */}
      <div className="card">
        <div className="card-head">
          <h2>节奏曲线</h2>
          <span className="muted">逐章张力均值(1 压抑 → 5 爆发,来自场景卡)</span>
        </div>
        {tensionMeans.length ? (
          <div className="mt-2">
            <Bars values={tensionMeans} lo={0} hi={5} className="bh-tension" />
            <div className="bh-axis">
              <span>第{data.tension_curve[0].chapter}章</span>
              <span>第{data.tension_curve[data.tension_curve.length - 1].chapter}章</span>
            </div>
            {data.tension_flat_chapters.length ? (
              <div className="bh-warn mt-2">
                ⚠ 无起伏章(全章所有场景同一张力档):
                {data.tension_flat_chapters.map((n) => (
                  <button key={n} type="button" className="bh-link ml-2"
                    onClick={() => onGotoChapter?.(n)}>第{n}章</button>
                ))}
                <span className="muted ml-2">——「像喝白水」在数据上的样子</span>
              </div>
            ) : <div className="muted mt-2">每章都有起伏。</div>}
          </div>
        ) : (
          <div className="muted mt-2">
            暂无场景卡数据——该书未启用场景级生成;开新书或重规划蓝图后可用。
          </div>
        )}
      </div>

      {/* ---- 一致性 + 伏笔 ---- */}
      <div className="card">
        <div className="card-head"><h2>一致性与伏笔</h2></div>
        <div className="mt-2">
          <div className="fact-line">
            <b>一致性问题</b> 未解决 {data.open_issues} 条
            {Object.keys(data.issues_by_type).length > 0 && (
              <span className="muted">
                :{Object.entries(data.issues_by_type)
                  .map(([k, v]) => `${k}×${v}`).join("、")}
              </span>
            )}
          </div>
          {!!data.issue_chapters.length && (
            <div className="fact-line muted">
              涉及章节:
              {data.issue_chapters.slice(0, 20).map((n) => (
                <button key={n} type="button" className="bh-link ml-2"
                  onClick={() => onGotoChapter?.(n)}>第{n}章</button>
              ))}
            </div>
          )}
          <div className="fact-line mt-2">
            <b>伏笔</b> 共 {data.foreshadow_total} 条,债务比 <Pct value={data.debt_ratio} />
            {data.foreshadow_by_status && (
              <span className="muted">
                :{Object.entries(data.foreshadow_by_status)
                  .map(([k, v]) => `${k}×${v}`).join("、")}
              </span>
            )}
          </div>
          {data.overdue.map((o, i) => (
            <div key={i} className="fact-line">
              <span className="badge err">逾期</span> 预期第{o.expected}章收:{o.content}
            </div>
          ))}
        </div>
      </div>

      {/* ---- 成本 ---- */}
      <div className="card">
        <div className="card-head">
          <h2>成本</h2>
          <span className="muted">本机全库 token 用量(用量表不记项目归属,按章均摊仅作参考)</span>
        </div>
        <div className="mt-2">
          {data.prompt_tokens || data.completion_tokens ? (
            <div className="fact-line">
              累计 <b>{(data.prompt_tokens + data.completion_tokens).toLocaleString()}</b> token
              (输入 {data.prompt_tokens.toLocaleString()} / 输出 {data.completion_tokens.toLocaleString()})
              · 按章均摊约 <b>{data.tokens_per_chapter.toLocaleString()}</b>
            </div>
          ) : <div className="muted">暂无记账数据。</div>}
        </div>
      </div>

      {!!data.notes.length && (
        <div className="card">
          <div className="card-head"><h2>口径说明</h2></div>
          <ul className="mt-2 muted">
            {data.notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        </div>
      )}
    </>
  );
}
