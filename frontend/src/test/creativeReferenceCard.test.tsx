// 参考修改必须重分析；跨作品切换不得带入上一份理解。
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import CreativeReferenceCard from "../ui/CreativeReferenceCard";
import { CreativeGoal, creativeApi } from "../creativeApi";

const run = vi.hoisted(() => vi.fn());
vi.mock("../ui/useJob", () => ({ useJob: () => ({ run }) }));
vi.mock("../creativeApi", async (original) => ({ ...await original<typeof import("../creativeApi")>(), creativeApi: { get: vi.fn(), analyze: vi.fn(), save: vi.fn() } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const goal: CreativeGoal = {
  intent: "认真占便宜的日常喜剧", form: "sketch", references: [{ name: "自己的参考", description: "克制的对白", excerpt: "省两块亏八块", locator: "饭店片段", url: "" }],
  selected: ["engine"], observations: [{ dimension: "engine", instruction: "用反向代价兑现人物目的", source_index: 0, basis: "excerpt", evidence: "亏八块" }],
  unknowns: [], must: "", avoid: "不要打脸", enabled: true, version: 2,
};

it("查看证据、选择借鉴维度后只采用当前版本；改参考后必须重分析", async () => {
  vi.mocked(creativeApi.get).mockResolvedValue({ goal });
  vi.mocked(creativeApi.save).mockResolvedValue({ goal: { ...goal, version: 3 } });
  const saved = vi.fn();
  render(<CreativeReferenceCard scope="anime" targetId={4} form="sketch" onSaved={saved} />);
  await screen.findByText(/已采用方向 2/);
  fireEvent.click(screen.getByText("给参考、调整理解"));
  expect(screen.getByText("用反向代价兑现人物目的")).toBeTruthy();
  fireEvent.click(screen.getByText("采用这个理解"));
  await waitFor(() => expect(saved).toHaveBeenCalledOnce());
  expect(creativeApi.save).toHaveBeenCalledWith("anime", 4, expect.objectContaining({ expected_version: 2, selected: ["engine"] }));
  fireEvent.change(screen.getByLabelText("喜欢什么"), { target: { value: "换成悬疑氛围" } });
  expect(screen.queryByText("采用这个理解")).toBeNull();
  const result = { ...goal, intent: "新要求", expected_version: 3, observations: [{ ...goal.observations[0]!, instruction: "按误判线索推进" }] };
  run.mockResolvedValue({ goal: result });
  fireEvent.click(screen.getByText("理解参考与想法"));
  expect(await screen.findByText("按误判线索推进")).toBeTruthy();
});

it("切到另一本书后清空旧参考并处理加载失败", async () => {
  vi.mocked(creativeApi.get).mockResolvedValueOnce({ goal }).mockResolvedValueOnce({ goal: {} });
  const { rerender } = render(<CreativeReferenceCard scope="project" targetId={1} form="serial" onSaved={() => {}} />);
  await screen.findByText(/已采用方向 2/);
  rerender(<CreativeReferenceCard scope="project" targetId={2} form="serial" onSaved={() => {}} />);
  await waitFor(() => expect(screen.queryByText(/已采用方向 2/)).toBeNull());
  expect(screen.getByLabelText("想要的体验")).toHaveValue("");
  expect(screen.queryByLabelText("作品/材料名")).toBeNull();
});
