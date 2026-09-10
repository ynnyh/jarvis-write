// 成书体检报告 API 与看板(docs/15 §7.3)。
//
// 单测这一层,是因为报告的价值**全在数字对不对、缺数据时说不说实话**:
// - 曲线要覆盖每一章、不能漏章(漏了会让人误以为那几章没问题);
// - 没数据的块必须留空 + 有 notes 说明,不能画一条贴地的线冒充「正常」;
// - 下载/预览走服务端渲染的 Markdown(前端只负责取文本,不自己拼)。
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, token } from "../api";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

function okFetch(payload: unknown = {}) {
  const mockFetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => payload,
    text: async () => String(payload),
  });
  vi.stubGlobal("fetch", mockFetch);
  return mockFetch;
}

const SAMPLE = {
  project_id: 7,
  title: "测试书",
  chapters_planned: 10,
  chapters_written: 3,
  total_words: 1200,
  avg_chapter_words: 400,
  completion: 0.3,
  flavor_curve: [
    { chapter: 1, score: 2.5, chars: 400 },
    { chapter: 2, score: 4.0, chars: 400 },
    { chapter: 3, score: 1.5, chars: 400 },
  ],
  mean_flavor: 2.67,
  worst_flavor: [{ chapter: 2, score: 4.0, chars: 400 }],
  tension_curve: [{ chapter: 1, scenes: 3, mean: 3.5, peak: 5, swing: 1.5 }],
  tension_flat_chapters: [2],
  open_issues: 2,
  issues_by_type: { state: 2 },
  issue_chapters: [1, 2],
  foreshadow_total: 3,
  foreshadow_by_status: { planted: 2, paid_off: 1 },
  overdue: [{ content: "断锋刀来历", planted: 1, expected: 3, importance: "major" }],
  debt_ratio: 0.667,
  prompt_tokens: 4000,
  completion_tokens: 2000,
  tokens_per_chapter: 2000,
  notes: ["成本:token 用量是本机全库口径"],
  markdown: "# 《测试书》成书体检报告\n",
};

describe("成书体检报告 API", () => {
  it("默认走 JSON,命中正确端点", async () => {
    token.set("tk");
    const mockFetch = okFetch(SAMPLE);

    const data = await api.healthReport(7);

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/projects/7/health-report");
    expect(opts.headers["Authorization"]).toBe("Bearer tk");
    expect(data.chapters_written).toBe(3);
    expect(data.mean_flavor).toBe(2.67);
  });

  it("曲线数据完整带回,前端画图不再二次加工", async () => {
    token.set("tk");
    okFetch(SAMPLE);
    const data = await api.healthReport(7);

    // 逐章一点,章号连续不漏
    expect(data.flavor_curve.map((p) => p.chapter)).toEqual([1, 2, 3]);
    expect(data.flavor_curve.map((p) => p.score)).toEqual([2.5, 4.0, 1.5]);
    // 节奏曲线带 mean/peak/swing,前端直接映射高度
    expect(data.tension_curve[0]).toMatchObject({ mean: 3.5, peak: 5, swing: 1.5 });
    expect(data.tension_flat_chapters).toEqual([2]);
  });

  it("缺数据的块是空数组 + notes 说明,不是假 0", async () => {
    token.set("tk");
    okFetch({
      ...SAMPLE,
      chapters_written: 0,
      flavor_curve: [],
      mean_flavor: null,
      tension_curve: [],
      foreshadow_total: 0,
      foreshadow_by_status: {},
      overdue: [],
      notes: ["质感曲线:暂无数据(还没有正文)", "节奏曲线:暂无数据(该书未启用场景卡)"],
    });
    const data = await api.healthReport(7);

    expect(data.flavor_curve).toEqual([]);
    expect(data.mean_flavor).toBeNull();   // 关键:不是 0
    expect(data.notes.length).toBeGreaterThan(0);
  });

  it("下载走服务端 Markdown,不自己拼报告", async () => {
    token.set("tk");
    const mockFetch = okFetch("# 报告正文");

    const md = await api.healthReportMarkdown(7);

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/projects/7/health-report?format=markdown");
    expect(opts.headers["Authorization"]).toBe("Bearer tk");
    expect(md).toBe("# 报告正文");
  });
});
