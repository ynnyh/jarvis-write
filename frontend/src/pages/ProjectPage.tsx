// 项目工作台:左侧创作流程导航(灵感→架构→大纲→写作→润色→看板)
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, Architecture, ChapterBrief, Outline, Project } from "../api";
import InspirePanel from "../panels/InspirePanel";
import ArchPanel from "../panels/ArchPanel";
import OutlinePanel from "../panels/OutlinePanel";
import ChaptersPanel from "../panels/ChaptersPanel";
import PolishPanel from "../panels/PolishPanel";
import BoardPanel from "../panels/BoardPanel";

export type Step = "inspire" | "arch" | "outline" | "write" | "polish" | "board";

const STEPS: { key: Step; no: number; label: string }[] = [
  { key: "inspire", no: 1, label: "灵感" },
  { key: "arch", no: 2, label: "架构" },
  { key: "outline", no: 3, label: "大纲" },
  { key: "write", no: 4, label: "写作" },
  { key: "polish", no: 5, label: "润色" },
  { key: "board", no: 6, label: "看板" },
];

export default function ProjectPage() {
  const { id } = useParams();
  const pid = Number(id);
  const [project, setProject] = useState<Project | null>(null);
  const [arch, setArch] = useState<Architecture | null>(null);
  const [outlines, setOutlines] = useState<Outline[]>([]);
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [step, setStep] = useState<Step | null>(null);
  const [err, setErr] = useState("");

  const reload = useCallback(async () => {
    const p = await api.getProject(pid);
    setProject(p);
    try { setArch(await api.getArchitecture(pid)); } catch { setArch(null); }
    setOutlines(await api.listOutlines(pid));
    setChapters(await api.listChapters(pid));
  }, [pid]);

  useEffect(() => {
    reload()
      .then(() => setStep((s) => s ?? null))
      .catch((e) => setErr(String(e)));
  }, [reload]);

  // 首次进入:按进度定位到该干活的环节
  useEffect(() => {
    if (step !== null || project === null) return;
    if (!project.topic) setStep("inspire");
    else if (!arch) setStep("arch");
    else if (!outlines.length) setStep("outline");
    else setStep("write");
  }, [project, arch, outlines, step]);

  if (!project) return <div className="muted">{err || "加载中…"}</div>;

  const wordsTotal = chapters.reduce((s, c) => s + c.word_count, 0);
  const staleCount = chapters.filter((c) => c.is_stale).length;
  const doneCount = chapters.filter((c) => c.status === "finalized" || c.status === "stale").length;

  return (
    <>
      <h1>{project.title}
        <span className="badge">{project.status}</span>
        {project.genre && <span className="badge">{project.genre}</span>}
      </h1>
      <div className="stat-strip">
        <div className="stat">主题<b className="stat-topic">{project.topic || "(未定,先去灵感区)"}</b></div>
        <div className="stat">大纲<b>{outlines.length}/{project.target_chapters} 章</b></div>
        <div className="stat">正文<b>{doneCount} 章 · {wordsTotal} 字</b></div>
        {staleCount > 0 && <div className="stat">失配<b className="stat-alert">{staleCount} 章</b></div>}
        {doneCount > 0 && (
          <div className="stat">导出
            <b className="stat-links">
              <a href={`/api/projects/${pid}/export/txt`}>txt</a>
              {" · "}
              <a href={`/api/projects/${pid}/export/epub`}>epub</a>
            </b>
          </div>
        )}
      </div>

      <div className="workbench">
        <div className="flow-nav">
          {STEPS.map((s) => (
            <div key={s.key}
              className={"flow-step" + (step === s.key ? " on" : "")}
              onClick={() => setStep(s.key)}>
              <span className="no">{s.no}</span>
              {s.label}
              {s.key === "write" && staleCount > 0 && <span className="dot" title="有章节与新大纲不符" />}
            </div>
          ))}
        </div>

        <div className="flow-main">
          {step === "inspire" && <InspirePanel project={project} onChanged={reload} />}
          {step === "arch" && <ArchPanel project={project} arch={arch} onChanged={reload} />}
          {step === "outline" && (
            <OutlinePanel pid={pid} outlines={outlines} hasArch={!!arch} onChanged={reload} onGotoStep={setStep} />
          )}
          {step === "write" && (
            outlines.length
              ? <ChaptersPanel pid={pid} project={project} outlines={outlines} />
              : <div className="card muted">先在「大纲」生成章节蓝图,才能开始写作。</div>
          )}
          {step === "polish" && <PolishPanel pid={pid} />}
          {step === "board" && (
            outlines.length
              ? <BoardPanel pid={pid} outlines={outlines} />
              : <div className="card muted">生成章节后,这里会展示故事圣经与伏笔追踪。</div>
          )}
        </div>
      </div>
    </>
  );
}
