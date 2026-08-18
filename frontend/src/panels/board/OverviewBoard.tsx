// 全书概览看板:章节网格地图 + 剧情时间线 + 人物出场时间线 + 伏笔时间线(拆自 BoardPanel.tsx)。
import { useEffect, useState } from "react";
import { api, OverviewChapter, OverviewOut, TimelineItem } from "../../api";
import { FS_CN } from "./shared";

// 格子状态:生成中 > 失配(is_stale 或版本不一致) > 已定稿/草稿/未生成
function cellState(c: OverviewChapter): string {
  if (c.status === "drafting") return "drafting";
  if (c.is_stale || (c.outline_version_used != null
    && c.outline_version_used !== c.outline_current_version)) return "stale";
  return c.status;
}

// 格子状态文案。注意:drafting 保留——overview 接口仍用它标记「生成中」(overview.py,蓝色脉动),
// 与章节 status 的死枚举清理不同源;drafted 已删(后端不再产生);finalized 为存量旧数据保留
const CELL_CN: Record<string, string> = {
  empty: "未生成", drafting: "生成中", finalized: "定稿", stale: "失配",
  pending_review: "待审", approved: "已审", quarantined: "拦截",
};

// 契约的时间跳跃提示(英文枚举)→ 中文;未知值原样展示
const JUMP_CN: Record<string, string> = {
  next_morning: "次日清晨", hours_later: "数小时后", days_later: "数日后",
};

function chapterTip(c: OverviewChapter): string {
  const lines = [
    `第${c.chapter_number}章${c.title ? `《${c.title}》` : ""}`,
  ];
  if (c.chapter_role) lines.push(`定位:${c.chapter_role}`);
  lines.push(c.status === "empty" ? "未生成" : `${c.word_count} 字`);
  if (c.outline_version_used != null) {
    const mismatch = c.outline_version_used !== c.outline_current_version;
    lines.push(
      `正文基于 v${c.outline_version_used} / 大纲 v${c.outline_current_version}`
      + (mismatch ? "(版本不一致,建议重写)" : ""),
    );
  } else {
    lines.push(`大纲 v${c.outline_current_version}`);
  }
  if (c.characters_involved.length) lines.push(`出场:${c.characters_involved.join("、")}`);
  return lines.join("\n");
}

