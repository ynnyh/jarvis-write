// 时序事实时间线:每条角色轨道把该角色的时序事实画成章节区间条——
// 「第 N 章时他是什么状态」的可视化版(与故事圣经·时间机同源同数据,零 LLM)。
// 复用概览页伏笔时间线的轨道/区间条样式(ov-fs-*);点区间条跳到该章。
import { useEffect, useMemo, useState } from "react";
import { api, FactSpan, FactsTimelineOut } from "../../api";
import { errMsg } from "../../pollJob";

const FACT_TYPE_CN: Record<string, string> = {
  state: "状态", ability: "能力", possession: "持有", relationship: "关系", location: "位置",
};
const IMP_CN: Record<string, string> = { critical: "关键", major: "重要", minor: "次要" };

function spanTip(f: FactSpan): string {
  const range = f.valid_until != null
    ? `第${f.valid_from}–${f.valid_until}章`
    : `第${f.valid_from}章起,至今有效`;
  return `${f.content}\n${FACT_TYPE_CN[f.fact_type] ?? f.fact_type} · ${IMP_CN[f.importance] ?? f.importance}\n${range}`;
}

export default function TimelineBoard({ pid, onGotoChapter }: {
  pid: number; onGotoChapter?: (n: number) => void;
}) {
  const [data, setData] = useState<FactsTimelineOut | null>(null);
  const [err, setErr] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("all");

  useEffect(() => {
    (async () => {
      setErr("");
      try { setData(await api.factsTimeline(pid)); } catch (e) { setErr(errMsg(e)); }
    })();
  }, [pid]);

  const maxCh = data?.max_chapter ?? 0;
  const types = useMemo(() => {
    const set = new Set<string>();
    (data?.tracks ?? []).forEach((t) => t.facts.forEach((f) => set.add(f.fact_type)));
    return Array.from(set);
  }, [data]);
  const tracks = useMemo(() => {
    const all = data?.tracks ?? [];
    if (typeFilter === "all") return all;
    return all
      .map((t) => ({ ...t, facts: t.facts.filter((f) => f.fact_type === typeFilter) }))
      .filter((t) => t.facts.length > 0);
  }, [data, typeFilter]);

  return (
    <>
      {err && <div className="msg-err mb-2">{err}</div>}

      <div className="card">
        <div className="card-head">
          <h2>状态时间线</h2>
          <span className="muted">
            时序事实按章节区间画条——谁在第几章到第几章是什么状态,一眼看穿;
            与「故事圣经 · 时间机」同源,点条跳到对应章
          </span>
          <div className="grow" />
          <span className="ov-key"><i className="ov-sw" style={{ background: "var(--err)" }} />关键</span>
          <span className="ov-key"><i className="ov-sw" style={{ background: "var(--warn)" }} />重要</span>
          <span className="ov-key"><i className="ov-sw" style={{ background: "var(--text-3)" }} />次要</span>
        </div>

        {types.length > 1 && (
          <div className="chips mt-2">
            <button type="button" className={"chip" + (typeFilter === "all" ? " on" : "")}
              onClick={() => setTypeFilter("all")}>全部类型</button>
            {types.map((t) => (
              <button key={t} type="button"
                className={"chip" + (typeFilter === t ? " on" : "")}
                onClick={() => setTypeFilter(t)}>
                {FACT_TYPE_CN[t] ?? t}
              </button>
            ))}
          </div>
        )}

        <div className="ov-scroll mt-2">
          <div className="ov-fs">
            <div className="ov-fs-axis">
              <span className="ov-fs-label" />
              <div className="ov-fs-ticks">
                {Array.from({ length: maxCh }, (_, i) => i + 1).map((n) => {
                  const dense = typeof window !== "undefined" && window.innerWidth <= 640;
                  const step = Math.max(1, Math.ceil(maxCh / (dense ? 8 : 24)));
                  return (
                    <span key={n} className="ov-fs-tick">
                      {n === 1 || n === maxCh || n % step === 0 ? n : ""}
                    </span>
                  );
                })}
              </div>
            </div>
            {tracks.map((t) => (
              <div key={t.entity_id} className="ov-fs-row">
                <span className="ov-fs-label" title={t.name}>
                  {t.name}{t.retired && <span className="muted">(退场)</span>}
                </span>
                <div className="ov-fs-track">
                  {t.facts.map((f, i) => {
                    const end = f.valid_until ?? maxCh;
                    const color = f.importance === "critical" ? "var(--err)"
                      : f.importance === "major" ? "var(--warn)" : "var(--text-3)";
                    return (
                      <div key={i} className="ov-fs-bar"
                        style={{
                          left: `${((f.valid_from - 1) / Math.max(maxCh, 1)) * 100}%`,
                          width: `${(Math.max(end - f.valid_from + 1, 1) / Math.max(maxCh, 1)) * 100}%`,
                          background: color,
                          opacity: 0.75,
                        }}
                        title={spanTip(f)}
                        onClick={() => onGotoChapter?.(f.valid_from)}
                        role="button" />
                    );
                  })}
                </div>
              </div>
            ))}
            {data && !tracks.length && (
              <div className="muted">
                {data.tracks.length
                  ? "该类型下暂无事实,换一个类型试试。"
                  : "暂无时序事实。生成章节后自动抽取,或在「故事圣经」手动登记。"}
              </div>
            )}
            {data && data.other_entities_count > 0 && (
              <div className="muted mt-2">
                另有 {data.other_entities_count} 个角色事实较少未成轨道,可在「人物」页签查看。
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
