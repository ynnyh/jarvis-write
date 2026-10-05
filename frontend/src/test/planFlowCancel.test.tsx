// PlanFlow 终止等待:LLM 超时放宽到分钟级后,三问/方案等待区必须有出口——
// 「终止等待」按钮 abort 当前请求,回空闲态(2026-10-05 月哥拍板)。
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import PlanFlow from "../pages/onboarding/PlanFlow";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual };
});

afterEach(() => cleanup());

function baseProps(overrides: Partial<Parameters<typeof PlanFlow>[0]> = {}) {
  return {
    pid: 1,
    mode: "serial",
    topic: "",
    briefText: "",
    briefConfirmed: false,
    questions: null,
    qAnswers: {},
    plans: null,
    selectedPlan: null,
    planBusy: "",
    planFeedback: "",
    onQuestions: vi.fn(),
    onCancelPlan: vi.fn(),
    onAnswer: vi.fn(),
    onAdoptAll: vi.fn(),
    onGenPlans: vi.fn(),
    onRevise: vi.fn(),
    onConfirmPlan: vi.fn(),
    onUnconfirm: vi.fn(),
    onFeedback: vi.fn(),
    onSelect: vi.fn(),
    onBack: vi.fn(),
    onGotoConcept: vi.fn(),
    ...overrides,
  } as Parameters<typeof PlanFlow>[0];
}

describe("PlanFlow 终止等待", () => {
  it("三问等待中显示「终止等待」,点击触发取消回调", () => {
    const onCancelPlan = vi.fn();
    render(<PlanFlow {...baseProps({ planBusy: "AI 正在出三问候选…", onCancelPlan })} />);
    fireEvent.click(screen.getByRole("button", { name: "终止等待" }));
    expect(onCancelPlan).toHaveBeenCalledTimes(1);
  });

  it("非等待态不显示终止按钮", () => {
    render(<PlanFlow {...baseProps({ planBusy: "" })} />);
    expect(screen.queryByRole("button", { name: "终止等待" })).toBeNull();
  });
});

describe("PlanFlow 三问正向引导", () => {
  const questions = [
    { key: "q1", title: "写什么味道", candidates: [
      { text: "都市异闻·冷峻悬疑", recommended: true, reason: "贴题材" },
    ] },
  ];

  it("三问屏有「跟 AI 说一句」引导输入,输入即回传 onFeedback", () => {
    const onFeedback = vi.fn();
    render(<PlanFlow {...baseProps({ questions, onFeedback })} />);
    const input = screen.getByPlaceholderText(/跟 AI 说一句想要什么/);
    fireEvent.change(input, { target: { value: "想要电台/声音类的点子" } });
    expect(onFeedback).toHaveBeenCalledWith("想要电台/声音类的点子");
  });

  it("引导输入回显当前 planFeedback 值", () => {
    render(<PlanFlow {...baseProps({ questions, planFeedback: "不要警察主角" })} />);
    expect(screen.getByDisplayValue("不要警察主角")).toBeTruthy();
  });
});

describe("PlanFlow 方案墙拍板出口", () => {
  const plan = {
    title: "第七份笔录", kernel: "k", protagonist: "p", world: "w", arc: "a",
    engine: "e", ending: "", flavor: [], scale: "", scale_reason: "", label: "",
    opening: "", payoff: "", escalation: "", mechanism: "", opening_sample: "",
  };

  it("选中方案后,方案墙正下方出现主按钮拍板出口,点击回调 onConfirmPlan(0)", () => {
    const onConfirmPlan = vi.fn();
    render(<PlanFlow {...baseProps({ plans: [plan], selectedPlan: 0, onConfirmPlan })} />);
    const btn = screen.getByRole("button", { name: /拍板进入概念深化/ });
    fireEvent.click(btn);
    expect(onConfirmPlan).toHaveBeenCalledWith(0);
  });

  it("未选中时不显示拍板行动条,主出口缺席", () => {
    render(<PlanFlow {...baseProps({ plans: [plan], selectedPlan: null })} />);
    expect(screen.queryByRole("button", { name: /拍板进入概念深化/ })).toBeNull();
  });
});
