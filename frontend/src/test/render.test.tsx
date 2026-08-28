// 出片引擎(轻量档)前端测试:宽高比软校验 + 设置卡保存流。
// 平台交互(提交/轮询)在后端 test_render.py 已全覆盖,这里只测前端自己的逻辑。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { checkImageAspect, RENDER_STATUS_CN } from "../renderApi";

// jsdom 没有 createObjectURL(checkImageAspect 里给 Image.src 用)
beforeEach(() => {
  Object.assign(URL, {
    createObjectURL: () => "blob:test",
    revokeObjectURL: () => {},
  });
});

function fakeImage(w: number, h: number) {
  return class FakeImage {
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    naturalWidth = w;
    naturalHeight = h;
    set src(_: string) { setTimeout(() => this.onload?.(), 0); }
  };
}

function file(name = "still.png"): File {
  return new File(["x"], name, { type: "image/png" });
}

describe("checkImageAspect(挂静帧前的画幅软校验)", () => {
  it("竖图配 9:16 → 放行(null)", async () => {
    vi.stubGlobal("Image", fakeImage(1080, 1920));
    expect(await checkImageAspect(file(), "9:16")).toBeNull();
  });

  it("横图配 9:16(横竖搞反)→ 给提醒", async () => {
    vi.stubGlobal("Image", fakeImage(1920, 1080));
    const warn = await checkImageAspect(file(), "9:16");
    expect(warn).toContain("1920×1080");
    expect(warn).toContain("9:16");
  });

  it("比例读不懂的画幅(如空 ratio)→ 不拦", async () => {
    vi.stubGlobal("Image", fakeImage(1920, 1080));
    expect(await checkImageAspect(file(), "")).toBeNull();
  });
});

describe("RENDER_STATUS_CN", () => {
  it("四种状态都有中文,未知状态不炸(调用方兜底原文)", () => {
    expect(RENDER_STATUS_CN.success).toBe("已出片");
    expect(RENDER_STATUS_CN.running).toBe("生成中");
    expect(RENDER_STATUS_CN["mystery"]).toBeUndefined();
  });
});

describe("RenderCard(设置 → 出片引擎)", () => {
  it("未配置时空态照实显示;填 token 保存后打回配置结果", async () => {
    const saveConfig = vi.fn().mockResolvedValue({
      base_url: "https://www.autodl.art",
      token_masked: "ak-1***890",
      has_token: true,
      resolution: "768p",
      workflow_i2v: "minimax_h3_lightx2v",
      workflow_t2v: "minimax_h3_lightx2v_no_pic",
      workflow_tts: "indextts2-v1",
      workflow_talk: "minimax_h3_image_audio_to_video",
      configured: true,
    });
    vi.doMock("../renderApi", () => ({
      renderApi: { getConfig: vi.fn().mockResolvedValue({
        base_url: "https://www.autodl.art", token_masked: "", has_token: false,
        resolution: "768p", workflow_i2v: "wf-i2v", workflow_t2v: "wf-t2v",
        workflow_tts: "wf-tts", workflow_talk: "wf-talk",
        configured: false,
      }), saveConfig },
      RENDER_STATUS_CN,
      checkImageAspect,
    }));
    const { RenderCard } = await import("../pages/settings/RenderCard");
    render(<RenderCard />);

    // 已存的 token 为空 → 占位提示「粘贴」;填入并保存
    await waitFor(() => screen.getByPlaceholderText(/粘贴 autodl\.art/));
    fireEvent.change(screen.getByPlaceholderText(/粘贴 autodl\.art/), {
      target: { value: "ak-real-token" },
    });
    fireEvent.click(screen.getByText("保存出片配置"));

    await waitFor(() => expect(saveConfig).toHaveBeenCalled());
    // token 原样提交;其余字段带回显值
    expect(saveConfig.mock.calls[0][0]).toMatchObject({
      token: "ak-real-token",
      resolution: "768p",
      workflow_i2v: "wf-i2v",
      workflow_tts: "wf-tts",
      workflow_talk: "wf-talk",
    });
    // 保存成功后状态徽标变「已配置」,输入框清空、占位变「留空保持不变」
    await waitFor(() => expect(screen.getByText("已配置")).toBeTruthy());
    expect(screen.getByPlaceholderText("留空保持不变")).toBeTruthy();
  });
});
