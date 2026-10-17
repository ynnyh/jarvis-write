// IntentScreen(理解屏,docs/32 屏 2)用例:
//  渲染理解卡 / 逐字段改 / 拿不准候选 chip 点选 / 确认① 的禁用与启用 / 确认请求的载荷。
// 这一屏是全链生成的最高约束入口(题材也在这里显式落库),所以除了渲染,还要钉住
// 「没选方向卡不能确认」与「确认时带的是当前卡 + 选中下标」这两条契约。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IntentCard, Project } from "../api";

// vi.hoisted:vi.mock 的工厂会被提升到 import 之前执行,普通 const 那时还在 TDZ
const mocks = vi.hoisted(() => ({
  getProject: vi.fn(),
  intentReadback: vi.fn(),
  intentConfirm: vi.fn(),
  bookPlans: vi.fn(),
  revisePlan: vi.fn(),
  threeQuestions: vi.fn(),
  // SSE 直播:测试里不真连(挂一个永不 resolve 的假流)
  followJobLive: vi.fn(() => new Promise<void>(() => {})),
}));
vi.mock("../api", () => ({ api: mocks }));

import IntentScreen from "../pages/onboarding/IntentScreen";

const CARD: IntentCard = {
  world: "民国上海,真实历史,没有超自然",
  viewpoint: "第一人称,主角是账房先生",
  cast: "主角与东家、巡捕房探长",
  flavor: ["冷硬", "市井"],
  must: ["弄堂里的日常质感"],
  avoid: ["系统", "穿越"],
  genre: "民国悬疑",
  uncertain: [
    { field: "viewpoint", reason: "没写清是第一人称还是第三人称", candidates: ["第三人称限知", "第一人称"] },
    { field: "must", reason: "不确定你更在意哪一样", candidates: ["上海话对白", "账本细节"] },
  ],
};

function project(over: Partial<Project> = {}): Project {
  return {
    id: 7, title: "未命名新书", topic: "账房先生查一桩假账", genre: "民国悬疑",
    target_chapters: 30, target_words_per_chapter: 3000,
    global_tendency: {}, status: "draft",
    intent_card: CARD, brief_confirmed: true,
    book_plans: [{
      title: "假账", kernel: "k", protagonist: "p", world: "w", arc: "a",
      flavor: [], scale: "", scale_reason: "", label: "",
    }],
    ...over,
  } as Project;
}

function renderScreen(over: Partial<Parameters<typeof IntentScreen>[0]> = {}) {
  const onConfirmed = vi.fn();
  const onBack = vi.fn();
  const utils = render(
    <IntentScreen pid={7} mode="serial" onConfirmed={onConfirmed} onBack={onBack} {...over} />,
  );
  return { ...utils, onConfirmed, onBack };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getProject.mockResolvedValue(project());
});
afterEach(() => cleanup());

