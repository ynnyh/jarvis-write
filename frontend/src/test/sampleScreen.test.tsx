// SampleScreen(样稿屏,docs/32 屏 3)用例:
//  样稿链五步进度渲染 / 三章正文卡 / 确认② 四个出口各自触发正确的请求。
// 四个出口是这一屏的全部出口(继续写 / 回去改 / 重出样稿 / 单章带话重写),
// 走错一个就是「点了没反应」或「改错东西」,所以逐个钉住。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.hoisted:vi.mock 的工厂会被提升到 import 之前执行,普通 const 那时还在 TDZ
const mocks = vi.hoisted(() => ({
  getProject: vi.fn(),
  runningJobs: vi.fn(),
  listOutlines: vi.fn(),
  listChapters: vi.fn(),
  getChapter: vi.fn(),
  getJob: vi.fn(),
  sampleChain: vi.fn(),
  fullChain: vi.fn(),
  generateChapterAsync: vi.fn(),
  followJobLive: vi.fn(() => new Promise<void>(() => {})),
}));
vi.mock("../api", () => ({ api: mocks }));

import SampleScreen from "../pages/onboarding/SampleScreen";

const OUTLINES = [1, 2, 3].map((n) => ({
  id: n, chapter_number: n, title: `第${n}章标题`, summary: `第${n}章的一句话`,
  chapter_role: "", chapter_purpose: "", suspense_level: "", foreshadowing: "",
  emotional_tone: "", plot_twist_level: "", characters_involved: null,
  key_items: [], scene_location: "", current_version: 1, scene_anchor: "",
}));
const CHAPTERS = [1, 2, 3].map((n) => ({
  chapter_number: n, status: "draft", word_count: 3000, is_stale: false,
}));

function renderScreen(over: Partial<Parameters<typeof SampleScreen>[0]> = {}) {
  const onBackToIntent = vi.fn();
  const onEnterWorkbench = vi.fn();
  const utils = render(
    <SampleScreen pid={7} initialJobId={null}
      onBackToIntent={onBackToIntent} onEnterWorkbench={onEnterWorkbench} {...over} />,
  );
  return { ...utils, onBackToIntent, onEnterWorkbench };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getProject.mockResolvedValue({
    id: 7, title: "假账", topic: "账房先生查一桩假账", genre: "民国悬疑",
    target_chapters: 30, target_words_per_chapter: 3000,
    global_tendency: { genre: "民国悬疑" }, status: "draft", brief_confirmed: true,
  });
  mocks.runningJobs.mockResolvedValue({ jobs: [] });
  mocks.listOutlines.mockResolvedValue(OUTLINES);
  mocks.listChapters.mockResolvedValue(CHAPTERS);
  // 轮询到「已完成」:用例不关心任务本身,只关心出口发了什么请求
  mocks.getJob.mockResolvedValue({ status: "done", stage: "", result: null, error: null });
});
afterEach(() => cleanup());

describe("SampleScreen 样稿进度与正文", () => {
  it("样稿链五步都在屏上,已有三章正文时正文步亮起", async () => {
    renderScreen();
    expect(await screen.findByText("先写三章给你看")).toBeTruthy();
    for (const label of ["概念", "梗卡", "核心种子", "前三章蓝图", "前三章正文"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    // 三章正文卡与阅读入口(逐章按钮数 = 章数)
    expect(await screen.findByRole("button", { name: /读样稿/ })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "读这一章" })).toHaveLength(3);
  });

  it("还没有正文时如实显示空态,不假装在跑", async () => {
    mocks.listChapters.mockResolvedValue([]);
    mocks.listOutlines.mockResolvedValue([]);
    renderScreen();
    expect(await screen.findByText(/还没有正文/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "读这一章" })).toBeNull();
  });

  it("回访时从 running-jobs 接上正在跑的样稿链(不重起一条)", async () => {
    mocks.listChapters.mockResolvedValue([]);
    mocks.listOutlines.mockResolvedValue([]);
    mocks.runningJobs.mockResolvedValue({
      jobs: [{ job_id: "job-sample-9", kind: "sample-chain-7", stage: "铺前三章蓝图" }],
    });
    renderScreen();
    // 接上的表现:顶部出现「正在写样稿」横幅,而不是空态/重新起链
    expect(await screen.findByText("正在写样稿…")).toBeTruthy();
    expect(mocks.sampleChain).not.toHaveBeenCalled();
  });
});

describe("SampleScreen 确认② 四个出口", () => {
  it("出口 1「读着对味,继续写」→ full-chain,然后进工作台", async () => {
    mocks.fullChain.mockResolvedValue({ job_id: "job-full-1" });
    const { onEnterWorkbench } = renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: /读着对味,继续写整本/ }));
    await waitFor(() => expect(mocks.fullChain).toHaveBeenCalledWith(7));
    await waitFor(() => expect(onEnterWorkbench).toHaveBeenCalledTimes(1));
  });

  it("出口 2「方向不对,回去改」→ 回理解屏", async () => {
    const { onBackToIntent } = renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "方向不对,回去改" }));
    expect(onBackToIntent).toHaveBeenCalledTimes(1);
    expect(mocks.sampleChain).not.toHaveBeenCalled();
  });

  it("出口 3「开场不对,重出样稿」→ sample-chain restart=blueprint,带一句话", async () => {
    mocks.sampleChain.mockResolvedValue({ job_id: "job-sample-2" });
    renderScreen();
    const box = await screen.findByPlaceholderText(/开场不对\?说一句想要的开场/);
    fireEvent.change(box, { target: { value: "直接从火场开始" } });
    fireEvent.click(screen.getByRole("button", { name: /开场不对,重出样稿/ }));
    await waitFor(() => expect(mocks.sampleChain).toHaveBeenCalledWith(7, {
      restart: "blueprint", directive: "直接从火场开始",
    }));
  });

  it("出口 3 不带话时 directive 为空(概念与种子不动,只重出蓝图正文)", async () => {
    mocks.sampleChain.mockResolvedValue({ job_id: "job-sample-3" });
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: /开场不对,重出样稿/ }));
    await waitFor(() => expect(mocks.sampleChain).toHaveBeenCalledWith(7, {
      restart: "blueprint", directive: undefined,
    }));
  });

  it("出口 4「第 N 章不对」→ 单章带话重写(既有 revision 通道)", async () => {
    mocks.generateChapterAsync.mockResolvedValue({ job_id: "job-ch-2" });
    renderScreen();
    const open = (await screen.findAllByRole("button", { name: /第 \d 章不对/ }))[1];
    fireEvent.click(open);
    const box = await screen.findByPlaceholderText(/这章哪里不对/);
    fireEvent.change(box, { target: { value: "开场太慢" } });
    fireEvent.click(screen.getByRole("button", { name: /按这句重写这一章/ }));
    await waitFor(() => expect(mocks.generateChapterAsync).toHaveBeenCalledWith(
      7, 2, { genre: "民国悬疑" }, "开场太慢"));
  });
});
