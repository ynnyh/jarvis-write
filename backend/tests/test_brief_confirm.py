# tests/test_brief_confirm.py
# -*- coding: utf-8 -*-
"""开书对话式确认流(确认链 L0):简介聊天 + 拍板门禁。

背景(2026-09-24 重构):选流派/没灵感不再直接抽引擎卡——先和策划聊出
一版完整简介,作者拍板(brief_confirmed)才解锁概念深化。覆盖:
- POST /api/projects/{pid}/brief-chat:回 reply+brief,线程/草稿落库,新草稿自动重新上锁
- PATCH brief:内容变了旧拍板作废(除非同次显式带 brief_confirmed)
- POST /api/projects/{pid}/concept-from-brief-async:未拍板 409(硬门在后端),
  拍板后 job 跑完回六字段概念,拍板简介必须注入深化 prompt
"""
from __future__ import annotations

import json
import time

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


def _create_project(client: TestClient, headers: dict, title: str, **extra) -> dict:
    body = {"title": title, "target_chapters": 3}
    body.update(extra)
    r = client.post("/api/projects", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


class _FakeAdapter:
    """捕获 prompt 的假适配器:ask() 返回预设内容,记录最后一次 prompt 供断言。"""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.last_prompt = ""

    async def ask(self, prompt: str, **kw) -> str:  # noqa: ANN003
        self.last_prompt = prompt
        return self.payload


_BRIEF_JSON = json.dumps({
    "reply": "接住了。世界观底盘你想要哪一种:①乱世江湖纯现实 ②末法修仙 ③架空低武?",
    "brief": "【故事内核】落魄镖师接下退休前最后一趟不许开箱的险镖,验货夜发现箱中藏着"
             "通缉的前朝公主\n【主角】李镖头,四十岁,想金盆洗手却被最后一趟镖锁死\n"
             "【困境与破局】江湖规矩要她死,他的良心要她活;破局靠老底子的镖行绝活\n"
             "【世界观底盘】未定\n【味道与连载引擎】冷峻里带热血;每趟镖一个新局",
}, ensure_ascii=False)

_CONCEPT_JSON = json.dumps({
    "logline": "落魄镖师押送一趟不许开箱的险镖,验货夜发现箱中藏着通缉的前朝公主",
    "hook": "规矩与良心的每一次碰撞",
    "twist": "雇主就是当年灭她满门的人",
    "protagonist": "李镖头,四十岁,想金盆洗手却被最后一趟镖锁死",
    "conflict": "江湖规矩要她死,自己良心要她活",
    "setting": "乱世末年,镖局行业凋零",
    "sell": "每一趟不问来路的镖,都是一次良心问价",
}, ensure_ascii=False)


def test_brief_chat_saves_draft_and_relocks(client, monkeypatch):
    """一轮对话:reply+brief 落库;即使之前拍过板,新草稿也自动回未拍板。"""
    headers = _auth(client, "brief_chat_user")
    p = _create_project(client, headers, "简介聊天书", topic="按「武侠」的套路来",
                        global_tendency={"genre": "武侠"})
    # 预置「已拍板」状态:聊出新草稿必须重新上锁
    r = client.patch(f"/api/projects/{p['id']}", headers=headers,
                     json={"brief": "旧简介", "brief_confirmed": True})
    assert r.status_code == 200 and r.json()["brief_confirmed"] is True

    from app.api.projects import brief as brief_mod

    adapter = _FakeAdapter(_BRIEF_JSON)
    monkeypatch.setattr(brief_mod, "get_adapter_for", lambda task: adapter)
    r = client.post(f"/api/projects/{p['id']}/brief-chat", headers=headers,
                    json={"message": "想写个镖师的故事,箱子里藏人那种"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["reply"].startswith("接住了")
    assert "【故事内核】" in data["brief"]  # 五段开书订单结构
    assert "【世界观底盘】" in data["brief"]
    proj = data["project"]
    assert proj["brief"] == data["brief"]
    assert proj["brief_confirmed"] is False  # 新草稿 → 重新上锁
    roles = [m["role"] for m in proj["chat_log"]]
    assert roles[-2:] == ["user", "assistant"]
    # 倾向必须进 prompt(流派是聊天的硬上下文);对谈必须是「逐项问」口径
    assert "武侠" in adapter.last_prompt
    assert "开书订单" in adapter.last_prompt
    assert "带结构逐项问" in adapter.last_prompt


def test_patch_brief_relock_semantics(client):
    """PATCH 语义:简介内容变了旧拍板作废;同次显式带 brief_confirmed= true 才保留。"""
    headers = _auth(client, "brief_patch_user")
    p = _create_project(client, headers, "简介拍板书")
    pid = p["id"]

    r = client.patch(f"/api/projects/{pid}", headers=headers,
                     json={"brief": "第一版简介", "brief_confirmed": True})
    assert r.json()["brief_confirmed"] is True

    # 只改内容 → 自动回未拍板
    r = client.patch(f"/api/projects/{pid}", headers=headers, json={"brief": "第二版简介"})
    assert r.json()["brief"] == "第二版简介"
    assert r.json()["brief_confirmed"] is False

    # 内容+显式拍板同次 → 保留(「拍板前顺手改一个字」的合并意图)
    r = client.patch(f"/api/projects/{pid}", headers=headers,
                     json={"brief": "第三版简介", "brief_confirmed": True})
    assert r.json()["brief_confirmed"] is True


def test_concept_from_brief_hard_gate(client):
    """硬门在后端:没简介/没拍板 → 409,不烧任何 LLM。"""
    headers = _auth(client, "brief_gate_user")
    p = _create_project(client, headers, "门禁书")
    pid = p["id"]

    r = client.post(f"/api/projects/{pid}/concept-from-brief-async", headers=headers)
    assert r.status_code == 409 and "还没有简介" in r.json()["detail"]

    r = client.patch(f"/api/projects/{pid}", headers=headers, json={"brief": "草稿订单"})
    assert r.status_code == 200
    r = client.post(f"/api/projects/{pid}/concept-from-brief-async", headers=headers)
    assert r.status_code == 409 and "还没拍板" in r.json()["detail"]


def test_concept_from_brief_job_develops(client, monkeypatch):
    """拍板后:job 跑完回六字段概念;拍板订单作为最高约束注入深化 prompt。"""
    headers = _auth(client, "brief_dev_user")
    p = _create_project(client, headers, "深化书", topic="按「武侠」的套路来",
                        global_tendency={"genre": "武侠"})
    pid = p["id"]
    r = client.patch(f"/api/projects/{pid}", headers=headers,
                     json={"brief": "【故事内核】镖师护送前朝公主的险镖故事", "brief_confirmed": True})
    assert r.status_code == 200

    from app.api import inspire as inspire_mod

    adapter = _FakeAdapter(_CONCEPT_JSON)
    monkeypatch.setattr(inspire_mod, "get_adapter_for", lambda task: adapter)

    r = client.post(f"/api/projects/{pid}/concept-from-brief-async", headers=headers)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    deadline = time.monotonic() + 30
    while True:
        job = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        assert job["status"] != "error", job
        if job["status"] == "done":
            break
        assert time.monotonic() < deadline, f"job 超时: {job}"
        time.sleep(0.02)

    concept = job["result"]["concept"]
    assert "镖师" in concept["logline"]
    assert concept["sell"].startswith("每一趟")
    # 拍板订单必须原文进 prompt(深化不许偷换的锚)
    assert "镖师护送前朝公主的险镖故事" in adapter.last_prompt
    assert "已拍板的开书订单" in adapter.last_prompt