describe("IntentScreen 理解卡", () => {
  it("已有理解卡时直接上屏,不再回读(不重复烧一次强档调用)", async () => {
    renderScreen();
    expect(await screen.findByText("AI 听到的是这个吗?")).toBeTruthy();
    expect(mocks.intentReadback).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/世界坐标/)).toHaveValue(CARD.world);
  });

  it("没有理解卡时自动回读一次,并把回读结果上屏", async () => {
    mocks.getProject.mockResolvedValue(project({ intent_card: null }));
    mocks.intentReadback.mockResolvedValue({ card: CARD, project: project() });
    renderScreen();
    await waitFor(() => expect(mocks.intentReadback).toHaveBeenCalledTimes(1));
    // 回读带上想法屏的话题与题材
    expect(mocks.intentReadback.mock.calls[0][1]).toMatchObject({
      mode: "serial", topic: "账房先生查一桩假账", genre: "民国悬疑",
    });
    expect(await screen.findByLabelText(/世界坐标/)).toHaveValue(CARD.world);
  });

  it("字段可逐条改:改世界坐标即写进当前卡", async () => {
    renderScreen();
    const world = await screen.findByLabelText(/世界坐标/);
    fireEvent.change(world, { target: { value: "架空王朝,但有蒸汽机" } });
    expect(world).toHaveValue("架空王朝,但有蒸汽机");
    // 改完直接确认:请求里带的是改过的卡
    fireEvent.click(screen.getByRole("button", { name: /选这套/ }));
    fireEvent.click(screen.getByRole("button", { name: /就按这个来/ }));
    await waitFor(() => expect(mocks.intentConfirm).toHaveBeenCalledTimes(1));
    expect(mocks.intentConfirm.mock.calls[0][1].card.world).toBe("架空王朝,但有蒸汽机");
  });

  it("拿不准的字段标出来,候选 chip 点选即填入(数组字段是追加)", async () => {
    renderScreen();
    // 两个拿不准字段各一枚标记
    expect((await screen.findAllByText("拿不准")).length).toBe(2);
    expect(screen.getByText(/没写清是第一人称还是第三人称/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "第三人称限知" }));
    expect(screen.getByLabelText(/视角与主角/)).toHaveValue("第三人称限知");

    const must = screen.getByLabelText(/必须保留/) as HTMLTextAreaElement;
    fireEvent.click(screen.getByRole("button", { name: "上海话对白" }));
    expect(must.value.split("\n")).toContain("上海话对白");
    expect(must.value.split("\n")).toContain("弄堂里的日常质感");
  });

  it("没选方向卡时确认① 按钮禁用并提示;选了就启用", async () => {
    renderScreen();
    const btn = await screen.findByRole("button", { name: /先挑一套方向/ });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(mocks.intentConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /^选这套$/ }));
    const ok = screen.getByRole("button", { name: /就按这个来/ });
    expect(ok).not.toBeDisabled();
  });
});

describe("IntentScreen 确认①", () => {
  it("确认成功回调带样稿 job,并把选中下标与理解卡交给后端", async () => {
    mocks.intentConfirm.mockResolvedValue({
      project: project({ setup_state: "sample" }), sample_job_id: "job-sample-1",
    });
    const { onConfirmed } = renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: /^选这套$/ }));
    fireEvent.click(screen.getByRole("button", { name: /就按这个来/ }));
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledWith(
      expect.objectContaining({ sampleJobId: "job-sample-1" })));
    expect(mocks.intentConfirm.mock.calls[0][1]).toMatchObject({ mode: "serial", index: 0 });
  });

  it("后端报错时留在本屏并显示原因", async () => {
    mocks.intentConfirm.mockRejectedValue(new Error("理解卡还是空的"));
    const { onConfirmed } = renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: /^选这套$/ }));
    fireEvent.click(screen.getByRole("button", { name: /就按这个来/ }));
    expect(await screen.findByText("理解卡还是空的")).toBeTruthy();
    expect(onConfirmed).not.toHaveBeenCalled();
  });
});

describe("IntentScreen 整卡重出", () => {
  it("按一句反馈重出:feedback 传给后端,结果替换当前卡", async () => {
    const next: IntentCard = { ...CARD, world: "真实历史:民国上海", uncertain: [] };
    mocks.intentReadback.mockResolvedValue({ card: next, project: project() });
    renderScreen();
    const box = await screen.findByPlaceholderText(/哪里读歪了/);
    fireEvent.change(box, { target: { value: "不是架空,就是真实历史" } });
    fireEvent.click(screen.getByRole("button", { name: "按这句重出理解卡" }));
    await waitFor(() => expect(mocks.intentReadback).toHaveBeenCalledTimes(1));
    expect(mocks.intentReadback.mock.calls[0][1].feedback).toBe("不是架空,就是真实历史");
    await waitFor(() => expect(screen.getByLabelText(/世界坐标/)).toHaveValue("真实历史:民国上海"));
  });
});
