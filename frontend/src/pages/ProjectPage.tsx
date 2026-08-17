// 项目工作台:三区信息架构(开书 setup / 写作 write / 全书 book)+ 设置 settings + 只读 read。
// 区进 URL(/project/:id/:step,step 为区名);setup 子步 ?step=、book 页签 ?tab=、
// 当前章 ?ch=、write 区动作卡 ?act= 全部进 URL,刷新/后退/分享不丢位置(?ctx= 已随参考抽屉废除)。
// 旧八步链接(inspire/arch/outline/write/polish/refresh/board/publish)一律 <Navigate replace> 重定向。
import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useProject, useArchitecture, useOutlines, useChapters, useInvalidateProject } from "../hooks/queries";
import { useBreakpoint } from "../hooks/useBreakpoint";
import { useDesktopHotkeys } from "../hooks/useDesktopHotkeys";
import { AppAction, dispatchAction, isAppAction, registerActionHandler } from "../ui/actions";
import CommandPalette from "../ui/CommandPalette";
import { setThemePref } from "../theme";
import { isDesktop, onMenuAction, openReadWindow } from "../desktop";
import { downloadFile } from "../api";
import { toast } from "../ui/Toaster";
import InspirePanel from "../panels/InspirePanel";
import ArchPanel from "../panels/ArchPanel";
import OutlinePanel from "../panels/OutlinePanel";
import WritePanel from "../panels/WritePanel";
import BoardPanel, { BoardTab } from "../panels/BoardPanel";
import AuditPanel from "../panels/AuditPanel";
import RefreshPanel from "../panels/RefreshPanel";
import SubmissionPanel from "../panels/SubmissionPanel";
import ProjectSettingsPanel from "../panels/ProjectSettingsPanel";
import ReadPanel from "../panels/ReadPanel";
import BookReader from "../components/BookReader";
import EmptyState from "../ui/EmptyState";

export type Zone = "setup" | "write" | "book" | "settings" | "read";
// setup 区子步(?step=):保留原来的向导感
export type SetupStep = "inspire" | "arch" | "outline";
// 面板内「去下一步」跳转目标:setup 子步或直接进写作区
export type GotoTarget = SetupStep | "write";
// book 区页签(?tab=)
type BookTab = BoardTab | "publish" | "audit" | "refresh";

const VALID_ZONES: Zone[] = ["setup", "write", "book", "settings", "read"];
const SETUP_STEPS: { key: SetupStep; label: string }[] = [
  { key: "inspire", label: "概念" },
  { key: "arch", label: "架构" },
  { key: "outline", label: "大纲" },
];
const BOOK_TABS: { key: BookTab; label: string }[] = [
  { key: "overview", label: "概览" },
  { key: "characters", label: "人物" },
  { key: "bible", label: "故事圣经" },
  { key: "foreshadow", label: "伏笔" },
  { key: "publish", label: "投稿" },
  { key: "audit", label: "体检" },
  { key: "refresh", label: "翻新" },
];

// 项目状态英文值 → 中文徽标(未知值原样兜底)
const PROJECT_STATUS_CN: Record<string, string> = { draft: "草稿", writing: "连载中" };

interface Guide { what: string; ai: string; done: string }

// 各区/子步引导:这一步干什么 / AI 会做什么 / 做完标准是什么
const GUIDES = {
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
    what: "逐章生成正文,主场就是正文本身:选中段落就地「改这段/手改」,读到多处问题边批注边一次改;右侧常驻 AI 窄栏梳理意见后整章优化或重写。章首「交稿单」一句话报告自检(校对/与设定有无冲突)并给出「通过/放行」,校对/评分/历史版本收在「更多」;目录(Ctrl+B/点章题)选章,连写在目录里。",
    ai: "AI 按蓝图写正文并维护一致性;改动一律 diff 逐条验收、旧版留快照可回退;校对/评分结果可一键带进验收流修复。",
    done: "定稿的章节会计入总字数,可随时在「全书」查看全书状态。",
  },
  overview: {
    what: "全书仪表盘:章节地图、人物卡、伏笔时间线、故事圣经。",
    ai: "数据由一致性引擎自动维护,发现伏笔悬空或章节失配会在这里亮出来。",
    done: "随时可看,不阻塞任何步骤。",
  },
  publish: {
    what: "把全书压缩成投稿表单要的内容:书名、标签、金句、简介、封面提示词,并多格式导出正文。",
    ai: "AI 依据概念/架构/大纲一次产出候选,挑好微调后逐项复制去平台发表。",
    done: "可选步骤,有定稿章节后即可投稿。",
  },
  audit: {
    what: "全书体检:跨章矛盾扫描、世界观规则扫描、契约批量补提与审核报告聚合。",
    ai: "LLM 逐章对照圣经/契约/硬规则体检正文,问题按来源落进各章审核报告(在写作区各章交稿单/审核报告里处理)。",
    done: "可选步骤,建议每写完一卷后跑一次。",
  },
  refresh: {
    what: "把已有书按新生成逻辑翻新:回填章节节拍、生成文风备忘、轻度重润或重度重写。",
    ai: "轻度重润锁情节去AI味、不改剧情;重度重写带节拍/文风备忘整章重跑并自动重抽圣经。",
    done: "可选步骤,适合翻新早期用旧逻辑写的章节。",
  },
} satisfies Record<string, Guide>;

