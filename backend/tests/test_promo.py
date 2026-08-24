# tests/test_promo.py
# -*- coding: utf-8 -*-
"""宣传片工坊测试(研讨 SSE / 简报 / 全链路生成 / 锚段兜底 / 导出,TestClient + mock LLM)。

验证点:
- CRUD 与归属隔离(他人企划 → 404);非法角度/方向 → 400
- 研讨对话:token 帧逐字流出、done 收尾、chat_log 落库(含回复)
- 简报:研讨记录收敛成结构化 brief;无研讨且无素材点 → job error 引导
- 全链路:风格 → 地标 → 解说词(需简报) → 分镜 → 提示词(画风锚兜底) → 成片包
- 导出:md 含简报/需核实清单/三轨;srt 时间轴确定
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


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


class _JsonAdapter:
    """假适配器:ask() 返回给定 JSON;stream() 逐字吐给定文本(研讨流用)。"""

    def __init__(self, *payloads: dict | str):
        self._asks = [json.dumps(p, ensure_ascii=False) if isinstance(p, dict) else p for p in payloads]
        self._ai = 0

    async def ask(self, prompt, system=None):
        if len(self._asks) == 1:
            return self._asks[0]
        item = self._asks[min(self._ai, len(self._asks) - 1)]
        self._ai += 1
        return item

    async def stream(self, messages):
        text = self._asks[-1] if isinstance(self._asks[-1], str) else self._asks[0]
        for ch in text:
            yield ch


_BRIEF_REPLY = {
    "positioning": "一条从凌晨烟火吃到午夜灯火的西安食事折子",
    "audience": "短视频平台的年轻游客,刷到 3 秒内决定去不去",
    "tone": "烟火气 舒缓 有嚼劲",
    "key_messages": ["西安的魂在市井吃食里", "一天十二时辰皆是烟火"],
    "structure": [
        {"title": "凌晨·头汤", "angle": "美食烟火", "beat": "回民街第一锅汤的蒸汽与掌勺人", "seconds": 25},
        {"title": "白日·市声", "angle": "人文历史", "beat": "城墙下的日常与人流", "seconds": 30},
        {"title": "午夜·灯火", "angle": "夜经济夜游", "beat": "夜市灯如昼,收束落 slogan", "seconds": 35},
    ],
    "slogan_candidates": ["长安烟火,不散", "来西安,吃一天"],
    "cautions": ["「回民街头汤」的时段表述需与实际营业时间核实"],
}

_STYLE_REPLY = {
    "style_name": "电影感实拍·暖烟火",
    "style_cn": "实拍电影感,暖金主色,浅景深长焦,市井颗粒质感",
    "style_en": "cinematic live footage, warm tones, shallow depth",
    "negative": "文字,水印,低分辨率",
}

_LANDMARK_REPLY = {
    "landmarks": [
        {"name": "回民街夜市", "appearance_cn": "仿古街巷,灯幌层叠,蒸汽与烟火气", "appearance_en": "night market alley, lanterns, steam"},
        {"name": "西安城墙", "appearance_cn": "青砖墙体,垛口连绵,晨光斜照", "appearance_en": "ancient city wall, bricks, dawn light"},
    ]
}

_SCRIPT_REPLY = {
    "synopsis": "从凌晨头汤到午夜灯火的西安食事一日。",
    "lines": [
        {"speaker": "旁白", "text": "凌晨四点,西安醒在头汤的蒸汽里。", "action": "回民街灶台蒸汽升腾,掌勺人下第一碗"},
        {"speaker": "旁白", "text": "六百年的城墙下,早点摊子支起来了。", "action": "城墙根早点摊,油条下锅"},
        {"speaker": "旁白", "text": "长安烟火,不散。", "action": "夜市灯火大全景,定格收束"},
    ],
}

_BOARD_REPLY = {
    "shots": [
        {"seq": 1, "scene_name": "回民街夜市", "characters": [],
         "action_desc": "灶台蒸汽升腾,掌勺人下第一碗", "shot_type": "特写", "camera": "推",
         "dialogue": "凌晨四点,西安醒在头汤的蒸汽里。", "duration_s": 5},
        {"seq": 2, "scene_name": "西安城墙", "characters": [],
         "action_desc": "城墙根早点摊,油条下锅", "shot_type": "中景", "camera": "固定",
         "dialogue": "六百年的城墙下,早点摊子支起来了。", "duration_s": 4},
        {"seq": 3, "scene_name": "回民街夜市", "characters": [],
         "action_desc": "夜市灯火大全景", "shot_type": "全景", "camera": "航拍",
         "dialogue": "长安烟火,不散。", "duration_s": 99},
    ]
}

_PROMPTS_REPLY = {
    "shots": [
        {"seq": 1, "prompt_cn": "灶台蒸汽特写(故意漏锚段)", "prompt_en": "steaming wok close-up", "negative": "低分辨率"},
        {"seq": 2, "prompt_cn": "城墙根早点摊中景,含实拍电影感", "prompt_en": "city wall breakfast stall, cinematic live footage, warm tones, shallow depth", "negative": ""},
        {"seq": 3, "prompt_cn": "夜市灯火航拍大全景,含实拍电影感", "prompt_en": "night market aerial, cinematic live footage, warm tones, shallow depth", "negative": ""},
    ]
}

_PACK_REPLY = {
    "shots": [
        {"seq": 1, "transition": "推入", "bgm_tag": "市井白噪+轻鼓点"},
        {"seq": 2, "transition": "叠化", "bgm_tag": "市井白噪+轻鼓点"},
        {"seq": 3, "transition": "定格收尾", "bgm_tag": "大气弦乐收"},
    ]
}


def _create_plan(client, headers, subject="西安") -> dict:
    r = client.post("/api/promos", headers=headers, json={
        "subject": subject, "angles": ["food", "culture"], "duration_s": 90, "direction": "live",
    })
    assert r.status_code == 200, r.text
    return r.json()["plan"]


def test_promo_crud_and_validation(client):
    """CRUD/归属/参数校验。"""
    headers = _auth(client, "promo_user")
    other = _auth(client, "promo_other")
    plan = _create_plan(client, headers)
    pid = plan["id"]
    assert plan["direction"] == "live"

    # 非法角度/方向 → 400;归属隔离 → 404
    assert client.post("/api/promos", headers=headers,
                       json={"subject": "x", "angles": ["wrong"]}).status_code == 400
    assert client.post("/api/promos", headers=headers,
                       json={"subject": "x", "direction": "pixel"}).status_code == 400
    assert client.get(f"/api/promos/{pid}", headers=other).status_code == 404

    # PATCH 素材点
    r = client.patch(f"/api/promos/{pid}", headers=headers,
                     json={"material_notes": "回民街头汤凌晨四点开熬;城墙明代扩建;slogan 候选「长安烟火,不散」"})
    assert r.status_code == 200
    # 列表(瘦身后不含 chat_log)
    r = client.get("/api/promos", headers=headers)
    assert r.status_code == 200
    item = next(p for p in r.json()["plans"] if p["id"] == pid)
    assert "chat_log" not in item and item["subject"] == "西安"
    assert client.delete(f"/api/promos/{pid}", headers=other).status_code == 404
    assert client.delete(f"/api/promos/{pid}", headers=headers).status_code == 200


def test_promo_chat_stream_and_brief(client):
    """研讨 SSE:token 逐字流 + done + 落库;随后收敛简报。"""
    headers = _auth(client, "promo_chat")
    plan = _create_plan(client, headers, "成都")
    pid = plan["id"]
    client.patch(f"/api/promos/{pid}", headers=headers,
                 json={"material_notes": "苍蝇馆子文化;盖碗茶;slogan「成都不慌」"})

    reply_text = "从吃的入手没问题——我建议以「苍蝇馆子的一天」做骨架:早上盖碗茶开场,深夜冷淡收束。你更想要烟火慢板还是快剪?"
    with patch("app.engines.promo.chat.get_adapter_for",
               return_value=_JsonAdapter(reply_text)):
        with client.stream("POST", f"/api/promos/{pid}/chat", headers=headers,
                           json={"messages": [
                               {"role": "user", "content": "我想从吃的入手做成都宣传片"},
                           ]}) as resp:
            assert resp.status_code == 200
            tokens = []
            done = None
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    ev = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line.split(":", 1)[1].strip())
                    if ev == "token":
                        tokens.append(payload["text"])
                    elif ev == "done":
                        done = payload
            assert done and done["reply"] == reply_text
            assert "".join(tokens) == reply_text

    # chat_log 落库(user + assistant)
    r = client.get(f"/api/promos/{pid}", headers=headers)
    log = r.json()["plan"]["chat_log"]
    assert [m["role"] for m in log] == ["user", "assistant"]
    assert log[1]["text"] == reply_text

    # 无研讨也无素材点 → 简报报错引导
    plan2 = _create_plan(client, headers, "洛阳")
    r = client.post(f"/api/promos/{plan2['id']}/brief", headers=headers)
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error" and "研讨" in job["error"]

    # 有研讨 → 收敛简报
    with patch("app.engines.promo.brief.get_adapter_for",
               return_value=_JsonAdapter(_BRIEF_REPLY)):
        r = client.post(f"/api/promos/{pid}/brief", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    brief = job["result"]["brief"]
    assert brief["positioning"].startswith("一条从凌晨")
    assert len(brief["structure"]) == 3
    assert brief["cautions"]


def test_promo_full_pipeline_and_exports(client):
    """风格→地标→解说词→分镜→提示词(兜底)→成片包→导出。"""
    headers = _auth(client, "promo_flow")
    plan = _create_plan(client, headers)
    pid = plan["id"]
    client.patch(f"/api/promos/{pid}", headers=headers,
                 json={"material_notes": "回民街头汤凌晨开熬;slogan「长安烟火,不散」"})

    # 解说词前置:没有简报 → 报错
    r = client.post(f"/api/promos/{pid}/script", headers=headers)
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error" and "简报" in job["error"]

    def run(action, patch_target, reply):
        with patch(patch_target, return_value=_JsonAdapter(reply)):
            r = client.post(f"/api/promos/{pid}/{action}", headers=headers)
            return _wait_job(client, headers, r.json()["job_id"])

    # 简报(seed 同款结构)
    job = run("brief", "app.engines.promo.brief.get_adapter_for", _BRIEF_REPLY)
    assert job["status"] == "done"

    # 风格
    job = run("style", "app.engines.promo.assets.get_adapter_for", _STYLE_REPLY)
    assert job["status"] == "done" and "实拍电影感" in job["result"]["style_cn"]

    # 地标(简报已定)
    job = run("landmarks", "app.engines.promo.assets.get_adapter_for", _LANDMARK_REPLY)
    assert job["status"] == "done" and len(job["result"]["landmarks"]) == 2

    # 解说词
    job = run("script", "app.engines.promo.script.get_adapter_for", _SCRIPT_REPLY)
    assert job["status"] == "done" and len(job["result"]["script"]["lines"]) == 3

    # 分镜(99s → clamp 10)
    job = run("storyboard", "app.engines.promo.storyboard.get_adapter_for", _BOARD_REPLY)
    assert job["status"] == "done"
    shots = job["result"]["shots"]
    assert len(shots) == 3 and shots[2]["duration_s"] == 10

    # 提示词:镜头1漏画风锚 → 兜底
    job = run("prompts", "app.engines.promo.prompt_render.get_adapter_for", _PROMPTS_REPLY)
    assert job["status"] == "done"
    shots = job["result"]["shots"]
    assert "实拍电影感" in shots[0]["prompt_cn"]
    assert "cinematic live footage" in shots[0]["prompt_en"]
    assert shots[0]["negative"].startswith("文字,水印")
    r = client.get(f"/api/promos/{pid}", headers=headers)
    assert r.json()["plan"]["status"] == "ready"

    # 成片包:配音稿对位 + 转场/配乐并入
    job = run("pack", "app.engines.promo.pack.get_adapter_for", _PACK_REPLY)
    assert job["status"] == "done"
    pack = job["result"]["pack"]
    assert len(pack["dubbing"]) == 3
    assert pack["checklist"][2]["transition"] == "定格收尾"
    assert "长安烟火,不散" in pack["narration_full"]

    # 导出
    r = client.get(f"/api/promos/{pid}/export?format=md", headers=headers)
    assert r.status_code == 200
    md = r.text
    assert "创作简报" in md and "需人工核实" in md
    assert "长安烟火,不散" in md and "地标卡" in md
    r = client.get(f"/api/promos/{pid}/export?format=srt", headers=headers)
    assert "1\n00:00:00,000 --> 00:00:05,000\n凌晨四点,西安醒在头汤的蒸汽里。" in r.text
    r = client.get(f"/api/promos/{pid}/export?format=csv", headers=headers)
    assert r.text.startswith("\ufeff")


# ---- 生成切段(画布拼接工作流) ----

_CHUNKS_REPLY = {
    "chunks": [
        {"index": 1, "motion_prompt_cn": "蒸汽升腾中缓缓推近,掌勺人捞面入碗",
         "motion_prompt_en": "steam rising, slow push-in",  # 故意漏锚
         "first_frame_hint": "用镜头1的静帧,起手蒸汽缓慢上涌",
         "link_note": "蒸汽消散处硬切下段"},
        {"index": 2, "motion_prompt_cn": "城墙根早点摊,油条下锅,含实拍电影感",
         "motion_prompt_en": "breakfast stall, cinematic live footage, warm tones, shallow depth",
         "first_frame_hint": "用镜头2的静帧",
         "link_note": "叠化入夜景"},
        {"index": 3, "motion_prompt_cn": "夜市灯火大全景缓缓拉开,含实拍电影感",
         "motion_prompt_en": "night market aerial pull-back, cinematic live footage, warm tones, shallow depth",
         "first_frame_hint": "用镜头3的静帧",
         "link_note": "收束:定格,加 slogan 字幕"},
    ]
}


def test_promo_chunks(client):
    """切段:镜头边界贪心(5+4=9 入段1;99→clamp10 独立段2)、时间码累计、锚段兜底、导出。"""
    headers = _auth(client, "promo_chunk")
    plan = _create_plan(client, headers)
    pid = plan["id"]
    client.patch(f"/api/promos/{pid}", headers=headers,
                 json={"material_notes": "slogan「长安烟火,不散」"})

    def run(action, patch_target, reply):
        with patch(patch_target, return_value=_JsonAdapter(reply)):
            r = client.post(f"/api/promos/{pid}/{action}", headers=headers)
            return _wait_job(client, headers, r.json()["job_id"])

    assert run("brief", "app.engines.promo.brief.get_adapter_for", _BRIEF_REPLY)["status"] == "done"
    assert run("style", "app.engines.promo.assets.get_adapter_for", _STYLE_REPLY)["status"] == "done"
    assert run("script", "app.engines.promo.script.get_adapter_for", _SCRIPT_REPLY)["status"] == "done"
    assert run("storyboard", "app.engines.promo.storyboard.get_adapter_for", _BOARD_REPLY)["status"] == "done"

    # 无分镜 → 400/错误;非法 chunk_s → 400
    r = client.post(f"/api/promos/{pid}/chunks", headers=headers, json={"chunk_s": 7})
    assert r.status_code == 400

    job = run("chunks", "app.engines.promo.chunks.get_adapter_for", _CHUNKS_REPLY)
    assert job["status"] == "done", job
    chunks = job["result"]["chunks"]
    assert chunks["chunk_s"] == 15
    items = chunks["items"]
    # 镜头1(5s)+镜头2(4s)=9s 同段;镜头3(10s)加入会超 15 → 独立成段
    assert len(items) == 2
    assert items[0]["shot_seqs"] == [1, 2]
    assert items[0]["start_s"] == 0 and items[0]["end_s"] == 9
    assert items[1]["shot_seqs"] == [3]
    assert items[1]["start_s"] == 9 and items[1]["end_s"] == 19
    # 时间码与 SRT 同轴:镜头3 的字幕条从 9s 起
    r = client.get(f"/api/promos/{pid}/export?format=srt", headers=headers)
    assert "00:00:09,000 --> 00:00:19,000" in r.text
    # LLM 桩只回了 3 段、实际 2 段:index>2 的丢弃,第 2 段取 index=2 的标注
    assert "城墙根" in items[1]["motion_prompt_cn"]
    # 英文漏锚 → 兜底注入画风锚
    assert "cinematic live footage" in items[0]["motion_prompt_en"]
    # GET 回读带 chunks
    r = client.get(f"/api/promos/{pid}", headers=headers)
    assert r.json()["plan"]["chunks"]["items"][0]["motion_prompt_cn"]

    # 导出 md 含切段表
    r = client.get(f"/api/promos/{pid}/export?format=md", headers=headers)
    assert "生成切段" in r.text and "一段一次生成,画布拼接" in r.text
    assert "首帧指引" in r.text

    # 5 秒切法:镜头1(5s)一段、镜头2(4s)一段、镜头3(10s)超限独立段并标 ⚠
    with patch("app.engines.promo.chunks.get_adapter_for",
               return_value=_JsonAdapter(_CHUNKS_REPLY)):
        r = client.post(f"/api/promos/{pid}/chunks", headers=headers, json={"chunk_s": 5})
        job = _wait_job(client, headers, r.json()["job_id"])
    items5 = job["result"]["chunks"]["items"]
    assert [i["shot_seqs"] for i in items5] == [[1], [2], [3]]
    assert items5[2]["over_limit"] is True and items5[0]["over_limit"] is False

    # 漏写运动提示词的段:先用该段镜头拼确定性兜底再注入锚与音频口径——
    # 反过来(先注入锚)会绕过「空提示词不追加」守卫,产出一条只有约束没有内容的提示词
    with patch("app.engines.promo.chunks.get_adapter_for",
               return_value=_JsonAdapter({"chunks": [
                   {"index": 1, "motion_prompt_cn": "", "motion_prompt_en": ""},
                   {"index": 2, "motion_prompt_cn": "城墙根早点摊,油条下锅,含实拍电影感",
                    "motion_prompt_en": "breakfast stall, cinematic live footage"},
               ]})):
        r = client.post(f"/api/promos/{pid}/chunks", headers=headers, json={"chunk_s": 15})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    m = job["result"]["chunks"]["items"][0]["motion_prompt_cn"]
    assert "镜头1" in m and "镜头2" in m     # 分镜自身的动作进了提示词(不是光秃秃的约束)
    assert "【画风锚】" in m                 # 锚仍注入
    assert "【音频】" in m                   # 音频口径跟着注入
