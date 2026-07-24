// 项目工作台:左侧创作流程导航(灵感→架构→大纲→写作→润色→看板)
// 当前步骤进 URL(/project/:id/:step),刷新/后退/分享链接都不丢位置
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useProject, useArchitecture, useOutlines, useChapters, useInvalidateProject } from "../hooks/queries";
import { downloadFile } from "../api";
import { toast } from "../ui/Toaster";
import InspirePanel from "../panels/InspirePanel";
import ArchPanel from "../panels/ArchPanel";
import OutlinePanel from "../panels/OutlinePanel";
import ChaptersPanel from "../panels/ChaptersPanel";
import EditorialPanel from "../panels/EditorialPanel";
import BoardPanel from "../panels/BoardPanel";
import SubmissionPanel from "../panels/SubmissionPanel";
import BookReader from "../components/BookReader";

export type Step = "inspire" | "arch" | "outline" | "write" | "polish" | "board" | "publish";

const STEPS: { key: Step; no: number; label: string }[] = [
  { key: "inspire", no: 1, label: "概念" },
  { key: "arch", no: 2, label: "架构" },
  { key: "outline", no: 3, label: "大纲" },
  { key: "write", no: 4, label: "写作" },
  { key: "polish", no: 5, label: "编辑部" },
  { key: "board", no: 6, label: "看板" },
  { key: "publish", no: 7, label: "投稿" },
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
    what: "编辑部三审:主编打分给建议、校对抓错别字语病、审核盯一致性;还有润色工作台精修文字。",
    ai: "主编按情节/文笔/节奏/人物打分并给出最该改的三件事;校对的修复逐条勾选才生效。",
    done: "可选步骤,建议每写完几章来过一遍。",
  },
  board: {
    what: "全书仪表盘:章节地图、人物卡、伏笔时间线、故事圣经。",
    ai: "数据由一致性引擎自动维护,发现伏笔悬空或章节失配会在这里亮出来。",
    done: "随时可看,不阻塞任何步骤。",
  },
  publish: {
    what: "把全书压缩成投稿表单要的内容:书名、标签、金句、简介、封面提示词,并多格式导出正文。",
    ai: "AI 依据概念/架构/大纲一次产出候选,挑好微调后逐项复制去平台发表。",
    done: "可选步骤,有定稿章节后即可投稿。",
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

  // React Query 数据获取(替代手动 useState + reload)
  const { data: project, error: projectErr } = useProject(pid);
  const { data: arch } = useArchitecture(pid);
  const { data: outlines = [] } = useOutlines(pid);
  const { data: chapters = [] } = useChapters(pid);
  const reload = useInvalidateProject(pid);

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

  // URL 未带步骤(旧链接/首次进入):按进度定位到该干活的环节
  useEffect(() => {
    if (!project) return;
    // 起步流未完成的草稿:回到起步流继续
    if (project.setup_state) {
      nav(`/new/${pid}/${project.setup_state}`, { replace: true });
      return;
    }
    if (step !== null) return;
    let target: Step;
    if (!project.topic) target = "inspire";
    else if (!arch) target = "arch";
    else if (!outlines.length) target = "outline";
    else target = "write";
    nav(`/project/${pid}/${target}`, { replace: true });
  }, [project, arch, outlines, step, nav, pid]);

  if (!project) return <div className="muted">{projectErr ? String(projectErr) : "加载中…"}</div>;

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
  // 进度地图:每步的完成度小字
  const stepSub: Partial<Record<Step, string>> = {
    inspire: project.topic ? "已定" : "未定",
    arch: arch ? `v${arch.version}` : "未生成",
    outline: outlines.length ? `${outlines.length}/${project.target_chapters} 章` : "未生成",
    write: doneCount ? `${doneCount} 章 · ${Math.round(wordsTotal / 10000 * 10) / 10}万字` : "未开始",
  };
  const NEXT: Partial<Record<Step, { to: Step; label: string }>> = {
    inspire: { to: "arch", label: "去架构 →" },
    arch: { to: "outline", label: "去大纲 →" },
    outline: { to: "write", label: "去写作 →" },
  };
  const nextHint = step && stepDone[step] ? NEXT[step] : undefined;

  // 智能下一步建议:按项目状态只提示一件最该做的事
  const plannedUpto = outlines.length ? Math.max(...outlines.map((o) => o.chapter_number)) : 0;
  const suggestion: { text: string; to: Step; btn: string } | null = (() => {
    if (!project.topic) return { text: "先把故事概念定下来——整本书的地基。", to: "inspire", btn: "去定概念" };
    if (!arch) return { text: "概念已定,让 AI 生成全书架构(核心种子/角色/世界观/情节)。", to: "arch", btn: "去生成架构" };
    if (!outlines.length) return { text: "架构就绪,下一步把它展开成逐章蓝图。", to: "outline", btn: "去生成大纲" };
    if (staleCount > 0) return { text: `有 ${staleCount} 章正文与新大纲失配,建议优先处理。`, to: "write", btn: "去查看" };
    // 滚动规划:快写到已规划边界且全书还没铺满 → 提示展开下一卷
    if (plannedUpto < project.target_chapters && doneCount >= outlines.length - 2)
      return { text: `即将写到已规划边界(第 ${plannedUpto} 章),按实际剧情展开下一卷蓝图吧。`, to: "outline", btn: "展开下一卷" };
    if (doneCount < outlines.length) return { text: `已写 ${doneCount}/${outlines.length} 章,继续写下一章,或勾选多章排队连写。`, to: "write", btn: "去写作" };
    return null;
  })();

  // 头部快捷导出:走鉴权下载(普通 <a> 不带 token 会 401)
  function exportBook(path: string, ext: string) {
    downloadFile(`/api/projects/${pid}/${path}`, `${project?.title || pid}.${ext}`)
      .catch((e) => toast.err("导出失败", String(e)));
  }

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
              <a href={`/api/projects/${pid}/export/txt`}
                onClick={(e) => { e.preventDefault(); exportBook("export/txt", "txt"); }}>txt</a>
              {" · "}
              <a href={`/api/projects/${pid}/export/epub`}
                onClick={(e) => { e.preventDefault(); exportBook("export/epub", "epub"); }}>epub</a>
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
              <span className="flow-label">
                {s.label}
                {stepSub[s.key] && <span className="flow-sub">{stepSub[s.key]}</span>}
              </span>
              {s.key === "write" && staleCount > 0 && <span className="dot" title="有章节与新大纲不符" />}
            </div>
          ))}
        </div>

        <div className="flow-main">
          {suggestion && step !== suggestion.to && (
            <div className="next-bar">
              <span>💡 {suggestion.text}</span>
              <button className="btn-sm primary" onClick={() => setStep(suggestion.to)}>
                {suggestion.btn}
              </button>
            </div>
          )}
          {step && (
            <StepGuide step={step} next={nextHint?.label}
              onNext={nextHint ? () => setStep(nextHint.to) : undefined} />
          )}          {step === "inspire" && <InspirePanel project={project} onChanged={reload} onGotoStep={setStep} />}
          {step === "arch" && <ArchPanel project={project} arch={arch ?? null} onChanged={reload} hasContent={!!arch || doneCount > 0} />}
          {step === "outline" && (
            <OutlinePanel pid={pid} project={project} outlines={outlines} hasArch={!!arch} onChanged={reload} onGotoStep={setStep} />
          )}
          {step === "write" && (
            outlines.length
              ? <ChaptersPanel pid={pid} project={project} outlines={outlines}
                  focusChapter={focusChapter} onFocusConsumed={() => setFocusChapter(null)} />
              : <div className="card muted">先在「大纲」生成章节蓝图,才能开始写作。</div>
          )}
          {step === "polish" && <EditorialPanel pid={pid} />}
          {step === "board" && (
            outlines.length
              ? <BoardPanel pid={pid} outlines={outlines}
                  onGotoChapter={(n) => { setStep("write"); setFocusChapter(n); }} />
              : <div className="card muted">生成章节后,这里会展示故事圣经与伏笔追踪。</div>
          )}
          {step === "publish" && <SubmissionPanel pid={pid} project={project} />}
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
