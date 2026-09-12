// 情节推进图看板(docs/19 M5):章×场景网格 + 伏笔埋收带,一屏理顺情节进展。
// 张力 1-5 着色(1 压抑 → 5 爆发),伏笔从埋设章画横带到回收/预期章,逾期标红。
// 纯读投影:数据来自 /plot-map(场景卡 + 大纲节拍 + 伏笔调度),零抽取。
import { useEffect, useState } from "react";
import { api, PlotMap } from "../../api";
import { errMsg } from "../../pollJob";

const TENSION_BG: Record<number, string> = {
  1: "t1", 2: "t2", 3: "t3", 4: "t4", 5: "t5",
};

export default function PlotMapBoard({ pid, onGotoChapter }: {
  pid: number; onGotoChapter?: (n: number) => void;
}) {
  const [data, setData] = useState<PlotMap | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    (async () => {
      setErr("");
      try { setData(await api.plotMap(pid)); } catch (e) { setErr(errMsg(e)); }
    })();
  }, [pid]);

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
      </div>
    </div>
  );
}