// 引导条:默认展开,可收起(收起状态存 localStorage,全项目共享)
function StepGuide({ guide, next, onNext }: { guide: Guide; next?: string; onNext?: () => void }) {
  const [hidden, setHidden] = useState(() => localStorage.getItem("guide-hidden") === "1");
  if (hidden) {
    return (
      <button type="button" className="guide-mini muted" onClick={() => { localStorage.removeItem("guide-hidden"); setHidden(false); }}>
        ⓘ 本区说明
      </button>
    );
  }
  return (
    <div className="notice notice-info step-guide">
      <div className="guide-body">
        <div><b>这一步:</b>{guide.what}</div>
        <div><b>AI 会:</b>{guide.ai}</div>
        <div><b>完成标准:</b>{guide.done}</div>
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

  // 当前区来自 URL;非法值当作未指定(旧八步值在渲染期重定向,见下方 legacyTarget)
  const zone: Zone | null = VALID_ZONES.includes(stepParam as Zone)
    ? (stepParam as Zone)
    : null;
  const [searchParams] = useSearchParams();

  // 旧八步链接 → 新区 URL(不丢 ch 等 query)
  const legacyTarget: string | null = (() => {
    switch (stepParam) {
      case "inspire": case "arch": case "outline":
        return `/project/${pid}/setup?step=${stepParam}`;
      case "polish": {
        const q = searchParams.toString();
        return `/project/${pid}/write${q ? `?${q}` : ""}`;
      }
      case "refresh": return `/project/${pid}/book?tab=refresh`;
      case "board": return `/project/${pid}/book`;
      case "publish": return `/project/${pid}/book?tab=publish`;
      default: return null;
    }
  })();

  // 区切换:保留 ch(「当前章」跨区不丢),其余子参数(step/tab/act/ctx)归属各区,不带过去
  const gotoZone = useCallback(
    (z: Zone) => {
      const ch = searchParams.get("ch");
      nav(`/project/${pid}/${z}${ch && z === "write" ? `?ch=${ch}` : ""}`);
    },
    [nav, pid, searchParams],
  );

  // ---- 桌面端(D 阶段):命令面板 + 快捷键 + 全局动作 dispatch + Tauri 菜单事件口 ----
  // 快捷键与命令面板只服务桌面宽屏,isMobile 下一律不挂监听不渲染
  const { isMobile } = useBreakpoint();
  const [paletteOpen, setPaletteOpen] = useState(false);
  // 全局动作 handler:对象每次渲染重建捕获最新 searchParams 等,经 ref 交给注册一次的稳定 wrapper
  const globalHandlers: Partial<Record<AppAction, () => void>> = {
    "command-palette": () => setPaletteOpen((v) => !v),
    "goto-setup": () => gotoZone("setup"),
    "goto-write": () => gotoZone("write"),
    "goto-book": () => gotoZone("book"),
    "goto-settings": () => gotoZone("settings"),
    "goto-help": () => nav("/help"),
    "theme-light": () => setThemePref("light"),
    "theme-dark": () => setThemePref("dark"),
    "theme-auto": () => setThemePref("auto"),
    "export-txt": () => exportBook("export/txt", "txt"),
    "export-epub": () => exportBook("export/epub", "epub"),
    "open-read-window": () => {
      const raw = searchParams.get("ch");
      const ch = raw ? Number(raw) : NaN;
      if (!Number.isInteger(ch) || ch <= 0) {
        toast.info("先在写作区选一章,再开对照阅读窗");
        return;
      }
      void openReadWindow(pid, ch)
        .then((ok) => { if (!ok) toast.info("对照阅读窗仅在桌面客户端可用"); })
        .catch((e) => toast.err("打开阅读窗失败", String(e)));
    },
  };
  const globalHandlersRef = useRef(globalHandlers);
  useEffect(() => { globalHandlersRef.current = globalHandlers; });
  useEffect(() => {
    const offs = (Object.keys(globalHandlersRef.current) as AppAction[]).map((name) =>
      registerActionHandler(name, () => globalHandlersRef.current[name]?.()));
    return () => offs.forEach((off) => off());
  }, []);
  // 快捷键(仅桌面宽屏;面板打开时其余键位让位)
  useDesktopHotkeys({ enabled: !isMobile, paletteOpen });
  // Tauri 菜单事件口:Rust 侧 MenuBuilder emit menu-action,与快捷键/命令面板共用 dispatch。
  // 浏览器下 isDesktop() 为 false,静默跳过。
  useEffect(() => {
    if (!isDesktop()) return;
    let cancelled = false;
    let unlisten: (() => void) | null = null;
    onMenuAction((name) => { if (isAppAction(name)) dispatchAction(name); })
      .then((u) => { if (!cancelled) unlisten = u; })
      .catch(() => {});
    return () => { cancelled = true; unlisten?.(); };
  }, []);

  // setup 子步 / book 页签:?step= / ?tab= 进 URL,非法值回落默认
  const setupStepRaw = searchParams.get("step");
  const bookTabRaw = searchParams.get("tab");
  const setSetupStep = (s: SetupStep) => {
    const q = new URLSearchParams(searchParams);
    q.set("step", s);
    nav(`/project/${pid}/setup?${q}`);
  };
  const setBookTab = (t: BookTab) => {
    nav(`/project/${pid}/book?tab=${t}`);
  };

  // URL 未带区(旧链接/首次进入):按进度定位到该干活的环节
  useEffect(() => {
    if (!project || legacyTarget) return;
    // 起步流未完成的草稿:回到起步流继续
    if (project.setup_state) {
      nav(`/new/${pid}/${project.setup_state}`, { replace: true });
      return;
    }
    if (zone !== null) return;
    let target: string;
    if (!project.topic) target = "setup?step=inspire";
    else if (!arch) target = "setup?step=arch";
    else if (!outlines.length) target = "setup?step=outline";
    else target = "write";
    nav(`/project/${pid}/${target}`, { replace: true });
  }, [project, arch, outlines, zone, legacyTarget, nav, pid]);

  if (legacyTarget) return <Navigate to={legacyTarget} replace />;
  if (!project) return <div className="muted">{projectErr ? String(projectErr) : "加载中…"}</div>;

  const wordsTotal = chapters.reduce((s, c) => s + c.word_count, 0);
  const staleCount = chapters.filter((c) => c.is_stale).length;
  const doneCount = chapters.filter((c) =>
    c.status === "pending_review" || c.status === "approved" ||
    c.status === "quarantined" || c.status === "finalized" || c.status === "stale",
  ).length;

  // setup 子步完成态:子步导航打勾 + 引导条给出「下一步」按钮
  const setupDone: Record<SetupStep, boolean> = {
    inspire: !!project.topic,
    arch: !!arch,
    outline: outlines.length > 0,
  };
  // 子步进度小字
  const setupSub: Record<SetupStep, string> = {
    inspire: project.topic ? "已定" : "未定",
    arch: arch ? `v${arch.version}` : "未生成",
    outline: outlines.length ? `${outlines.length}/${project.target_chapters} 章` : "未生成",
  };
  // 默认子步:第一个未完成的;全部完成时落大纲(最常回看)
  const setupStep: SetupStep = SETUP_STEPS.some((s) => s.key === setupStepRaw)
    ? (setupStepRaw as SetupStep)
    : (SETUP_STEPS.find((s) => !setupDone[s.key])?.key ?? "outline");
  const bookTab: BookTab = BOOK_TABS.some((t) => t.key === bookTabRaw)
    ? (bookTabRaw as BookTab)
    : "overview";

  // 区 tab 的完成勾与进度小字
  const setupAllDone = setupDone.inspire && setupDone.arch && setupDone.outline;
  const zoneDone: Partial<Record<Zone, boolean>> = { setup: setupAllDone, write: doneCount > 0 };
  const zoneSub: Partial<Record<Zone, string>> = {
    setup: setupAllDone ? "已就绪" : `${[setupDone.inspire, setupDone.arch, setupDone.outline].filter(Boolean).length}/3 步`,
    write: doneCount ? `${doneCount} 章 · ${Math.round(wordsTotal / 10000 * 10) / 10}万字` : "未开始",
  };
  const SETUP_NEXT: Partial<Record<SetupStep, { to: SetupStep | "write"; label: string }>> = {
    inspire: { to: "arch", label: "去架构 →" },
    arch: { to: "outline", label: "去大纲 →" },
    outline: { to: "write", label: "去写作 →" },
  };
  const setupNext = zone === "setup" && setupDone[setupStep] ? SETUP_NEXT[setupStep] : undefined;

  // 智能下一步建议:按项目状态只提示一件最该做的事(to 为新区路径)
  const plannedUpto = outlines.length ? Math.max(...outlines.map((o) => o.chapter_number)) : 0;
  const suggestion: { text: string; zone: Zone; path: string; btn: string } | null = (() => {
    if (!project.topic) return { text: "先把故事概念定下来——整本书的地基。", zone: "setup", path: `/project/${pid}/setup?step=inspire`, btn: "去定概念" };
    if (!arch) return { text: "概念已定,让 AI 生成全书架构(核心种子/角色/世界观/情节)。", zone: "setup", path: `/project/${pid}/setup?step=arch`, btn: "去生成架构" };
    if (!outlines.length) return { text: "架构就绪,下一步把它展开成逐章蓝图。", zone: "setup", path: `/project/${pid}/setup?step=outline`, btn: "去生成大纲" };
    if (staleCount > 0) return { text: `有 ${staleCount} 章正文与新大纲失配,建议优先处理。`, zone: "write", path: `/project/${pid}/write`, btn: "去查看" };
    // 滚动规划:快写到已规划边界且全书还没铺满 → 提示展开下一卷
    if (plannedUpto < project.target_chapters && doneCount >= outlines.length - 2)
      return { text: `即将写到已规划边界(第 ${plannedUpto} 章),按实际剧情展开下一卷蓝图吧。`, zone: "setup", path: `/project/${pid}/setup?step=outline`, btn: "展开下一卷" };
    if (doneCount < outlines.length) return { text: `已写 ${doneCount}/${outlines.length} 章,继续写下一章,或勾选多章排队连写。`, zone: "write", path: `/project/${pid}/write`, btn: "去写作" };
    return null;
  })();

  // 头部快捷导出:走鉴权下载(普通 <a> 不带 token 会 401)
  function exportBook(path: string, ext: string) {
    downloadFile(`/api/projects/${pid}/${path}`, `${project?.title || pid}.${ext}`)
      .catch((e) => toast.err("导出失败", String(e)));
  }

  // 当前区/子位置的引导内容(setup 三个子步 + write + book 部分页签)
  const guide: Guide | undefined = (() => {
    if (zone === "setup") return GUIDES[setupStep];
    if (zone === "write") return GUIDES.write;
    if (zone === "book" && bookTab in GUIDES) return GUIDES[bookTab as keyof typeof GUIDES];
    return undefined;
  })();

  return (
    <>
      {/* read 区是纯阅读页(对照窗/移动端用):不渲染项目头、统计条与区导航 */}
      {zone !== "read" && (
        <>
          <h1 className="project-head"><span className="project-title-text">{project.title}</span>
            <span className="badge">{PROJECT_STATUS_CN[project.status] ?? project.status}</span>
            {project.genre && <span className="badge">{project.genre}</span>}
            {chapters.length > 0 && (
              <button className="primary read-book-btn" onClick={() => setReadingBook(true)}>
                阅读全书
              </button>
            )}
          </h1>
          <div className="stat-strip">
            <div className="stat">主题<b className="stat-topic">{project.topic || "(未定,先去开书区)"}</b></div>
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

          {/* 区导航:开书 / 写作 / 全书 + 右侧设置入口(read 区经 URL 进入,不占 tab) */}
          <div className="zone-nav">
            {(["setup", "write", "book"] as const).map((z) => (
              <button key={z} type="button"
                className={"zone-tab" + (zone === z ? " on" : "")}
                onClick={() => gotoZone(z)}>
                <span className="no">{zoneDone[z] ? "✓" : { setup: "壹", write: "贰", book: "叁" }[z]}</span>
                <span className="flow-label">
                  {{ setup: "开书", write: "写作", book: "全书" }[z]}
                  {zoneSub[z] && <span className="flow-sub">{zoneSub[z]}</span>}
                </span>
                {z === "write" && staleCount > 0 && <span className="dot" title="有章节与新大纲不符" />}
              </button>
            ))}
            <div className="grow" />
            <button type="button" className={"zone-tab zone-gear" + (zone === "settings" ? " on" : "")}
              title="字数守卫 / 审校把关 / 世界观硬规则"
              onClick={() => gotoZone("settings")}>
              ⚙︎ 设置
            </button>
          </div>
        </>
      )}

      <div className="flow-main">
        {suggestion && zone !== "read" && zone !== suggestion.zone && (
          <div className="next-bar">
            <span>💡 {suggestion.text}</span>
            <button className="btn-sm primary" onClick={() => nav(suggestion.path)}>
              {suggestion.btn}
            </button>
          </div>
        )}
        {guide && (
          <StepGuide guide={guide} next={setupNext?.label}
            onNext={setupNext ? () => {
              if (setupNext.to === "write") gotoZone("write");
              else setSetupStep(setupNext.to);
            } : undefined} />
        )}

        {zone === "setup" && (
          <>
            <div className="chips board-tabs">
              {SETUP_STEPS.map((s) => (
                <button key={s.key} type="button"
                  className={"chip" + (setupStep === s.key ? " on" : "")}
                  onClick={() => setSetupStep(s.key)}>
                  {setupDone[s.key] ? "✓ " : ""}{s.label}
                  <span className="muted"> {setupSub[s.key]}</span>
                </button>
              ))}
            </div>
            {setupStep === "inspire" && <InspirePanel project={project} onChanged={reload} onGotoStep={setSetupStep} />}
            {setupStep === "arch" && <ArchPanel project={project} arch={arch ?? null} onChanged={reload} hasContent={!!arch || doneCount > 0} />}
            {setupStep === "outline" && (
              <OutlinePanel pid={pid} project={project} outlines={outlines} hasArch={!!arch} onChanged={reload}
                onGotoStep={(s) => { if (s === "write") gotoZone("write"); else setSetupStep(s); }} />
            )}
          </>
        )}

        {zone === "write" && (
          outlines.length
            ? <WritePanel pid={pid} outlines={outlines} />
            : <EmptyState>先在「开书 → 大纲」生成章节蓝图,才能开始写作。</EmptyState>
        )}

        {zone === "book" && (
          <>
            <div className="chips board-tabs">
              {BOOK_TABS.map((t) => (
                <button key={t.key} type="button"
                  className={"chip" + (bookTab === t.key ? " on" : "")}
                  onClick={() => setBookTab(t.key)}>
                  {t.label}
                </button>
              ))}
            </div>
            {(bookTab === "overview" || bookTab === "characters" || bookTab === "bible" || bookTab === "foreshadow") && (
              outlines.length
                ? <BoardPanel pid={pid} outlines={outlines} tab={bookTab}
                    onGotoChapter={(n) => {
                      // 看板点章节格子 → 写作区 + ch 进 URL(write 区按 URL 打开该章)
                      nav(`/project/${pid}/write?ch=${n}`);
                    }} />
                : <EmptyState>生成章节后,这里会展示故事圣经与伏笔追踪。</EmptyState>
            )}
            {bookTab === "publish" && <SubmissionPanel pid={pid} project={project} />}
            {bookTab === "audit" && <AuditPanel pid={pid} project={project} />}
            {bookTab === "refresh" && <RefreshPanel pid={pid} />}
          </>
        )}

        {zone === "settings" && <ProjectSettingsPanel pid={pid} project={project} />}
        {zone === "read" && <ReadPanel pid={pid} outlines={outlines} />}
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

      {/* Ctrl+K 命令面板(仅桌面宽屏;isMobile 下快捷键也不挂,永远进不来) */}
      {!isMobile && paletteOpen && (
        <CommandPalette pid={pid} onClose={() => setPaletteOpen(false)} />
      )}
    </>
  );
}
