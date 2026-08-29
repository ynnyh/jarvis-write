# tests/test_clips_promo_film_prompt.py
# -*- coding: utf-8 -*-
"""整片提示词(clips 系三工坊 + 宣传片)测试:生成 / 读取 / 保存 / 归属 / 原料注入。

clips 系的原料在行内 JSON(clip.shots/lines),宣传片在独立表(promo_shots)——
两种形态各验一遍生成链路,LLM 全程打桩:
- 无分镜拦截(说人话)
- 生成跑通:风格/台词/说话人/点子/地标/素材点进原料,围栏被剥掉,结果落库可读
- PUT 整段替换保存;归属隔离 404
"""
from __future__ import annotations

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


def _uid(client: TestClient, username: str) -> int:
    from app.db.models import User
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        return s.query(User).filter(User.username == username).first().id


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


class _TextAdapter:
    def __init__(self, text: str):
        self.text = text
        self.prompts: list[str] = []

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
        return self.text


_CLIP_CLIP_JSON = {
    "take": "A", "logline": "末班地铁的告白", "punchline": "到站了,别下车。",
    "lines": [{"speaker": "她", "text": "你为什么还要听?"}],
    "shots": [
        {"seq": 1, "shot_type": "近景", "camera": "固定", "duration_s": 4,
         "action_desc": "她垂眼看戒指,指尖停在杯沿", "dialogue": "你为什么还要听?",
         "characters": []},
        {"seq": 2, "shot_type": "特写", "camera": "推", "duration_s": 3,
         "action_desc": "她眼眶泛起微弱水光,把话咽了回去", "dialogue": ""},
    ],
    "chunks": [{"index": 0, "start_s": 0, "end_s": 7, "duration_s": 7, "shot_seqs": [1, 2]}],
}

_CLIP_REPLY = (
    "一条 7 秒、9:16 竖屏的情绪短片。总述:末班地铁,冷暖交叠。\n"
    "【主体一致性】她:黑色卷发,深色大衣,全片形象不变。\n"
    "【镜头一|0.0—4.0秒】近景,她垂眼看戒指。她:你为什么还要听?\n"
    "【声音设计】对白逐音节同步。【影像限制】不要文字水印。"
    "【核心要求】力量来自没说出口的那半句。"
)

_PROMO_REPLY = (
    "【第1段|0—5秒】实拍电影感,暖金主色,城墙灯影。全景缓慢推近:明代城墙砖石斑驳,"
    "灯笼成排摇曳。旁白:千年城墙,烟火人家。衔接:硬切。"
)


def _seed_clip(uid: int, *, with_shots: bool = True) -> int:
    from app.db.models import MoodClip
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        row = MoodClip(
            user_id=uid, mode="mood", duration_s=15, direction="live",
            custom_theme="末班地铁", inspiration="异地恋的最后一通电话",
            dialogue_style="dialogue",
            style_cn="写实电影质感,冷暖交叠,雨夜反光", negative="文字水印、五官错位",
            chosen=0 if with_shots else -1,
            clip=_CLIP_CLIP_JSON if with_shots else {},
            status="picked" if with_shots else "generated",
        )
        s.add(row)
        s.commit()
        return row.id


def _seed_promo(uid: int, *, with_shots: bool = True, shot_count: int = 1) -> int:
    from app.db.models import PromoPlan, PromoShot
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        plan = PromoPlan(
            user_id=uid, subject="西安", title="西安·烟火食事", duration_s=60,
            direction="live", style_cn="实拍电影感,暖金主色,城墙灯影",
            negative="logo 水印", material_notes="城墙 13.74 公里;600 年历史",
            script={"lines": [{"speaker": "旁白", "text": "千年城墙,烟火人家。"}]},
            landmarks=[{"name": "西安城墙", "appearance_cn": "明代城墙,砖石斑驳,灯笼成排"}],
        )
        s.add(plan)
        s.flush()
        if with_shots:
            for n in range(shot_count):
                s.add(PromoShot(
                    promo_id=plan.id, seq=n + 1, scene_name="西安城墙", shot_type="全景",
                    camera="推", duration_s=5, action_desc=f"城墙全景缓慢推近(镜{n + 1})",
                    dialogue="千年城墙,烟火人家。" if n == 0 else "",
                ))
        s.commit()
        return plan.id


# =============== clips 系(情绪/灵感/故事) ===============


def test_clip_film_prompt_requires_shots(client):
    headers = _auth(client, "cfp_no_shots")
    eid = _seed_clip(_uid(client, "cfp_no_shots"), with_shots=False)

    r = client.post(f"/api/clips/{eid}/film-prompt", headers=headers)
    assert r.status_code == 200, r.text
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error", job
    assert "分镜" in job["error"]


def test_clip_film_prompt_generate_and_get(client):
    headers = _auth(client, "cfp_gen")
    eid = _seed_clip(_uid(client, "cfp_gen"))
    adapter = _TextAdapter(f"```text\n{_CLIP_REPLY}\n```")

    with patch("app.engines.clips.film_prompt.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/clips/{eid}/film-prompt", headers=headers)
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    got = client.get(f"/api/clips/{eid}/film-prompt", headers=headers)
    assert got.json()["film_prompt"] == _CLIP_REPLY  # 围栏已剥,整段保存

    prompt = adapter.prompts[0]
    assert "写实电影质感" in prompt  # 风格锚进原料
    assert "异地恋的最后一通电话" in prompt  # 点子进原料
    assert "她:你为什么还要听?" in prompt  # 台词带说话人(lines 文本反查)
    assert "到站了,别下车。" in prompt  # 金句进原料


