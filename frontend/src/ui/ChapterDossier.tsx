// ChapterDossier — 本章作战图(docs/19 M1):写前一屏看全本章的纲与账。
// 纯读投影:梗兑现拍(顶部)/章纲/出场人物(带本章有效关系)/伏笔账(埋收强化逾期)/
// 场景条/承上钩子。缺数据如实缺省(梗未建、人物未入圣经、未做场景切分),
// 不填 0 冒充「没问题」。可折叠,折叠状态记 localStorage(每书独立)。
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { qk } from "../hooks/queries";

const HIDDEN_KEY = "dossier-hidden";

export default function ChapterDossier({ pid, chapterNumber }: { pid: number; chapterNumber: number }) {
  const navigate = useNavigate();
  const [hidden, setHidden] = useState(() => localStorage.getItem(HIDDEN_KEY) === "1");
  const dossier = useQuery({
    queryKey: qk.dossier(pid, chapterNumber),
    queryFn: () => api.getChapterDossier(pid, chapterNumber),
    staleTime: 30_000,
  });

  if (hidden) {
    return (
      <button type="button" className="guide-mini muted dossier-mini"
        onClick={() => { localStorage.removeItem(HIDDEN_KEY); setHidden(false); }}>
        🎯 本章作战图
      </button>
    );
  }

  const d = dossier.data;

  function collapse() {
    localStorage.setItem(HIDDEN_KEY, "1");
    setHidden(true);
  }

  return (
    <div className="dossier" data-testid="chapter-dossier">
      <div className="dossier-head">
        <b>🎯 本章作战图</b>
        <span className="grow" />
        <button className="btn-sm" onClick={() => navigate(`/project/${pid}/settings`)}>改梗卡</button>
        <button className="btn-sm" onClick={collapse}>收起</button>
      </div>

      {dossier.isLoading || !d ? (
        <div className="muted mt-1"><span className="spin spin-sm" /> 整理本章档案…</div>
      ) : (
        <>
          {/* 梗行:纲在上。梗未建 → 如实说,给去设置的入口 */}
          <div className="dossier-premise">
            {d.premise ? (
              <>
                <b>梗</b>
                <span>{d.premise.high_concept}</span>
                {d.outline.premise_beat ? (
                  <span className="chip">本章兑现:{d.outline.premise_beat}</span>
                ) : (
                  <span className="chip chip-warn">本章节拍未标</span>
                )}
              </>
            ) : (
              <>
                <b>梗</b>
                <span className="muted">未定核心梗——梗是全书的纲,建议先补建</span>
                <button className="btn-sm" onClick={() => navigate(`/project/${pid}/settings`)}>去补建</button>
              </>
            )}
          </div>

          <div className="dossier-cols">
            {/* 章纲 */}
            <div className="dossier-block">
              <div className="dossier-block-title">章纲{d.outline.title ? ` · 《${d.outline.title}》` : ""}</div>
              {d.outline.summary ? (
                <>
                  <div className="dossier-line"><span className="muted">定位</span>{d.outline.chapter_role || "—"}</div>
                  <div className="dossier-line"><span className="muted">悬念</span>{d.outline.suspense_level || "—"}</div>
                  <div className="dossier-line"><span className="muted">情绪</span>{d.outline.emotional_tone || "—"}</div>
                  <div className="dossier-line"><span className="muted">简述</span>{d.outline.summary}</div>
                  {d.outline.scene_anchor && (
                    <div className="dossier-line dossier-hook">
                      <span className="muted">本章末钩</span>{d.outline.scene_anchor}
                    </div>
                  )}
                </>
              ) : (
                <div className="muted">本章还没有蓝图。</div>
              )}
            </div>

            {/* 出场人物 + 本章有效关系 */}
            <div className="dossier-block">
              <div className="dossier-block-title">出场人物 {d.characters.length || ""}</div>
              {d.characters.length === 0 && <div className="muted">大纲未列人物。</div>}
              {d.characters.map((c) => (
                <div className="dossier-char" key={c.name} title={c.matched ? "" : "尚未录入故事圣经,可在圣经页补建"}>
                  <span className={"premise-chip-name" + (c.matched ? "" : " unmatched")}>{c.name}</span>
                  {c.relations.map((r, i) => (
                    <span key={i} className="chip">{r.to_name}·{r.relation}</span>
                  ))}
                  {!c.matched && <span className="chip chip-warn">未入圣经</span>}
                </div>
              ))}
            </div>

            {/* 伏笔账 */}
            <div className="dossier-block">
              <div className="dossier-block-title">伏笔账</div>
              {(["planted", "paid_off", "reinforced", "overdue"] as const).map((k) => {
                const items = d.foreshadows[k];
                const cn = { planted: "本章埋", paid_off: "本章收", reinforced: "本章强化", overdue: "已逾期" }[k];
                if (!items.length) return null;
                return (
                  <div className="dossier-line" key={k}>
                    <span className={"muted" + (k === "overdue" ? " dossier-overdue" : "")}>{cn}</span>
                    <span>{items.map((f) => f.description).join("；")}</span>
                  </div>
                );
              })}
              {!d.foreshadows.planted.length && !d.foreshadows.paid_off.length
                && !d.foreshadows.reinforced.length && !d.foreshadows.overdue.length && (
                <div className="muted">本章无伏笔动作。</div>
              )}
            </div>
          </div>

          {/* 场景条(做过场景切分才显示;没切分回落到大纲节拍) */}
          {(d.scenes.length > 0 || d.outline.beats.length > 0) && (
            <div className="dossier-scenes">
              {d.scenes.length > 0
                ? d.scenes.map((s) => (
                  <span className="chip" key={s.seq} title={`${s.location || "—"} · 张力${s.tension_level} · ${s.status}`}>
                    场{s.seq} {s.emotion_target || s.title || ""}·张力{s.tension_level}
                  </span>
                ))
                : d.outline.beats.map((b, i) => <span className="chip" key={i}>拍{i + 1}:{b.slice(0, 18)}</span>)}
            </div>
          )}

          {/* 承上钩子 */}
          <div className="dossier-line dossier-hook-line">
            <span className="muted">承上</span>
            {d.prev_threads.length
              ? d.prev_threads.join("；")
              : <span className="muted">{d.chapter_number > 1 ? "上一章没有未回收的钩子(或未提取契约)" : "全书第一章"}</span>}
          </div>
        </>
      )}
    </div>
  );
}
