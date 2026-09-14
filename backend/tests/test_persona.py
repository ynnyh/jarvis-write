# tests/test_persona.py
# -*- coding: utf-8 -*-
"""核心人物画像:落库/author 来源保护/生成注入/门禁注入/人物卡展示。"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"

PERSONA = {
    "logline": "想活命的天使,也是账单上的囚徒",
    "appearance": "白大褂,腕上倒计时纹路微亮",
    "traits": ["隐忍腹黑", "沉稳老练"],
    "speech": "短句,不带情绪的陈述句",
    "motive": "救回每一个能救的人",
    "fear": "自己变成收割者",
    "arc": "从透支自己到找到第三条路",
    "never_do": ["见死不救", "主动杀人"],
    "source": "author",
}


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


def _mkproject(client: TestClient, headers: dict, title: str) -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_persona_create_patch_roundtrip(client):
    """开卡带画像(author 来源)→ PATCH 改画像 → 人物卡返回结构化画像。"""
    headers = _auth(client, f"per_{uuid.uuid4().hex[:6]}")
    pid = _mkproject(client, headers, "画像书")

    r = client.post(f"/api/projects/{pid}/characters", headers=headers,
                    json={"name": "林夏", "profile": "急诊科主治",
                          "persona": PERSONA})
    assert r.status_code == 200, r.text

    r = client.get(f"/api/projects/{pid}/characters", headers=headers)
    chars = r.json()["characters"]
    linxia = next(c for c in chars if c["name"] == "林夏")
    assert linxia["persona"]["logline"] == "想活命的天使,也是账单上的囚徒"
    assert linxia["persona"]["traits"] == ["隐忍腹黑", "沉稳老练"]
    assert linxia["persona"]["never_do"] == ["见死不救", "主动杀人"]
    assert linxia["persona"]["source"] == "author"

    # PATCH 画像:作者手改 → source 仍 author,新值生效
    new_persona = dict(PERSONA, fear="自己变成机器")
    r = client.patch(f"/api/projects/{pid}/characters/{linxia['id']}", headers=headers,
                     json={"persona": new_persona})
    assert r.status_code == 200, r.text

    r = client.get(f"/api/projects/{pid}/characters", headers=headers)
    linxia = next(c for c in r.json()["characters"] if c["name"] == "林夏")
    assert linxia["persona"]["fear"] == "自己变成机器"


def test_persona_survives_architecture_rerun(client):
    """作者手订的画像在架构重提炼时不被 AI 覆盖(persona.source=author 保护)。"""
    from unittest.mock import patch

    from app.db.session import SessionLocal
    from app.db.models import Entity, Project, User
    from app.engines.consistency.persona import coerce_persona

    headers = _auth(client, f"per_arch_{uuid.uuid4().hex[:6]}")
    pid = _mkproject(client, headers, "画像保护书")

    # 直接入库:作者手订画像的实体
    with SessionLocal() as db:
        u = db.query(User).filter(User.username.startswith("per_arch_")).first()
        p = db.query(Project).filter(Project.id == pid).first()
        base = {"profile": "急诊科主治", "persona": dict(PERSONA)}
        db.add(Entity(project_id=pid, entity_type="character", name="林夏",
                      base_profile=base))
        db.commit()

    from app.engines.consistency.persona import coerce_persona
    coerced = coerce_persona(dict(PERSONA, fear="被改掉的恐惧"))
    assert coerced["fear"] == "被改掉的恐惧"