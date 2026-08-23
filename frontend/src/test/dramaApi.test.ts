// 漫剧 API 客户端:挂素材那几条(multipart / 鉴权读图 / 路径拼接)。
//
// 为什么单测这一层:
// - 上传是 multipart,**不能手设 Content-Type**——手设了浏览器就不会带 boundary,
//   后端只会收到一堆解析不出来的字节。这一条肉眼很难发现,必须钉住;
// - 读图端点要 Authorization 头,而 <img src> 带不了头,所以只能取 blob 转本地 URL。
//   哪天有人图省事改回 <img src={url}>,这条测试要拦下来;
// - 定妆照挂在角色卡上、静帧挂在分镜格上,两条路径长得很像(characters/{id}/reference
//   vs shots/{id}/asset),拼错了照样 200(打到别的资源上),所以路径也钉住。
import { beforeEach, describe, expect, it, vi } from "vitest";

import { dramaApi } from "../dramaApi";
import { token } from "../api";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

function okFetch(payload: unknown = { shot: { id: 9 } }) {
  const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
  vi.stubGlobal("fetch", mockFetch);
  return mockFetch;
}

describe("挂分镜静帧", () => {
  it("上传走 multipart 且不手设 Content-Type(否则 boundary 丢了)", async () => {
    token.set("tk");
    const mockFetch = okFetch();
    const file = new File([new Uint8Array([1, 2, 3])], "a.png", { type: "image/png" });

    await dramaApi.uploadShotAsset(7, 42, file, "第二版");

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/projects/7/drama/shots/42/asset");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Authorization"]).toBe("Bearer tk");
    expect(opts.headers["Content-Type"]).toBeUndefined();
    expect(opts.body).toBeInstanceOf(FormData);
    expect((opts.body as FormData).get("file")).toBe(file);
    expect((opts.body as FormData).get("note")).toBe("第二版");
  });

  it("外链/删除打到分镜格那条路径上(别和角色卡的定妆照串了)", async () => {
    const mockFetch = okFetch();

    await dramaApi.linkShotAsset(7, 42, "https://cdn.example.com/x.png", "即梦出的");
    expect(mockFetch.mock.calls[0][0]).toBe("/api/projects/7/drama/shots/42/asset/link");
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({
      url: "https://cdn.example.com/x.png", note: "即梦出的",
    });

    await dramaApi.deleteShotAsset(7, 42, 1);
    expect(mockFetch.mock.calls[1][0]).toBe("/api/projects/7/drama/shots/42/asset/1");
    expect(mockFetch.mock.calls[1][1].method).toBe("DELETE");

    // 角色卡那条是另一条路径,两者不能拼串
    await dramaApi.deleteRef(7, 42, 1);
    expect(mockFetch.mock.calls[2][0]).toBe("/api/projects/7/drama/characters/42/reference/1");
  });

  it("读缩略图带鉴权头并转成本地 blob URL", async () => {
    token.set("tk");
    const blob = new Blob(["img"], { type: "image/png" });
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => blob });
    vi.stubGlobal("fetch", mockFetch);
    // jsdom 不实现 createObjectURL,自己顶一个(只关心「有没有走这一步」)
    const create = vi.fn().mockReturnValue("blob:fake");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create }));

    const url = await dramaApi.shotAssetBlobUrl(7, 42, 0);

    expect(mockFetch.mock.calls[0][0]).toBe("/api/projects/7/drama/shots/42/asset/0");
    expect(mockFetch.mock.calls[0][1].headers["Authorization"]).toBe("Bearer tk");
    expect(create).toHaveBeenCalledWith(blob);
    expect(url).toBe("blob:fake");
  });

  it("上传失败把后端的人话 detail 抛出来(不是干巴巴的 HTTP 400)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 400, json: async () => ({ detail: "一格最多挂 2 张" }),
    }));
    const file = new File(["x"], "a.png", { type: "image/png" });
    await expect(dramaApi.uploadShotAsset(7, 42, file)).rejects.toThrow("一格最多挂 2 张");
  });
});

describe("视频段计划", () => {
  it("单次时长上限进 query(换档即时重算全靠它)", async () => {
    const mockFetch = okFetch({ plan: { limit_s: 15, segments: [] } });
    await dramaApi.getClips(7, 3, 15);
    expect(mockFetch.mock.calls[0][0])
      .toBe("/api/projects/7/drama/episodes/3/clips?limit_s=15");
  });
});
