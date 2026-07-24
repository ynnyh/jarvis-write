# tests/test_style_profile.py
# -*- coding: utf-8 -*-
"""创作偏好档案测试:拼装器注入 + JSON 解析 + GET/PUT/absorb 接口。

档案存在 project.global_tendency["_profile"],复用倾向拼装器注入所有生成环节。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import app.api.projects as proj_mod
from app.api.projects import _parse_profile_json
from app.auth import build_token
from app.db.models import Architecture, Chapter, Project, User
from app.db.session import SessionLocal
from app.engines.tendency.assembler import (
    assemble_tendency,
    merge_tendency,
    render_style_block,
)
from app.main import app


# ---------- 拼装器:档案注入 ----------
def test_profile_renders_into_block():
    gt = {"_profile": {"style": "冷峻克制", "taboos": "不要后宫", "audience": "", "other": ""}}
    block = render_style_block(assemble_tendency("chapter", None, gt))
    assert "创作偏好档案" in block
    assert "文风:冷峻克制" in block
    assert "禁忌/避雷:不要后宫" in block
    assert "读者定位" not in block  # 空字段不出现


def test_profile_and_tendency_both_render_profile_first():
    gt = {"pace": "快节奏", "_profile": {"style": "白描"}}
    block = render_style_block(assemble_tendency("chapter", None, gt))
    assert "创作偏好档案" in block
    assert "本次写作倾向" in block
    # 档案优先级更高,排在倾向之前
    assert block.index("创作偏好档案") < block.index("本次写作倾向")


def test_profile_not_misprocessed_as_tag():
    # _profile 是 dict,不能被当成标签维度塞进倾向文本
    gt = {"_profile": {"style": "白描"}}
    assembled = assemble_tendency("chapter", None, gt)
    assert "{" not in assembled.directives_text
    assert "_profile" not in assembled.applied


def test_empty_profile_no_block():
    assert render_style_block(assemble_tendency("chapter", None, {})) == ""
    gt = {"_profile": {"style": "", "taboos": "", "audience": "", "other": ""}}
    assert render_style_block(assemble_tendency("chapter", None, gt)) == ""


def test_override_cannot_replace_profile():
    # 单次临时倾向覆盖不了全书档案(档案只来自 global)
    gt = {"_profile": {"style": "冷峻"}}
    merged = merge_tendency(gt, {"_profile": {"style": "华丽"}})
    assert merged["_profile"]["style"] == "冷峻"


# ---------- _parse_profile_json ----------
def test_parse_bare_json():
    out = _parse_profile_json('{"style": "白描", "taboos": "不要后宫"}')
    assert out["style"] == "白描"
    assert out["taboos"] == "不要后宫"
    assert out["audience"] == ""


def test_parse_code_fence():
    raw = '```json\n{"style": "冷峻", "other": "留反转"}\n```'
    out = _parse_profile_json(raw)
    assert out["style"] == "冷峻"
    assert out["other"] == "留反转"


def test_parse_surrounding_text():
    raw = '好的,这是档案:{"audience": "初中生"} 以上。'
    assert _parse_profile_json(raw)["audience"] == "初中生"


# ---------- GET / PUT / absorb 接口 ----------
@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def seeded():
    """用户 + 带标签倾向的项目(用于验证档案不会冲掉标签)。"""
    db = SessionLocal()
    try:
        user = User(username=f"prof-{uuid.uuid4().hex[:8]}", password_hash="x", is_active=True)
        db.add(user)
        db.flush()
        project = Project(
            title="档案测试书", user_id=user.id, target_chapters=1,
            global_tendency={"pace": "快节奏"},
        )
        db.add(project)
        db.commit()
        token = build_token(user.id)
        yield token, project.id
    finally:
        db.close()


def test_get_profile_empty_by_default(client, seeded):
    token, pid = seeded
    r = client.get(f"/api/projects/{pid}/style-profile",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"style": "", "taboos": "", "audience": "", "other": ""}


def test_put_then_get_profile(client, seeded):
    token, pid = seeded
    r = client.put(f"/api/projects/{pid}/style-profile",
                   json={"style": "冷峻克制", "taboos": "不要后宫"},
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["style"] == "冷峻克制"
    assert r.json()["taboos"] == "不要后宫"
    r2 = client.get(f"/api/projects/{pid}/style-profile",
                    headers={"Authorization": f"Bearer {token}"})
    assert r2.json()["style"] == "冷峻克制"


def test_put_preserves_tag_tendencies(client, seeded):
    token, pid = seeded
    client.put(f"/api/projects/{pid}/style-profile", json={"style": "白描"},
               headers={"Authorization": f"Bearer {token}"})
    db = SessionLocal()
    p = db.query(Project).filter(Project.id == pid).first()
    assert p.global_tendency.get("pace") == "快节奏"  # 标签没被冲掉
    assert p.global_tendency["_profile"]["style"] == "白描"
    db.close()


def test_put_empty_clears_profile(client, seeded):
    token, pid = seeded
    client.put(f"/api/projects/{pid}/style-profile", json={"style": "白描"},
               headers={"Authorization": f"Bearer {token}"})
    client.put(f"/api/projects/{pid}/style-profile", json={"style": ""},
               headers={"Authorization": f"Bearer {token}"})
    db = SessionLocal()
    p = db.query(Project).filter(Project.id == pid).first()
    assert "_profile" not in (p.global_tendency or {})
    db.close()


def test_profile_requires_auth(client, seeded):
    _, pid = seeded
    assert client.get(f"/api/projects/{pid}/style-profile").status_code == 401


# ---------- absorb:LLM 归类合并(打桩适配器) ----------
class _AbsorbAdapter:
    def __init__(self, reply):
        self._reply = reply

    async def ask(self, prompt, system=None):
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def test_absorb_merges_via_llm(client, seeded, monkeypatch):
    token, pid = seeded
    reply = '{"style": "冷峻克制", "taboos": "不要后宫", "audience": "初中生", "other": ""}'
    monkeypatch.setattr(proj_mod, "get_adapter_for", lambda task, **kw: _AbsorbAdapter(reply))
    r = client.post(f"/api/projects/{pid}/style-profile/absorb",
                    json={"directive": "文风冷峻点,别写后宫,面向初中生"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["style"] == "冷峻克制"
    assert body["taboos"] == "不要后宫"
    assert body["audience"] == "初中生"


def test_absorb_degrades_on_llm_failure(client, seeded, monkeypatch):
    token, pid = seeded
    monkeypatch.setattr(proj_mod, "get_adapter_for",
                        lambda task, **kw: _AbsorbAdapter(RuntimeError("boom")))
    r = client.post(f"/api/projects/{pid}/style-profile/absorb",
                    json={"directive": "主角别降智"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert "主角别降智" in r.json()["other"]  # 降级:原文并进其他主张


# ---------- extract:从已有内容反向提炼(打桩适配器) ----------
@pytest.fixture()
def seeded_with_content():
    """用户 + 项目 + 架构 + 一章定稿正文(用于提炼)。"""
    db = SessionLocal()
    try:
        user = User(username=f"extr-{uuid.uuid4().hex[:8]}", password_hash="x", is_active=True)
        db.add(user)
        db.flush()
        project = Project(title="提炼测试书", user_id=user.id, target_chapters=3,
                          topic="一个复仇者追查真相的故事")
        db.add(project)
        db.flush()
        db.add(Architecture(
            project_id=project.id, core_seed="复仇与救赎",
            character_dynamics="主角隐忍", world_building="冷峻都市", plot_architecture="三幕",
        ))
        db.add(Chapter(
            project_id=project.id, chapter_number=1, status="finalized",
            final_content="雨下了一整夜。他站在巷口,没有打伞。" * 20,
        ))
        db.commit()
        token = build_token(user.id)
        yield token, project.id
    finally:
        db.close()


def test_extract_from_content(client, seeded_with_content, monkeypatch):
    token, pid = seeded_with_content
    reply = '{"style": "冷峻克制", "taboos": "", "audience": "成人悬疑读者", "other": "每章留钩子"}'
    monkeypatch.setattr(proj_mod, "get_adapter_for", lambda task, **kw: _AbsorbAdapter(reply))
    r = client.post(f"/api/projects/{pid}/style-profile/extract",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["style"] == "冷峻克制"
    assert body["audience"] == "成人悬疑读者"
    # 已直接落库
    db = SessionLocal()
    p = db.query(Project).filter(Project.id == pid).first()
    assert p.global_tendency["_profile"]["style"] == "冷峻克制"
    db.close()


def test_extract_empty_book_400(client, seeded, monkeypatch):
    # seeded 项目无架构、无正文、无 topic → 没东西可提炼
    token, pid = seeded
    monkeypatch.setattr(proj_mod, "get_adapter_for",
                        lambda task, **kw: _AbsorbAdapter('{"style": "x"}'))
    r = client.post(f"/api/projects/{pid}/style-profile/extract",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


def test_extract_llm_failure_502(client, seeded_with_content, monkeypatch):
    token, pid = seeded_with_content
    monkeypatch.setattr(proj_mod, "get_adapter_for",
                        lambda task, **kw: _AbsorbAdapter(RuntimeError("boom")))
    r = client.post(f"/api/projects/{pid}/style-profile/extract",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 502
    # 失败不保存
    db = SessionLocal()
    p = db.query(Project).filter(Project.id == pid).first()
    assert "_profile" not in (p.global_tendency or {})
    db.close()
