# tests/test_evolution.py
# -*- coding: utf-8 -*-
"""进化系统 P0(docs/36):旋钮 / 复盘 / 记忆管理 / 开书注入。

守的是什么:
1. **off = 停止采集**:off 档复盘 409、开书不注入——这是对「不想被学」用户的硬承诺;
2. **记忆是用户资产**:跨用户 404,可删可清,删了立即不影响下一次注入;
3. **只落有证据的条目**:LLM 输出必须过收敛(白名单/截断/去重),坏输出不得落库。
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db.models import Chapter, EvolutionMemory, GenInput, User
from app.db.session import SessionLocal
from app.main import app

INVITE = "test-invite"

_RETRO_JSON = json.dumps(
    {
        "summary": "冷面细节控,账房题材,讨厌段尾抒情",
        "memories": [
            {"kind": "style", "content": "段尾停在动作上,不做总结升华",
             "evidence": "第1章草稿删掉全部段尾抒情", "confidence": "high"},
            {"kind": "lesson", "content": "配角对白收着点,不许抢主角戏",
             "evidence": "作者重写要求:配角收着点", "confidence": "medium"},
            # 非法 kind / 非法 confidence / 空内容:收敛时应被规整或丢弃
            {"kind": "bogus", "content": "非法kind要归为偏好", "evidence": "x", "confidence": "extreme"},
            {"kind": "style", "content": "", "evidence": "空内容要丢弃"},
            {"kind": "style", "content": "段尾停在动作上,不做总结升华",
             "evidence": "与第一条重复,要去重"},
        ],
    },
    ensure_ascii=False,
)

_READBACK_JSON = json.dumps(
    {
        "world": "当代都市·老城区", "viewpoint": "第三人称·跟老周",
        "cast": "账房先生老周(半退休)", "flavor": ["冷幽默"],
        "must": ["账本细节"], "avoid": ["穿越"], "genre": "都市职场",
    },
    ensure_ascii=False,
)


class _JsonAdapter:
    """只实现 ask 的最小适配器(复盘/回读端点都只用 ask)。"""

    def __init__(self, text: str):
        self._text = text
        self.last_prompt = ""

    async def ask(self, prompt, system=None):
        self.last_prompt = prompt
        return self._text


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str) -> tuple[dict, int]:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    with SessionLocal() as db:
        uid = db.query(User).filter_by(username=username).first().id
    return {"Authorization": f"Bearer {token}"}, uid


def _project(client: TestClient, headers: dict, title: str = "账房") -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _approved_chapter(project_id: int, number: int = 1, *, with_draft: bool = True) -> None:
    with SessionLocal() as db:
        draft = "老周把账本合上。窗外的雨还在下,他想,这一生就像一本流水账,蜿蜒而绵长,终究归入尘土,化作记忆里的一缕青烟,飘散在岁月的长河之中。" if with_draft else ""
        final = "老周把账本合上,起身关窗。雨点砸在玻璃上,他数到第七下,回头把今天的账页折了个角。"
        db.add(Chapter(
            project_id=project_id, chapter_number=number, status="approved",
            draft_content=draft, final_content=final,
            word_count=len(final),
        ))
        db.commit()


def _set_level(client: TestClient, headers: dict, level: str) -> None:
    r = client.put("/api/evolution/config", headers=headers, json={"level": level})
    assert r.status_code == 200, r.text


# —— 旋钮 ——
def test_config_default_off_update_and_validate(client: TestClient):
    headers, _ = _register(client, "evo_cfg")
    r = client.get("/api/evolution/config", headers=headers)
    assert r.status_code == 200 and r.json() == {"level": "off"}

    r = client.put("/api/evolution/config", headers=headers, json={"level": "confirm"})
    assert r.status_code == 200 and r.json() == {"level": "confirm"}
    assert client.get("/api/evolution/config", headers=headers).json() == {"level": "confirm"}

    assert client.put(
        "/api/evolution/config", headers=headers, json={"level": "banana"}
    ).status_code == 422
    # 未登录:旋钮也不许动
    assert client.get("/api/evolution/config").status_code == 401


# —— 复盘:开关与前置条件 ——
def test_retrospective_off_rejected(client: TestClient):
    headers, _ = _register(client, "evo_off")
    pid = _project(client, headers)
    _approved_chapter(pid)
    r = client.post(f"/api/projects/{pid}/retrospective", headers=headers)
    assert r.status_code == 409
    assert "进化开关" in r.json()["detail"]
    with SessionLocal() as db:
        assert db.query(EvolutionMemory).count() == 0


def test_retrospective_needs_approved_chapter(client: TestClient):
    headers, _ = _register(client, "evo_empty")
    pid = _project(client, headers)
    _set_level(client, headers, "confirm")
    r = client.post(f"/api/projects/{pid}/retrospective", headers=headers)
    assert r.status_code == 400
    assert "定稿" in r.json()["detail"]


# —— 复盘:confirm 档,候选 pending,输出过收敛 ——
def test_retrospective_confirm_mode(client: TestClient):
    headers, uid = _register(client, "evo_confirm")
    pid = _project(client, headers)
    _approved_chapter(pid)
    with SessionLocal() as db:  # 作者指令证据
        db.add(GenInput(project_id=pid, kind="chapter-revision",
                        payload={"directive": "配角收着点,别抢主角戏"}))
        db.commit()
    _set_level(client, headers, "confirm")

    adapter = _JsonAdapter(_RETRO_JSON)
    with patch("app.engines.evolution.reflect.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/retrospective", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] == "confirm"
    assert body["summary"] == "冷面细节控,账房题材,讨厌段尾抒情"
    # 收敛:非法 kind→preference、非法 confidence→medium、空内容丢弃、重复去重 → 3 条
    assert len(body["memories"]) == 3
    assert all(m["status"] == "pending" for m in body["memories"])
    assert body["memories"][2]["kind"] == "preference"
    assert body["memories"][2]["confidence"] == "medium"
    # prompt 带证据包:采纳样本 + 删改痕迹 + 指令
    assert "作者采纳的正文样本" in adapter.last_prompt
    assert "主要删改" in adapter.last_prompt
    assert "配角收着点" in adapter.last_prompt

    with SessionLocal() as db:
        rows = db.query(EvolutionMemory).filter_by(user_id=uid).all()
        assert len(rows) == 3 and all(row.status == "pending" for row in rows)
        kinds = {row.kind for row in rows}
        assert kinds == {"style", "lesson", "preference"}
        traced = db.query(GenInput).filter_by(kind="evolution-retrospective", project_id=pid).all()
        assert len(traced) == 1 and traced[0].project_id == pid

    # 重复复盘:同一句教训不重复入库(去重),没有新条目时如实返回空列表
    with patch("app.engines.evolution.reflect.get_adapter_for", return_value=_JsonAdapter(_RETRO_JSON)):
        r = client.post(f"/api/projects/{pid}/retrospective", headers=headers)
    assert r.status_code == 200
    assert r.json()["memories"] == []
    with SessionLocal() as db:
        assert db.query(EvolutionMemory).filter_by(user_id=uid).count() == 3


# —— 复盘:auto 档,直接 confirmed ——
def test_retrospective_auto_mode_confirms(client: TestClient):
    headers, _ = _register(client, "evo_auto")
    pid = _project(client, headers)
    _approved_chapter(pid)
    _set_level(client, headers, "auto")
    with patch("app.engines.evolution.reflect.get_adapter_for", return_value=_JsonAdapter(_RETRO_JSON)):
        r = client.post(f"/api/projects/{pid}/retrospective", headers=headers)
    assert r.status_code == 200
    assert all(m["status"] == "confirmed" for m in r.json()["memories"])


# —— 复盘:LLM 输出不可用 → 502 且不落库 ——
def test_retrospective_llm_garbage_no_persist(client: TestClient):
    headers, uid = _register(client, "evo_bad")
    pid = _project(client, headers)
    _approved_chapter(pid)
    _set_level(client, headers, "confirm")
    with patch(
        "app.engines.evolution.reflect.get_adapter_for",
        return_value=_JsonAdapter("这不是 JSON,模型今天抽风了"),
    ):
        r = client.post(f"/api/projects/{pid}/retrospective", headers=headers)
    assert r.status_code == 502
    with SessionLocal() as db:
        assert db.query(EvolutionMemory).filter_by(user_id=uid).count() == 0


# —— 记忆管理:跨用户 404,可删可清 ——
def test_memory_isolation_and_management(client: TestClient):
    headers_a, uid_a = _register(client, "evo_owner")
    headers_b, _ = _register(client, "evo_stranger")
    with SessionLocal() as db:
        row = EvolutionMemory(
            user_id=uid_a, project_id=None, kind="lesson",
            content="不要在第二章之前让主角受伤", evidence="x", confidence="high",
            status="confirmed",
        )
        db.add(row)
        db.commit()
        mid = row.id

    # B 看不见也动不了 A 的记忆
    assert client.get("/api/evolution/memories", headers=headers_b).json() == {"items": []}
    assert client.patch(
        f"/api/evolution/memories/{mid}", headers=headers_b, json={"status": "dismissed"}
    ).status_code == 404
    assert client.delete(f"/api/evolution/memories/{mid}", headers=headers_b).status_code == 404

    # A:改状态 / 删除 / 清空
    r = client.patch(
        f"/api/evolution/memories/{mid}", headers=headers_a, json={"status": "dismissed"}
    )
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert client.patch(
        f"/api/evolution/memories/{mid}", headers=headers_a, json={"status": "whatever"}
    ).status_code == 422
    assert client.delete(f"/api/evolution/memories/{mid}", headers=headers_a).json() == {"ok": True}
    assert client.get("/api/evolution/memories", headers=headers_a).json() == {"items": []}

    with SessionLocal() as db:
        db.add(EvolutionMemory(user_id=uid_a, kind="style", content="c1", status="confirmed"))
        db.add(EvolutionMemory(user_id=uid_a, kind="style", content="c2", status="pending"))
        db.commit()
    r = client.post("/api/evolution/memories/clear", headers=headers_a)
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert client.get("/api/evolution/memories", headers=headers_a).json() == {"items": []}


# —— 开书注入:confirm 过的记忆进回读 prompt,off/无记忆不进 ——
def test_readback_injects_confirmed_memories(client: TestClient):
    headers, uid = _register(client, "evo_inject")
    pid = _project(client, headers)
    _approved_chapter(pid)
    _set_level(client, headers, "confirm")
    with patch("app.engines.evolution.reflect.get_adapter_for", return_value=_JsonAdapter(_RETRO_JSON)):
        r = client.post(f"/api/projects/{pid}/retrospective", headers=headers)
    assert r.status_code == 200
    # 作者拍板第一条(style)
    mid = r.json()["memories"][0]["id"]
    assert client.patch(
        f"/api/evolution/memories/{mid}", headers=headers,
        json={"status": "confirmed"},
    ).status_code == 200

    adapter = _JsonAdapter(_READBACK_JSON)
    with patch("app.api.projects.intent.get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{pid}/intent-readback", headers=headers,
            json={"topic": "老城区账房先生的故事"},
        )
    assert r.status_code == 200, r.text
    assert "【作者历史偏好与教训" in adapter.last_prompt
    assert "段尾停在动作上" in adapter.last_prompt
    # 未拍板的 lesson 不注入
    assert "不许抢主角戏" not in adapter.last_prompt
    with SessionLocal() as db:
        traced = db.query(GenInput).filter_by(kind="evolution-injection", project_id=pid).all()
        assert len(traced) == 1 and traced[0].payload["count"] == 1


def test_readback_no_injection_when_off_or_empty(client: TestClient):
    headers, uid = _register(client, "evo_noinject")
    pid = _project(client, headers)
    # 有 confirmed 记忆但 off 档:不注入(off = 停止采集也停止注入)
    with SessionLocal() as db:
        db.add(EvolutionMemory(
            user_id=uid, kind="style", content="段尾停在动作上",
            status="confirmed",
        ))
        db.commit()
    adapter = _JsonAdapter(_READBACK_JSON)
    with patch("app.api.projects.intent.get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{pid}/intent-readback", headers=headers,
            json={"topic": "一个普通点子"},
        )
    assert r.status_code == 200
    assert "【作者历史偏好与教训" not in adapter.last_prompt
    with SessionLocal() as db:
        assert db.query(GenInput).filter_by(kind="evolution-injection", project_id=pid).count() == 0
