# tests/test_series.py
# -*- coding: utf-8 -*-
"""角色系列短片工坊测试(角色档案/定妆注入/单集直出/空壳守卫/参考图,TestClient + mock LLM)。

验证点:
- 角色 CRUD 与归属隔离;缺名/缺定妆/auto 方向/时长越界 → 400
- AI 代写定妆:概念与方向硬约束进提示词正文;篇幅自由(短定妆直接收),
  空壳重试后仍空失败上屏
- 单集生成:定妆逐字注入、剧情与时长进提示词;空壳守卫(空输出重试→成功),
  短提示词直接收(不卡字数——用户明确要求)
- 生成失败回 draft(卡片不卡在「生成中」);手改输出;删角色级联删剧集
- 定妆参考图:上传/读/删/外链/超限拦/权限隔离
- 引擎分层门禁里注册 series 线(见 test_engine_conventions)
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"

# 定妆与提示词(篇幅自由:非空即收;这里用长文本测注入完整性)
_LOOK = (
    "一只成年小浣熊,体态圆润敦实,站起来约到成年人膝盖;灰褐色粗毛,"
    "眼圈与尾环深黑,耳尖米白,四肢炭灰,尾巴蓬松带五道黑环。圆脸黑鼻,"
    "眼睛乌亮,笑起来眯成缝;左耳缺一小口——跨集认脸的记号。常年穿一条"
    "洗旧的红色针织围巾,边缘脱线;右爪总攥着一颗攒了很久的橡果。"
    "性格好奇又嘴硬,招牌动作是抱爪眯眼歪头打量,整体气质是市井里"
    "讨生活的小机灵鬼。"
)

_LONG_PROMPT = (
    "黄昏的杂货店过道里,一只穿红围巾的小浣熊蹲在第二层货架前。"
    "镜头从过道纵深缓慢推进,起幅是全景——暖黄的顶灯在它灰褐色的粗毛上"
    "镀出绒边,深黑眼圈、左耳的小缺口、爪里那颗攒了很久的橡果都清晰可见,"
    "收银台在画面左侧虚化,货架木纹与罐头标签在暖光里泛着旧旧的质感。"
    "第 1 到 4 秒,它抱爪眯眼歪头打量最上层那罐蜂蜜,尾巴的五道黑环"
    "随着重心轻晃;第 5 到 9 秒,它踮起后爪扒住货架边缘,红围巾的脱线头"
    "在灯下飘了一下,罐子被它一寸寸挪到边缘;第 10 到 12 秒,特写——"
    "罐子脱爪,它瞳孔骤缩,前爪在空中捞了两把;第 13 到 15 秒,落幅中景,"
    "它抱着稳稳接住的罐子瘫坐在地上,长出一口气,又立刻警觉地左右看看,"
    "把罐子塞进围巾里。环境音:顶灯的电流嗡鸣、罐子滚过木架的闷响、"
    "它自己的短促鼻息;画面里没有文字与水印。"
    # 长输出场景:正文细节再铺一层光线与质感的连续描写
    "光线从头顶的暖黄灯管斜切下来,在它毛尖上留下一圈毛茸茸的轮廓光,"
    "货架背光面的阴影里浮着细小的尘埃,红围巾的针织纹理随着呼吸微微起伏。"
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _wait_job(client: TestClient, headers: dict, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] != "running":
            return job
        assert time.monotonic() < deadline, f"job {job_id} 超时: {job}"
        time.sleep(0.02)


class _StubAdapter:
    """单发桩:记录提示词(断言注入),按脚本顺序回 JSON。"""

    def __init__(self, outputs: list[dict | Exception]):
        self._outputs = list(outputs)
        self.prompts: list[str] = []

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
        out = self._outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return json.dumps(out, ensure_ascii=False)


_BODY = {
    "name": "小浣熊",
    "look": _LOOK,
    "direction": "render3d",
    "default_duration_s": 10,
    "style_hints": "杂货店、暖光",
}


def _mk_character(client, headers, extra=None) -> int:
    body = dict(_BODY)
    body.update(extra or {})
    r = client.post("/api/series/characters", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()["character_row"]["id"]


# ---------- 角色 CRUD ----------

def test_character_crud_and_validation(client):
    headers = _auth(client, "series_user")
    other = _auth(client, "series_other")

    # 校验:缺名/缺定妆/auto 方向/时长越界 → 400
    bad = _BODY.copy(); bad["name"] = " "
    assert client.post("/api/series/characters", headers=headers, json=bad).status_code == 400
    bad = _BODY.copy(); bad["look"] = ""
    assert client.post("/api/series/characters", headers=headers, json=bad).status_code == 400
    bad = _BODY.copy(); bad["direction"] = "auto"
    assert client.post("/api/series/characters", headers=headers, json=bad).status_code == 400
    bad = _BODY.copy(); bad["direction"] = "pixel"
    assert client.post("/api/series/characters", headers=headers, json=bad).status_code == 400
    for d in (4, 16):
        bad = _BODY.copy(); bad["default_duration_s"] = d
        assert client.post("/api/series/characters", headers=headers, json=bad).status_code == 400

    cid = _mk_character(client, headers)
    # meta 目录里没有 auto(系列角色必须选具体方向)
    meta = client.get("/api/series/meta", headers=headers).json()
    assert all(d["key"] != "auto" for d in meta["directions"])
    assert (meta["min_duration_s"], meta["max_duration_s"]) == (5, 15)

    # 归属隔离:别人看不见
    assert client.get(f"/api/series/characters/{cid}", headers=other).status_code == 404

    # 改档案:改名/改默认时长;空 look 拒绝
    r = client.patch(f"/api/series/characters/{cid}", headers=headers,
                     json={"name": "小浣熊阿囤", "default_duration_s": 12})
    assert r.status_code == 200
    assert r.json()["character_row"]["name"] == "小浣熊阿囤"
    assert r.json()["character_row"]["default_duration_s"] == 12
    assert client.patch(f"/api/series/characters/{cid}", headers=headers,
                        json={"look": " "}).status_code == 400

    # 删角色:级联删剧集(角色详情里查不到这集了)
    r = client.post(f"/api/series/characters/{cid}/episodes", headers=headers,
                    json={"plot": "小浣熊偷蜂蜜"})
    assert r.status_code == 200
    eid = r.json()["episode_row"]["id"]
    assert client.delete(f"/api/series/characters/{cid}", headers=headers).status_code == 200
    r = client.get("/api/series/characters", headers=headers).json()
    assert all(c["id"] != cid for c in r["characters"])
    from app.db.session import SessionLocal
    from app.db.models import SeriesEpisode as _Ep

    with SessionLocal() as s:
        assert s.get(_Ep, eid) is None


# ---------- AI 代写定妆 ----------

def test_draft_look_injection_and_guard(client):
    headers = _auth(client, "series_look")
    brief = "一只戴红围巾、爱囤零食的小浣熊"
    stub = _StubAdapter([{"look": _LOOK}])
    with patch("app.engines.series.generate.get_adapter_for", return_value=stub):
        r = client.post("/api/series/characters/draft-look", headers=headers,
                        json={"brief": brief, "direction": "render3d", "style_hints": "杂货店"})
    assert r.status_code == 200, r.text
    assert r.json()["look"] == _LOOK
    # 概念/方向硬约束/氛围关键词必须进提示词正文(模型必须看得见)
    assert brief in stub.prompts[0]
    assert "三维动画渲染风" in stub.prompts[0]
    assert "杂货店" in stub.prompts[0]

    # 短定妆直接收(篇幅自由,不卡字数——用户明确要求)
    stub = _StubAdapter([{"look": "一只戴红围巾的灰毛小浣熊"}])
    with patch("app.engines.series.generate.get_adapter_for", return_value=stub):
        r = client.post("/api/series/characters/draft-look", headers=headers,
                        json={"brief": brief, "direction": "render3d"})
    assert r.status_code == 200, r.text
    assert r.json()["look"] == "一只戴红围巾的灰毛小浣熊"
    assert len(stub.prompts) == 1  # 非空即收,没触发重试

    # 空壳 → 重试一次 → 仍空上屏 400(长短不拘:非空短定妆直接收,不卡字数)
    stub = _StubAdapter([{"look": " "}, {"look": ""}])
    with patch("app.engines.series.generate.get_adapter_for", return_value=stub):
        r = client.post("/api/series/characters/draft-look", headers=headers,
                        json={"brief": brief, "direction": "render3d"})
    assert r.status_code == 400
    assert len(stub.prompts) == 2  # 重试过一次
    # 空 brief 直接 400(不进 LLM)
    assert client.post("/api/series/characters/draft-look", headers=headers,
                       json={"brief": " ", "direction": "render3d"}).status_code == 400


# ---------- 单集生成 ----------

def test_episode_generate_injection_and_word_guard(client):
    headers = _auth(client, "series_gen")
    cid = _mk_character(client, headers)

    # 建集:时长缺省用角色默认(10),plot 必填
    assert client.post(f"/api/series/characters/{cid}/episodes", headers=headers,
                       json={"plot": " "}).status_code == 400
    r = client.post(f"/api/series/characters/{cid}/episodes", headers=headers,
                    json={"plot": "小浣熊在杂货店偷蜂蜜,差点被抓个正着"})
    assert r.status_code == 200
    ep = r.json()["episode_row"]
    assert ep["duration_s"] == 10 and ep["status"] == "draft"
    eid = ep["id"]

    # 空壳守卫:第一次空输出 → 重试;第二次长输出 → done(长短不拘,只挡空)
    stub = _StubAdapter([
        {"title": "偷蜜未遂", "prompt_cn": "  ", "negative": "低分辨率"},
        {"title": "偷蜜未遂", "prompt_cn": _LONG_PROMPT, "negative": "文字,水印,配乐"},
    ])
    with patch("app.engines.series.generate.get_adapter_for", return_value=stub):
        r = client.post(f"/api/series/episodes/{eid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    out = job["result"]
    assert out["title"] == "偷蜜未遂"
    assert out["prompt_cn"] == _LONG_PROMPT
    # 负面词被摘掉音频词(配乐只进正文,负面框给画面)
    assert "配乐" not in out["negative"]

    # 定妆逐字注入 + 剧情 + 时长进提示词正文
    assert _LOOK in stub.prompts[0]
    assert "小浣熊在杂货店偷蜂蜜" in stub.prompts[0]
    assert "10 秒" in stub.prompts[0]
    assert stub.prompts[0].index(_LOOK) < stub.prompts[0].index("本集剧情")

    # 落库核对
    r = client.get(f"/api/series/characters/{cid}", headers=headers)
    eps = r.json()["episodes"]
    assert eps[0]["status"] == "done" and eps[0]["status_cn"] == "已出词"
    assert eps[0]["output"]["prompt_cn"] == _LONG_PROMPT

    # 手改输出:整段替换,title 空了用剧情兜底
    r = client.put(f"/api/series/episodes/{eid}", headers=headers,
                   json={"output": {"title": "", "prompt_cn": _LONG_PROMPT + "改", "negative": "x"}})
    assert r.status_code == 200
    saved = r.json()["episode_row"]["output"]
    assert saved["title"] and saved["prompt_cn"].endswith("改")
    # 手改剧情(为重生成)与时长越界
    assert client.put(f"/api/series/episodes/{eid}", headers=headers,
                      json={"duration_s": 20}).status_code == 400

    # 短提示词直接收(篇幅自由,不卡字数——用户明确要求)
    r = client.post(f"/api/series/characters/{cid}/episodes", headers=headers,
                    json={"plot": "小浣熊抱着橡果睡着,尾巴一抖一抖"})
    assert r.status_code == 200
    eid2 = r.json()["episode_row"]["id"]
    short = "暖光下一只戴红围巾的小浣熊抱着橡果打盹,尾巴随呼吸轻摆,环境音是细小的鼾声。"
    stub = _StubAdapter([{"title": "抱果而眠", "prompt_cn": short, "negative": ""}])
    with patch("app.engines.series.generate.get_adapter_for", return_value=stub):
        r = client.post(f"/api/series/episodes/{eid2}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["prompt_cn"] == short
    assert len(stub.prompts) == 1  # 非空即收,没触发重试

    # 生成失败:两发都抛异常 → 任务失败,集卡回 draft
    stub = _StubAdapter([RuntimeError("LLM 挂了"), RuntimeError("又挂了")])
    with patch("app.engines.series.generate.get_adapter_for", return_value=stub):
        r = client.post(f"/api/series/episodes/{eid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error"
    r = client.get(f"/api/series/characters/{cid}", headers=headers)
    ep_row = next(e for e in r.json()["episodes"] if e["id"] == eid)
    assert ep_row["status"] == "draft"


def test_delete_rejected_while_generating(client):
    """生成中拒绝删除:任务收尾要 UPDATE 这一行,行没了会 StaleDataError(与 clips/birthday 同教训)。"""
    from app import jobs as jobs_mod

    headers = _auth(client, "series_delguard")
    cid = _mk_character(client, headers)
    r = client.post(f"/api/series/characters/{cid}/episodes", headers=headers,
                    json={"plot": "小浣熊赶夜路"})
    eid = r.json()["episode_row"]["id"]

    def _fake_job(kind: str, jid: str) -> None:
        with jobs_mod._LOCK:
            jobs_mod._JOBS[jid] = {
                "kind": kind, "status": "running", "owner_id": None,
                "stage": "生成中", "result": None, "error": None,
            }

    try:
        # 剧集生成中:删集/删角色/改档案都拦
        _fake_job(f"series-gen-{eid}", f"fakesere{eid:06d}")
        assert client.delete(f"/api/series/episodes/{eid}", headers=headers).status_code == 409
        assert client.delete(f"/api/series/characters/{cid}", headers=headers).status_code == 409
        assert client.patch(f"/api/series/characters/{cid}", headers=headers,
                            json={"name": "改名"}).status_code == 409
        with jobs_mod._LOCK:
            jobs_mod._JOBS.pop(f"fakesere{eid:06d}", None)
        assert client.delete(f"/api/series/episodes/{eid}", headers=headers).status_code == 200
    finally:
        with jobs_mod._LOCK:
            jobs_mod._JOBS.pop(f"fakesere{eid:06d}", None)


# ---------- 定妆参考图 ----------

# 1×1 透明 PNG(只按文件头判定类型,sniff 不过度解码)
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00"
    b"\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r"
    b"\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_reference_image_ops(client):
    headers = _auth(client, "series_ref")
    other = _auth(client, "series_ref2")
    cid = _mk_character(client, headers)

    r = client.post(f"/api/series/characters/{cid}/reference", headers=headers,
                    files={"file": ("ref.png", _PNG, "image/png")},
                    data={"note": "正面定妆照"})
    assert r.status_code == 200, r.text
    refs = r.json()["character_row"]["ref_images"]
    assert len(refs) == 1 and refs[0]["kind"] == "upload"
    assert refs[0]["note"] == "正面定妆照"

    # 读回(鉴权):上传目录不挂静态服务
    r = client.get(f"/api/series/characters/{cid}/reference/0", headers=headers)
    assert r.status_code == 200 and r.content == _PNG
    assert r.headers["content-type"].startswith("image/png")
    # 权限隔离:别人读不到
    assert client.get(f"/api/series/characters/{cid}/reference/0", headers=other).status_code == 404

    # 外链 + 非法链接拦
    r = client.post(f"/api/series/characters/{cid}/reference/link", headers=headers,
                    json={"url": "https://img.example.com/ref.jpg", "note": "侧面"})
    assert r.status_code == 200 and len(r.json()["character_row"]["ref_images"]) == 2
    assert client.post(f"/api/series/characters/{cid}/reference/link", headers=headers,
                       json={"url": "不是链接"}).status_code == 400

    # 超限:每角色最多 3 张
    assert client.post(f"/api/series/characters/{cid}/reference/link", headers=headers,
                       json={"url": "https://img.example.com/back.jpg"}).status_code == 200
    r = client.post(f"/api/series/characters/{cid}/reference/link", headers=headers,
                    json={"url": "https://img.example.com/x.jpg"})
    assert r.status_code == 400 and "最多 3 张" in r.json()["detail"]

    # 删上传的那张(文件连删),再读 404
    assert client.delete(f"/api/series/characters/{cid}/reference/0", headers=headers).status_code == 200
    assert client.get(f"/api/series/characters/{cid}/reference/0", headers=headers).status_code == 404
