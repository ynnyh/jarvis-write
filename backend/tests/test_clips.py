# tests/test_clips.py
# -*- coding: utf-8 -*-
"""情绪短片工坊测试(批产三本子/三选一/锚段兜底/切段/金句溯源/导出,TestClient + mock LLM)。

验证点:
- CRUD 与归属隔离;非法主题/时长/方向 → 400
- 批产:三个候选、归一化(镜头上限/时长收敛/切段分组)、画风锚兜底
- 三选一:pick 后 clip 落定、status=picked;无效序号 400
- 小说衍生:无定稿章节 → 引导错误;金句 quote_source 不在正文节选 → cautions 标注(溯源红线)
- 导出:md 手卡含金句/切段;srt 时间轴与分镜同轴
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
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload, ensure_ascii=False)

    async def ask(self, prompt, system=None):
        return self._raw


def _candidate(take: str, prompt_cn: str, quote: str = "") -> dict:
    return {
        "take": take,
        "logline": f"{take}的一支 15 秒短片",
        "emotion_curve": "平静→屏息→空",
        "lines": [
            {"speaker": "旁白", "text": "有些话,隔了十年才说。", "action": "空教室,粉笔灰浮动"},
        ],
        "shots": [
            {"seq": 1, "scene_name": "空教室", "characters": [],
             "action_desc": "空教室,阳光切出粉笔灰", "shot_type": "全景", "camera": "固定",
             "dialogue": "有些话,隔了十年才说。", "duration_s": 8,
             "prompt_cn": prompt_cn, "prompt_en": "empty classroom, dust in sunbeam", "negative": "低分辨率"},
            {"seq": 2, "scene_name": "空教室", "characters": [],
             "action_desc": "黑板上的字被擦到一半,定格", "shot_type": "特写", "camera": "推",
             "dialogue": "", "duration_s": 7,
             "prompt_cn": "黑板半擦的字特写,含水墨颗粒质感", "prompt_en": "half-erased blackboard closeup", "negative": ""},
        ],
        "punchline": "没说出口的,才最难消化。",
        "quote_source": quote,
        "hook_text": "他读了二十年她的遗书",
    }


_BATCH_REPLY = {
    "style_name": "电影感实拍·冷蓝",
    "style_cn": "实拍电影感,冷蓝主色,自然光,胶片颗粒",
    "style_en": "cinematic live footage, cold blue tones, film grain",
    "negative": "文字,水印",
    "clips": [
        _candidate("未说出口的道歉", "空教室全景(故意漏画风锚)"),
        _candidate("删掉的聊天记录", "手机屏幕特写,含实拍电影感,冷蓝主色,自然光,胶片颗粒"),
        _candidate("空椅子", "长椅空了一半,含实拍电影感,冷蓝主色,自然光,胶片颗粒"),
    ],
}


def test_clips_generic_flow(client):
    """通用流:创建校验 → 批产(归一化/兜底/切段) → 三选一 → 导出。"""
    headers = _auth(client, "clips_user")
    other = _auth(client, "clips_other")

    # 校验:无主题 400 / 非法时长 400 / 非法方向 400
    assert client.post("/api/clips", headers=headers,
                       json={"theme": "", "custom_theme": "", "duration_s": 15}).status_code == 400
    assert client.post("/api/clips", headers=headers,
                       json={"theme": "regret", "duration_s": 20}).status_code == 400
    assert client.post("/api/clips", headers=headers,
                       json={"theme": "regret", "duration_s": 15, "direction": "pixel"}).status_code == 400

    r = client.post("/api/clips", headers=headers, json={
        "theme": "regret", "duration_s": 15, "direction": "live",
        "inspiration": "异地恋的最后一通电话",
    })
    assert r.status_code == 200, r.text
    cid = r.json()["clip_row"]["id"]

    # 归属隔离
    assert client.get(f"/api/clips/{cid}", headers=other).status_code == 404

    with patch("app.engines.clips.batch.get_adapter_for",
               return_value=_JsonAdapter(_BATCH_REPLY)):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    candidates = job["result"]["candidates"]
    assert len(candidates) == 3
    c0 = candidates[0]
    # 15s → 镜头上限 5(两格都保留);总时长 8+7=15 切一段
    assert len(c0["shots"]) == 2
    assert len(c0["chunks"]) == 1 and c0["chunks"][0]["duration_s"] == 15
    # 镜头1 故意漏画风锚 → 引擎兜底注入
    assert "实拍电影感" in c0["shots"][0]["prompt_cn"]
    assert "cinematic live footage" in c0["shots"][0]["prompt_en"]
    # 负面基座并入
    assert c0["shots"][1]["negative"].startswith("文字,水印")
    assert c0["punchline"]

    # 三选一
    r = client.post(f"/api/clips/{cid}/pick", headers=headers, json={"index": 1})
    assert r.status_code == 200
    row = r.json()["clip_row"]
    assert row["chosen"] == 1 and row["status"] == "picked"
    assert row["clip"]["take"] == "删掉的聊天记录"
    # 无效序号被参数校验拦截
    assert client.post(f"/api/clips/{cid}/pick", headers=headers,
                       json={"index": 9}).status_code == 422

    # 导出
    r = client.get(f"/api/clips/{cid}/export?format=md", headers=headers)
    assert r.status_code == 200
    assert "情绪短片手卡" in r.text and "金句字幕卡" in r.text and "生成切段" in r.text
    r = client.get(f"/api/clips/{cid}/export?format=srt", headers=headers)
    assert "1\n00:00:00,000 --> 00:00:08,000\n有些话,隔了十年才说。" in r.text


def test_clips_novel_derived_grounding(client):
    """小说衍生:无定稿章节报错;金句不在正文 → cautions 溯源标注;在正文 → 无警示。"""
    headers = _auth(client, "clips_novel")
    r = client.post("/api/projects", headers=headers, json={"title": "投流书"})
    pid = r.json()["id"]

    # 没有定稿章节 → job error 引导
    r = client.post("/api/clips", headers=headers, json={
        "theme": "regret", "duration_s": 15, "source_project_id": pid,
    })
    cid = r.json()["clip_row"]["id"]
    r = client.post(f"/api/clips/{cid}/generate", headers=headers)
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error" and "定稿" in job["error"]

    # 种一章定稿正文
    from app.db.session import SessionLocal
    from app.db.models import Chapter

    with SessionLocal() as s:
        s.add(Chapter(project_id=pid, chapter_number=1, status="approved",
                      final_content="他把她的遗书读了二十年,纸角都磨圆了。"))
        s.commit()

    # 候选1 金句不在正文(编造) → 溯源警示;候选2 引用原句 → 干净
    reply = dict(_BATCH_REPLY)
    reply["clips"] = [
        _candidate("编造金句", "空教室全景,含实拍电影感,冷蓝主色,自然光,胶片颗粒",
                   quote="这句在正文里根本不存在"),
        _candidate("真引用", "长椅空了一半,含实拍电影感,冷蓝主色,自然光,胶片颗粒",
                   quote="纸角都磨圆了"),
    ]
    with patch("app.engines.clips.batch.get_adapter_for", return_value=_JsonAdapter(reply)):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    cands = job["result"]["candidates"]
    assert any("未在正文节选中找到" in c for c in cands[0]["cautions"])
    assert cands[1]["cautions"] == []
    # 列表按项目过滤
    r = client.get(f"/api/clips?project_id={pid}", headers=headers)
    assert [c["id"] for c in r.json()["clips"]] == [cid]
