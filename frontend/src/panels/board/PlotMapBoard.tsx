// 情节推进图看板(docs/19 M5):章×场景网格 + 伏笔埋收带 + 人物在场带(docs/20),
// 一屏理顺情节进展。张力 1-5 着色(1 压抑 → 5 爆发),伏笔从埋设章画横带到回收/预期章,
// 逾期标红;人物在场带画首秀/退场,失踪检测(线未收又长期未出场)标黄。
// 纯读投影:数据来自 /plot-map(场景卡 + 大纲节拍 + 伏笔调度)+ /characters,零抽取。
import { useEffect, useMemo, useState } from "react";
import { api, CharactersOut, PlotMap } from "../../api";
import { errMsg } from "../../pollJob";

const TENSION_BG: Record<number, string> = {
  1: "t1", 2: "t2", 3: "t3", 4: "t4", 5: "t5",
};

// 失踪检测阈值:有未闭合关系线、却连续这么多章没露面 → 标黄(docs/20 §7.2)
const MISSING_AFTER = 30;

export default function PlotMapBoard({ pid, onGotoChapter }: {
  pid: number; onGotoChapter?: (n: number) => void;
}) {
  const [data, setData] = useState<PlotMap | null>(null);
  const [cast, setCast] = useState<CharactersOut | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    (async () => {
      setErr("");
      try {
        const [pm, cs] = await Promise.all([
          api.plotMap(pid),
          api.characters(pid).catch(() => null),
        ]);
        setData(pm); setCast(cs);
      } catch (e) { setErr(errMsg(e)); }
    })();
  }, [pid]);

  // 在场带数据:出场过的人物按首秀排序;失踪 = 未退场 + 有未闭合关系线 + 断档超阈值
  const presence = useMemo(() => {
    const chapters = data?.chapters ?? [];
    if (!chapters.length || !cast) return [];
    const nums = chapters.map((c) => c.chapter_number);
    const maxN = Math.max(...nums);
    return cast.characters
      .filter((c) => c.appearance_chapters.length > 0)
      .sort((a, b) => Math.min(...a.appearance_chapters) - Math.min(...b.appearance_chapters))
      .map((c) => {
        const app = [...c.appearance_chapters].sort((x, y) => x - y);
        const first = app[0];
        const last = app[app.length - 1];
        const openTies = c.relations.some((r) => r.valid_until === null);
        const missing = !c.retired && openTies && maxN - last >= MISSING_AFTER;
        return { card: c, app, first, last, missing };
      });
  }, [data, cast]);

  if (err) return <div className="msg-err">{err}</div>;
  if (!data) return <div className="muted"><span className="spin" />整理情节推进图…</div>;

  if (!data.chapters.length) {
    return (
      <div className="card">
        <div className="card-head"><h2>情节推进图</h2></div>
        <div className="muted mt-2">还没有蓝图。先在「开书」铺蓝图,这里会画出全书的情节进展。</div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-head">
        <h2>情节推进图</h2>
        <span className="muted">每章一列:场景格按张力着色(1 压抑 → 5 爆发);点格跳本章;下方是伏笔埋收带。</span>
      </div>

      <div className="plotmap-scroll">
        <div className="plotmap-grid">
          {data.chapters.map((c) => (
            <div className="plotmap-col" key={c.chapter_number}>
              <button type="button"
                className={"plotmap-col-head" + (c.written ? "" : " unwritten")}
                title={c.premise_beat ? `梗兑现:${c.premise_beat}` : "本章节拍未标"}
                onClick={() => onGotoChapter?.(c.chapter_number)}>
                <b>第{c.chapter_number}章</b>
                <span className="muted">{c.emotional_tone || c.chapter_role || ""}</span>
              </button>
              {c.scenes.length > 0
                ? c.scenes.map((s) => (
                  <div key={s.seq}
                    className={"plotmap-scene " + (TENSION_BG[s.tension_level] ?? "t3")}
                    title={s.title || s.location || ""}
                    onClick={() => onGotoChapter?.(c.chapter_number)}>
                    <span>场{s.seq}</span>
                    {s.location && <span className="muted">{s.location}</span>}
                  </div>
                ))
                : c.beats.map((b, i) => (
                  <div key={i} className="plotmap-scene beats" title={b}>
                    <span>拍{i + 1}</span>
                    <span className="muted">{b.slice(0, 10)}</span>
                  </div>
                ))}
            </div>
          ))}
        </div>

        {/* 伏笔埋收带:每条一行,从埋设章横到回收/预期章;超出预期(逾期)标红 */}
        {data.foreshadows.length > 0 && (
          <div className="plotmap-fs">
            <div className="dossier-block-title mt-2">伏笔埋收链</div>
            {data.foreshadows.map((f) => {
              const overdue = !f.payoff && f.expected !== null && f.expected < data.chapters.length;
              return (
                <div className="plotmap-fs-row" key={f.id}
                  title={`${f.description}(状态:${f.status})`}>
                  <span className="plotmap-fs-label">{f.description.slice(0, 14)}</span>
                  <span className="plotmap-fs-track">
                    {data.chapters.map((c) => {
                      const inSpan = c.chapter_number >= f.planted
                        && (f.payoff === null || c.chapter_number <= f.payoff);
                      const isPlant = c.chapter_number === f.planted;
                      const isEnd = f.payoff === c.chapter_number;
                      return (
                        <i key={c.chapter_number}
                          className={[
                            "plotmap-fs-cell",
                            inSpan ? "on" : "",
                            isPlant ? "plant" : "",
                            isEnd ? "payoff" : "",
                            overdue && c.chapter_number > f.expected! ? "overdue" : "",
                          ].join(" ")}
                          title={`第${c.chapter_number}章`} />
                      );
                    })}
                  </span>
                  <span className="muted plotmap-fs-desc">
                    {f.payoff ? `第${f.payoff}章已收` : f.expected ? `预期第${f.expected}章收` : "未定期"}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {/* 人物在场带(docs/20 §7.2):每角色一行,首秀点/末场点标记;
            失踪检测:未退场 + 关系线未闭合 + 断档 ≥ 阈值 → 黄行提醒安排下场 */}
        {presence.length > 0 && (
          <div className="plotmap-fs">
            <div className="dossier-block-title mt-2">
              人物在场带
              <span className="muted"> · 亮格=出场,星=首秀,黑点=最近一场;黄行=线没收人先丢,该安排了</span>
            </div>
            {presence.map(({ card, app, first, last, missing }) => (
              <div className={"plotmap-fs-row" + (missing ? " missing" : "")}
                key={card.id}
                title={missing
                  ? `${card.name}:关系线未闭合,却已 ${last ? "自第" + last + "章后" : ""}长期未出场——考虑安排下场或让他回来`
                  : `${card.name}(出场 ${app.length} 章)`}>
                <span className={"plotmap-fs-label" + (card.retired ? " retired" : "")}>
                  {card.retired ? "🔒" : ""}{card.name}
                </span>
                <span className="plotmap-fs-track">
                  {data!.chapters.map((c) => {
                    const on = app.includes(c.chapter_number);
                    return (
                      <i key={c.chapter_number}
                        className={[
                          "plotmap-fs-cell",
                          on ? "on cast" : "",
                          c.chapter_number === first ? "debut" : "",
                          c.chapter_number === last && app.length > 1 ? "latest" : "",
                        ].join(" ")}
                        title={`${card.name} · 第${c.chapter_number}章`} />
                    );
                  })}
                </span>
                <span className="muted plotmap-fs-desc">
                  {card.retired
                    ? "已退场"
                    : missing
                      ? `第${last}章后未出场`
                      : app.length === 1 ? `仅第${first}章` : `首秀第${first}章`}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