export default function OverviewBoard({ pid, onGotoChapter }: { pid: number; onGotoChapter?: (n: number) => void }) {
  const [data, setData] = useState<OverviewOut | null>(null);
  const [err, setErr] = useState("");
  // 剧情时间线(契约聚合,零 LLM):与 overview 独立拉取,失败互不影响
  const [timeline, setTimeline] = useState<TimelineItem[] | null>(null);

  useEffect(() => {
    (async () => {
      setErr("");
      try { setData(await api.overview(pid)); } catch (e) { setErr(String(e)); }
    })();
    api.timeline(pid)
      .then((r) => setTimeline(r.items))
      .catch(() => setTimeline([]));
  }, [pid]);

  const chapters = data?.chapters ?? [];
  const maxCh = chapters.length ? Math.max(...chapters.map((c) => c.chapter_number)) : 1;
  // 最新已生成章号:判断伏笔是否逾期
  const currentCh = chapters.reduce((m, c) => (c.status !== "empty" ? Math.max(m, c.chapter_number) : m), 0);
  const nums = Array.from({ length: maxCh }, (_, i) => i + 1);
  // 刻度密度:窄屏(手机)稀疏一些,避免数字挤成一团
  const dense = typeof window !== "undefined" && window.innerWidth <= 640;
  const tickStep = Math.max(1, Math.ceil(maxCh / (dense ? 8 : 24)));

  return (
    <>
      {err && <div className="msg-err mb-2">{err}</div>}

      {/* ---- 章节网格地图 ---- */}
      <div className="card">
        <div className="card-head">
          <h2>章节地图</h2>
          <span className="muted">每章一格,点格子跳到写作</span>
          <div className="grow" />
          <span className="badge">未生成</span>
          <span className="badge warn">草稿</span>
          <span className="badge warn">待审</span>
          <span className="badge ok">已审</span>
          <span className="badge err">拦截</span>
          <span className="badge err">大纲已变</span>
        </div>
        <div className="ov-grid mt-2">
          {chapters.map((c) => {
            const st = cellState(c);
            return (
              <button key={c.chapter_number} type="button"
                className={"ov-cell st-" + st} title={chapterTip(c)}
                onClick={() => onGotoChapter?.(c.chapter_number)}>
                <b>{c.chapter_number}</b>
                <span>{CELL_CN[st] ?? st}</span>
              </button>
            );
          })}
          {!chapters.length && <div className="muted">暂无大纲。</div>}
        </div>
      </div>

      {/* ---- 剧情时间线(章末契约聚合,零 LLM) ---- */}
      <div className="card">
        <div className="card-head">
          <h2>剧情时间线</h2>
          <span className="muted">各章章末的剧情时间/地点,从章末契约聚合;写前预审与门禁已用它抓跨章时间倒流</span>
        </div>
        <div className="mt-2">
          {timeline === null && <div className="muted"><span className="spin" />加载中…</div>}
          {timeline !== null && !timeline.length && (
            <div className="muted">
              暂无契约数据——老书可去「编辑部 → 审核报告」批量补提契约后生成。
            </div>
          )}
          {(timeline ?? []).map((t) => (
            <div key={t.chapter} className="fact-line">
              <b>第{t.chapter}章末</b> {t.in_story_time || "时间未知"}
              {t.location && <span className="muted"> @ {t.location}</span>}
              {t.time_jump_hint && t.time_jump_hint !== "none" && (
                <span className="badge"> 下章跳跃:{JUMP_CN[t.time_jump_hint] ?? t.time_jump_hint}</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ---- 人物出场时间线 ---- */}
      <div className="card">
        <div className="card-head">
          <h2>人物出场</h2>
          <span className="muted">{data?.characters.length ?? 0} 位人物 × {maxCh} 章</span>
        </div>
        <div className="ov-scroll mt-2">
          <table className="tbl ov-timeline">
            <thead>
              <tr>
                <th className="ov-name">人物</th>
                {nums.map((n) => <th key={n}>{n}</th>)}
              </tr>
            </thead>
            <tbody>
              {(data?.characters ?? []).map((c) => {
                const on = new Set(c.chapters);
                return (
                  <tr key={c.name}>
                    <td className={"ov-name" + (c.retired ? " retired" : "")}>
                      {c.name}{c.retired && <span className="muted">(退场)</span>}
                    </td>
                    {nums.map((n) => <td key={n} className={on.has(n) ? "on" : ""} />)}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {data && !data.characters.length && (
            <div className="muted">暂无人物。生成章节后自动抽取,或在「人物」页签登记。</div>
          )}
        </div>
      </div>

      {/* ---- 伏笔时间线 ---- */}
      <div className="card">
        <div className="card-head">
          <h2>伏笔时间线</h2>
          <div className="grow" />
          <span className="ov-key"><i className="ov-sw planted" />已埋设</span>
          <span className="ov-key"><i className="ov-sw reinforced" />已强化</span>
          <span className="ov-key"><i className="ov-sw paid_off" />已回收</span>
          <span className="ov-key"><i className="ov-sw abandoned" />已弃用</span>
          <span className="ov-key"><i className="ov-sw overdue" />逾期未收</span>
        </div>
        <div className="ov-scroll mt-2">
          <div className="ov-fs">
            <div className="ov-fs-axis">
              <span className="ov-fs-label" />
              <div className="ov-fs-ticks">
                {nums.map((n) => (
                  <span key={n} className="ov-fs-tick">
                    {n === 1 || n === maxCh || n % tickStep === 0 ? n : ""}
                  </span>
                ))}
              </div>
            </div>
            {(data?.foreshadowings ?? []).map((f, i) => {
              const end = f.resolved_chapter ?? f.expected_chapter ?? maxCh;
              const overdue = f.expected_chapter != null && f.resolved_chapter == null
                && f.expected_chapter <= currentCh;
              const cls = overdue ? "overdue" : f.status;
              const range = `第${f.planted_chapter}章埋设 → `
                + (f.resolved_chapter ? `第${f.resolved_chapter}章回收`
                  : f.expected_chapter ? `预期第${f.expected_chapter}章回收` : "未设预期回收");
              return (
                <div key={i} className="ov-fs-row">
                  <span className="ov-fs-label" title={f.content}>{f.content}</span>
                  <div className="ov-fs-track">
                    <div className={"ov-fs-bar " + cls}
                      style={{
                        left: `${((f.planted_chapter - 1) / maxCh) * 100}%`,
                        width: `${(Math.max(end - f.planted_chapter + 1, 1) / maxCh) * 100}%`,
                      }}
                      title={`${f.content}\n${FS_CN[f.status] ?? f.status}${overdue ? " · 逾期未收" : ""}\n${range}`} />
                  </div>
                </div>
              );
            })}
            {data && !data.foreshadowings.length && (
              <div className="muted">暂无登记伏笔。</div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
