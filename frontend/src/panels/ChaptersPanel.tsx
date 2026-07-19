// 写作面板:逐章生成 / 阅读;本章蓝图上下文置顶;润色移步「润色」工作区
import { useCallback, useEffect, useState } from "react";
import {
  api, ChapterBrief, ChapterDetail, GenerateChapterResponse, Outline, Project, Tendency,
} from "../api";
import TendencySelector from "../components/TendencySelector";

interface Props { pid: number; project: Project; outlines: Outline[]; }

const STATUS_CN: Record<string, string> = {
  empty: "未生成", drafting: "生成中", drafted: "有草稿",
  finalized: "已定稿", stale: "大纲已变",
};

export default function ChaptersPanel({ pid, project, outlines }: Props) {
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [current, setCurrent] = useState<ChapterDetail | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [genResult, setGenResult] = useState<GenerateChapterResponse | null>(null);
  const [genTendency, setGenTendency] = useState<Tendency>({});
  const [showTendency, setShowTendency] = useState(false);

  const reload = useCallback(async () => {
    setChapters(await api.listChapters(pid));
  }, [pid]);
  useEffect(() => { reload().catch((e) => setErr(String(e))); }, [reload]);

  const byNum = new Map(chapters.map((c) => [c.chapter_number, c]));
  const currentOutline = current
    ? outlines.find((o) => o.chapter_number === current.chapter_number)
    : null;

  async function open(n: number) {
    setErr(""); setGenResult(null);
    try { setCurrent(await api.getChapter(pid, n)); } catch (e) { setErr(String(e)); }
  }

  async function generate(n: number) {
    setBusy(`第 ${n} 章生成中:草稿 → 定稿 → 一致性检查 → 状态抽取 → 前情摘要(约3-10分钟)…`);
    setErr(""); setGenResult(null);
    try {
      const r = await api.generateChapter(pid, n, genTendency);
      setGenResult(r); setCurrent(r);
      await reload();
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  return (
    <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
      <div style={{ width: 300, flexShrink: 0 }}>
        <div className="card" style={{ padding: 12 }}>
          <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
            <h3 style={{ flex: 1, margin: 0 }}>章节</h3>
            <button onClick={() => setShowTendency(!showTendency)}>
              {showTendency ? "收起" : "正文倾向"}
            </button>
          </div>
          {showTendency && (
            <div style={{ marginBottom: 10 }}>
              <TendencySelector node="chapter" value={genTendency} onChange={setGenTendency} compact />
            </div>
          )}
          {outlines.map((o) => {
            const ch = byNum.get(o.chapter_number);
            const st = ch?.status ?? "empty";
            return (
              <div key={o.chapter_number} className="fact-line"
                style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ cursor: ch ? "pointer" : "default", flex: 1 }}
                  onClick={() => ch && open(o.chapter_number)}>
                  <b>第{o.chapter_number}章</b> {o.title}
                  <span className={"badge " + (ch?.is_stale ? "err" : st === "finalized" ? "ok" : "")}>
                    {ch?.is_stale ? "大纲已变" : STATUS_CN[st] ?? st}
                  </span>
                  {ch && <span className="muted"> {ch.word_count}字</span>}
                </span>
                <button disabled={!!busy} onClick={() => generate(o.chapter_number)}>
                  {ch ? "重写" : "生成"}
                </button>
              </div>
            );
          })}
        </div>
        {busy && <div className="card muted"><span className="spin" />{busy}</div>}
        {err && <div className="msg-err">{err}</div>}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {genResult && (
          <div className="card" style={{ background: "#f8fffa" }}>
            <b>生成完成</b> {genResult.word_count} 字
            {genResult.consistency_issues.length
              ? <div style={{ marginTop: 6 }}>
                  <span className="badge err">一致性问题 {genResult.consistency_issues.length}</span>
                  {genResult.consistency_issues.map((i, k) => (
                    <div key={k} className="fact-line">
                      <b>[{i.severity}]</b> {i.description}
                      <div className="muted">建议: {i.suggestion}</div>
                    </div>
                  ))}
                </div>
              : <span className="badge ok">一致性检查通过</span>}
          </div>
        )}

        {current ? (
          <>
            {currentOutline && (
              <div className="card" style={{ background: "#fafbff" }}>
                <b>本章蓝图</b> 第{currentOutline.chapter_number}章《{currentOutline.title}》
                <span className="badge">{currentOutline.chapter_role}</span>
                <div className="muted" style={{ marginTop: 4 }}>{currentOutline.summary}</div>
                <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
                  伏笔:{currentOutline.foreshadowing || "无"}
                </div>
              </div>
            )}
            <div className="card">
              <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
                <h2 style={{ flex: 1, margin: 0 }}>
                  第{current.chapter_number}章 正文
                  <span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>{current.word_count}字</span>
                </h2>
                <span className="muted">要改文笔?去左侧「润色」工作区</span>
              </div>
              <div className="prose">{current.final_content || current.draft_content || "(空)"}</div>
            </div>
          </>
        ) : (
          <div className="card muted">
            左侧点「生成」写新章,或点章节标题阅读。生成时自动注入:本章蓝图、前情摘要、
            最近章节结尾、人物当前状态(硬约束)、到期伏笔提醒、重复用词避免清单。
          </div>
        )}
      </div>
    </div>
  );
}
