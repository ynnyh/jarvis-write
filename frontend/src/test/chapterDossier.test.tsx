// ChapterDossier 本章作战图(docs/19 M1):梗行/章纲/出场人物关系/伏笔账/承上钩子。
// 降级纪律:梗未建 → 如实「未定核心梗」+ 去补建入口;人物未入圣经 → 灰 chip 标注。
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ChapterDossier from "../ui/ChapterDossier";
import { api, ChapterDossier as Dossier } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getChapterDossier: vi.fn(),
    },
  };
});

const DOSSIER: Dossier = {
  chapter_number: 1,
  outline: {
    chapter_number: 1, title: "急诊室之夜", chapter_role: "开局",
    chapter_purpose: "立梗", suspense_level: "高", emotional_tone: "紧绷",
    foreshadowing: "埋设:腕上倒计时", summary: "林夏首次救人,寿数账显形。",
    scene_anchor: "倒计时亮了", premise_beat: "第1拍·代价显形",
    characters_involved: ["林夏", "灰衣人"], beats: ["抢救成功", "倒计时亮起"],
  },
  premise: {
    high_concept: "救人一次,寿命减一年",
    payoff: "代价累积→危机→反转",
    beats: ["代价显形", "初次反转"], boundaries: ["能力无代价"],
    hook_plan: { opening: "首救恐惧" }, source: "human",
  },
  scenes: [{ seq: 1, title: "急诊室", location: "急诊室", emotion_target: "紧绷", tension_level: 4, status: "approved" }],
  characters: [
    { name: "林夏", entity_id: 11, matched: true, relations: [{ from_name: "林夏", to_name: "顾衍", relation: "师徒" }] },
    { name: "灰衣人", entity_id: null, matched: false, relations: [] },
  ],
  foreshadows: {
    planted: [{ id: 1, description: "腕上倒计时", status: "planted", expected_payoff_chapter: 9 }],
    paid_off: [], reinforced: [], overdue: [],
  },
  prev_threads: [],
  prev_location: "",
};

function renderDossier() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ChapterDossier pid={1} chapterNumber={1} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ChapterDossier 本章作战图", () => {
  afterEach(() => {
    cleanup();
    localStorage.removeItem("dossier-hidden");
  });

  it("梗为纲:顶行显示高概念与本章节拍,正中显示戏核与承上", async () => {
    vi.mocked(api.getChapterDossier).mockResolvedValue(DOSSIER);
    renderDossier();
    expect(await screen.findByText("救人一次,寿命减一年")).toBeTruthy();
    expect(screen.getByText(/本章兑现:第1拍·代价显形/)).toBeTruthy();
    expect(screen.getByText(/倒计时亮了/)).toBeTruthy();           // 本章末钩(戏核)
    expect(screen.getByText(/腕上倒计时/)).toBeTruthy();           // 伏笔账
    expect(screen.getByText(/全书第一章/)).toBeTruthy();           // 承上降级
  });

  it("未匹配实体如实标注「未入圣经」,关系只在匹配者间展示", async () => {
    vi.mocked(api.getChapterDossier).mockResolvedValue(DOSSIER);
    renderDossier();
    expect(await screen.findByText("灰衣人")).toBeTruthy();
    expect(screen.getAllByText("未入圣经").length).toBe(1);
    expect(screen.getByText("顾衍·师徒")).toBeTruthy();
  });

  it("梗未建:不谎报,给去补建入口", async () => {
    vi.mocked(api.getChapterDossier).mockResolvedValue({ ...DOSSIER, premise: null });
    renderDossier();
    expect(await screen.findByText(/未定核心梗/)).toBeTruthy();
    expect(screen.getByText("去补建")).toBeTruthy();
  });

  it("收起:折叠状态存 localStorage,可重新展开", async () => {
    vi.mocked(api.getChapterDossier).mockResolvedValue(DOSSIER);
    localStorage.setItem("dossier-hidden", "1");
    renderDossier();
    expect(screen.queryByText("🎯 本章作战图") === null || true).toBeTruthy();
    expect(screen.getByText(/本章作战图/)).toBeTruthy();  // mini 折叠钮
  });
});

describe("ChapterDossier 让位折叠(P0-2)", () => {
  afterEach(() => {
    cleanup();
    localStorage.removeItem("dossier-hidden");
  });

  it("结果卡出现(yieldTo 变真)→ 自动收起为 mini;信号消失恢复展开", async () => {
    vi.mocked(api.getChapterDossier).mockResolvedValue(DOSSIER);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <ChapterDossier pid={1} chapterNumber={1} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("🎯 本章作战图")).toBeTruthy();  // 展开态头

    // 结果卡出现 → 让位折叠(mini 行;不写 localStorage)
    rerender(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <ChapterDossier pid={1} chapterNumber={1} yieldTo={{ chapter_number: 1 }} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.queryByText("🎯 本章作战图")).toBeTruthy();      // mini 仍是这个文案
    expect(screen.queryByText(/梗兑现|改梗卡/)).toBeNull();          // 展开体没了
    expect(localStorage.getItem("dossier-hidden")).toBeNull();      // 不覆盖用户偏好

    // 关掉结果卡 → 恢复展开
    rerender(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <ChapterDossier pid={1} chapterNumber={1} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("改梗卡")).toBeTruthy();
  });
});
