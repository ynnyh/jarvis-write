// 项目工作台:左侧创作流程导航(灵感→架构→大纲→写作→润色→看板)
// 当前步骤进 URL(/project/:id/:step),刷新/后退/分享链接都不丢位置
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, Architecture, ChapterBrief, Outline, Project } from "../api";
import InspirePanel from "../panels/InspirePanel";
import ArchPanel from "../panels/ArchPanel";
import OutlinePanel from "../panels/OutlinePanel";
import ChaptersPanel from "../panels/ChaptersPanel";
import PolishPanel from "../panels/PolishPanel";
import BoardPanel from "../panels/BoardPanel";
import BookReader from "../components/BookReader";

export type Step = "inspire" | "arch" | "outline" | "write" | "polish" | "board";

const STEPS: { key: Step; no: number; label: string }[] = [
  { key: "inspire", no: 1, label: "灵感" },
  { key: "arch", no: 2, label: "架构" },
  { key: "outline", no: 3, label: "大纲" },
  { key: "write", no: 4, label: "写作" },
  { key: "polish", no: 5, label: "润色" },
  { key: "board", no: 6, label: "看板" },
];

// 各步引导:这一步干什么 / AI 会做什么 / 做完标准是什么
const GUIDES: Record<Step, { what: string; ai: string; done: string }> = {
  inspire: {
    what: "把模糊的想法捏成结构化「故事概念」——整本书的地基。",
    ai: "AI 可以给你 4 个差异化方案、按你的指令局部改、或边聊边帮你捏。",
    done: "点「定为本书概念」后即完成,可去下一步。",
  },
  arch: {
    what: "按雪花写作法生成全书顶层设计:核心种子、角色动力学、世界观、情节架构。",
    ai: "AI 依据你的概念和倾向一次产出四块,每块都可手动改。",
    done: "四块内容你都认可后,即可去「大纲」。",
  },
  outline: {
    what: "把架构展开成逐章蓝图:每章的目的、悬念、伏笔、出场人物。",
    ai: "AI 分块生成全部章节;之后可逐章编辑或一句话指令修改,大改会自动做级联影响分析。",
    done: "章节蓝图生成完毕即可开始「写作」。",
  },
  write: {
    what: "逐章生成正文。建议按顺序写,前文会作为上下文保证连贯。",
    ai: "AI 按蓝图写正文并维护一致性;不满意可带反馈重写,旧版会留快照可回退。",
    done: "定稿的章节会计入总字数,可随时在「看板」查看全书状态。",
  },
  polish: {
    what: "对已写好的段落做精修:润色文字、保持事实一致。",
    ai: "AI 润色时会锁定既有设定不改动,润色稿应用前可手动微调。",
    done: "可选步骤,想发布前再来也行。",
  },
  board: {
    what: "全书仪表盘:章节地图、人物卡、伏笔时间线、故事圣经。",
    ai: "数据由一致性引擎自动维护,发现伏笔悬空或章节失配会在这里亮出来。",
    done: "随时可看,不阻塞任何步骤。",
  },
};

// 引导条:默认展开,可收起(收起状态存 localStorage,全项目共享)
function StepGuide({ step, next, onNext }: { step: Step; next?: string; onNext?: () => void }) {
  const g = GUIDES[step];
  const [hidden, setHidden] = useState(() => localStorage.getItem("guide-hidden") === "1");
  if (hidden) {
    return (
      <div className="guide-mini muted" onClick={() => { localStorage.removeItem("guide-hidden"); setHidden(false); }}>
        ⓘ 本步说明
      </div>
    );
  }
  return (
    <div className="notice notice-info step-guide">
      <div className="guide-body">
        <div><b>这一步:</b>{g.what}</div>
        <div><b>AI 会:</b>{g.ai}</div>
        <div><b>完成标准:</b>{g.done}</div>
      </div>
      <div className="guide-side">
        {next && onNext && <button className="btn-sm primary" onClick={onNext}>{next}</button>}
        <button className="btn-sm" onClick={() => { localStorage.setItem("guide-hidden", "1"); setHidden(true); }}>
          收起
        </button>
      </div>
    </div>
  );
}

