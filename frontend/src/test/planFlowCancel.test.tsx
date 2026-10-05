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