def test_clip_film_prompt_save_and_ownership(client):
    headers = _auth(client, "cfp_save")
    eid = _seed_clip(_uid(client, "cfp_save"))

    assert client.get(f"/api/clips/{eid}/film-prompt", headers=headers).json()["film_prompt"] == ""
    r = client.put(f"/api/clips/{eid}/film-prompt", headers=headers,
                   json={"film_prompt": "  自己写的整片提示词。\n"})
    assert r.status_code == 200 and r.json()["film_prompt"] == "自己写的整片提示词。"

    other = _auth(client, "cfp_other")
    assert client.get(f"/api/clips/{eid}/film-prompt", headers=other).status_code == 404
    assert client.put(f"/api/clips/{eid}/film-prompt", headers=other,
                      json={"film_prompt": "偷改"}).status_code == 404


# =============== 宣传片 ===============


def test_promo_film_prompt_requires_shots(client):
    headers = _auth(client, "pfp_no_shots")
    pid = _seed_promo(_uid(client, "pfp_no_shots"), with_shots=False)

    r = client.post(f"/api/promos/{pid}/film-prompt", headers=headers)
    assert r.status_code == 200, r.text
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error", job
    assert "分镜" in job["error"]


def test_promo_film_prompt_generate_and_get(client):
    """分段版:文档头(使用说明)+ 分段提示词块;原料注入齐活。"""
    headers = _auth(client, "pfp_gen")
    pid = _seed_promo(_uid(client, "pfp_gen"))
    adapter = _TextAdapter(_PROMO_REPLY)

    with patch("app.engines.promo.film_prompt.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/promos/{pid}/film-prompt", headers=headers)
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    got = client.get(f"/api/promos/{pid}/film-prompt", headers=headers)
    doc = got.json()["film_prompt"]
    assert doc.startswith("【使用说明】")  # 引擎写的确定性文档头
    assert _PROMO_REPLY in doc  # 模型的分段提示词块整段保存

    prompt = adapter.prompts[0]
    assert "西安城墙" in prompt  # 地标卡进原料
    assert "13.74 公里" in prompt  # 素材点(硬约束)进原料
    assert "解说词(旁白):千年城墙,烟火人家。" in prompt  # 解说带说话人
    assert "宣传片" in prompt  # 类型定位进原料
    assert "切成 1 段" in prompt  # 单段计划(5 秒镜头 ≤15s 上限 → 一段)


def test_promo_film_prompt_segments_split_by_limit(client):
    """20 秒分镜、单段上限 15s → 贪心切成 2 段,分段计划进原料;30s 上限只切 1 段。"""
    headers = _auth(client, "pfp_seg")
    pid = _seed_promo(_uid(client, "pfp_seg"), shot_count=4)  # 4 镜 × 5s = 20s
    adapter = _TextAdapter(_PROMO_REPLY)

    with patch("app.engines.promo.film_prompt.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/promos/{pid}/film-prompt", headers=headers,
                        json={"segment_s": 15})
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job

    prompt = adapter.prompts[0]
    assert "切成 2 段" in prompt
    assert "【第2段|15—20秒】" in prompt  # 贪心装满:3 镜 0-15 / 1 镜 15-20,边界落镜头边界

    # 30s 上限:同样 20s 全片装进一段
    adapter30 = _TextAdapter(_PROMO_REPLY)
    with patch("app.engines.promo.film_prompt.get_adapter_for", return_value=adapter30):
        r = client.post(f"/api/promos/{pid}/film-prompt", headers=headers,
                        json={"segment_s": 30})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert "切成 1 段" in adapter30.prompts[0]


def test_promo_film_prompt_rejects_bad_segment_s(client):
    """单段时长白名单外(20s):400 说人话。"""
    headers = _auth(client, "pfp_badseg")
    pid = _seed_promo(_uid(client, "pfp_badseg"))

    r = client.post(f"/api/promos/{pid}/film-prompt", headers=headers,
                    json={"segment_s": 20})
    assert r.status_code == 400 and "15 / 30" in r.json()["detail"]


def test_promo_film_prompt_save_and_ownership(client):
    headers = _auth(client, "pfp_save")
    pid = _seed_promo(_uid(client, "pfp_save"))

    r = client.put(f"/api/promos/{pid}/film-prompt", headers=headers,
                   json={"film_prompt": "自己写的宣传片整片提示词"})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/promos/{pid}/film-prompt", headers=headers).json()["film_prompt"] \
        == "自己写的宣传片整片提示词"

    other = _auth(client, "pfp_other")
    assert client.get(f"/api/promos/{pid}/film-prompt", headers=other).status_code == 404
    assert client.put(f"/api/promos/{pid}/film-prompt", headers=other,
                      json={"film_prompt": "偷改"}).status_code == 404