export default function ProjectPage() {
  const { id, step: stepParam } = useParams();
  const pid = Number(id);
  const nav = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [arch, setArch] = useState<Architecture | null>(null);
  const [outlines, setOutlines] = useState<Outline[]>([]);
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [err, setErr] = useState("");
  // 全书阅读模式(有已生成章节时,标题行出现「阅读全书」入口)
  const [readingBook, setReadingBook] = useState(false);
  // 看板「概览」点章节格子 → 跳写作步并打开该章(消费后清空)
  const [focusChapter, setFocusChapter] = useState<number | null>(null);

  // 当前步骤来自 URL;非法值当作未指定
  const VALID_STEPS = STEPS.map((s) => s.key);
  const step: Step | null = VALID_STEPS.includes(stepParam as Step)
    ? (stepParam as Step)
    : null;
  const setStep = useCallback(
    (s: Step) => nav(`/project/${pid}/${s}`),
    [nav, pid],
  );

  const reload = useCallback(async () => {
    const p = await api.getProject(pid);
    setProject(p);
    try { setArch(await api.getArchitecture(pid)); } catch { setArch(null); }
    setOutlines(await api.listOutlines(pid));
    setChapters(await api.listChapters(pid));
  }, [pid]);

  useEffect(() => {
    reload().catch((e) => setErr(String(e)));
  }, [reload]);

  // URL 未带步骤(旧链接/首次进入):按进度定位到该干活的环节
  useEffect(() => {
    if (step !== null || project === null) return;
    let target: Step;
    if (!project.topic) target = "inspire";
    else if (!arch) target = "arch";
    else if (!outlines.length) target = "outline";
    else target = "write";
    nav(`/project/${pid}/${target}`, { replace: true });
  }, [project, arch, outlines, step, nav, pid]);

  if (!project) return <div className="muted">{err || "加载中…"}</div>;

  const wordsTotal = chapters.reduce((s, c) => s + c.word_count, 0);
  const staleCount = chapters.filter((c) => c.is_stale).length;
  const doneCount = chapters.filter((c) => c.status === "finalized" || c.status === "stale").length;

  // 各步完成态:导航打勾 + 引导条给出「下一步」按钮
  const stepDone: Partial<Record<Step, boolean>> = {
    inspire: !!project.topic,
    arch: !!arch,
    outline: outlines.length > 0,
    write: doneCount > 0,
  };
  const NEXT: Partial<Record<Step, { to: Step; label: string }>> = {
    inspire: { to: "arch", label: "去架构 →" },
    arch: { to: "outline", label: "去大纲 →" },
    outline: { to: "write", label: "去写作 →" },
  };
  const nextHint = step && stepDone[step] ? NEXT[step] : undefined;

  return (
    <>
      <h1 className="project-head"><span className="project-title-text">{project.title}</span>
        <span className="badge">{project.status}</span>
        {project.genre && <span className="badge">{project.genre}</span>}
        {chapters.length > 0 && (
          <button className="primary read-book-btn" onClick={() => setReadingBook(true)}>
            阅读全书
          </button>
        )}
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
              className={"flow-step" + (step === s.key ? " on" : "") + (stepDone[s.key] ? " step-done" : "")}
              onClick={() => setStep(s.key)}>
              <span className="no">{stepDone[s.key] ? "✓" : s.no}</span>
              {s.label}
              {s.key === "write" && staleCount > 0 && <span className="dot" title="有章节与新大纲不符" />}
            </div>
          ))}
        </div>

        <div className="flow-main">
          {step && (
            <StepGuide step={step} next={nextHint?.label}
              onNext={nextHint ? () => setStep(nextHint.to) : undefined} />
          )}          {step === "inspire" && <InspirePanel project={project} onChanged={reload} onGotoStep={setStep} />}
          {step === "arch" && <ArchPanel project={project} arch={arch} onChanged={reload} />}
          {step === "outline" && (
            <OutlinePanel pid={pid} outlines={outlines} hasArch={!!arch} onChanged={reload} onGotoStep={setStep} />
          )}
          {step === "write" && (
            outlines.length
              ? <ChaptersPanel pid={pid} project={project} outlines={outlines}
                  focusChapter={focusChapter} onFocusConsumed={() => setFocusChapter(null)} />
              : <div className="card muted">先在「大纲」生成章节蓝图,才能开始写作。</div>
          )}
          {step === "polish" && <PolishPanel pid={pid} />}
          {step === "board" && (
            outlines.length
              ? <BoardPanel pid={pid} outlines={outlines}
                  onGotoChapter={(n) => { setStep("write"); setFocusChapter(n); }} />
              : <div className="card muted">生成章节后,这里会展示故事圣经与伏笔追踪。</div>
          )}
        </div>
      </div>

      {readingBook && chapters.length > 0 && (
        <BookReader
          pid={pid}
          project={project}
          outlines={outlines}
          chapters={chapters}
          onClose={() => setReadingBook(false)}
        />
      )}
    </>
  );
}
