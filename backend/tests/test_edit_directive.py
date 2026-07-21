# tests/test_edit_directive.py
# -*- coding: utf-8 -*-
"""修改指令接口测试:解析预览(mock LLM)/ 应用落库 / 归属隔离。"""
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


def _create_project(client: TestClient, headers: dict, title: str = "指令书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _seed_outlines(project_id: int, with_chapter_content: bool = False) -> None:
    """直接落库两章大纲(第1章可选带定稿正文),走 API 生成依赖 LLM 太慢。"""
    from app.db.models import Chapter, Outline
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        o1 = Outline(project_id=project_id, chapter_number=1, title="拜师",
                     summary="男主拜反派为师,男二出场相助")
        o2 = Outline(project_id=project_id, chapter_number=2, title="下山",
                     summary="男主与男二下山历练")
        db.add(o1)
        db.add(o2)
        db.flush()
        if with_chapter_content:
            db.add(Chapter(project_id=project_id, outline_id=o1.id, chapter_number=1,
                           final_content="正文……", status="finalized"))
        db.commit()
    finally:
        db.close()


class _FakeAdapter:
    """假适配器:返回预设文本,并记录 prompt。"""

    def __init__(self, text: str, captured: dict | None = None):
        self._text = text
        self._captured = captured if captured is not None else {}

    async def ask(self, prompt, system=None):
        self._captured["prompt"] = prompt
        return self._text


_LLM_JSON = (
    '{"analysis": "男二戏份并入女主,前两章受影响",'
    ' "items": ['
    '  {"chapter_number": 1, "new_title": "拜师奇遇",'
    '   "new_summary": "男主拜反派为师,女主出场相助",'
    '   "change_reason": "男二出场章,戏份并给女主"},'
    '  {"chapter_number": 2,'
    '   "new_summary": "男主与女主下山历练",'
    '   "change_reason": "同行者由男二改为女主"},'
    '  {"chapter_number": 99, "new_summary": "幻觉章号应被丢弃"},'
    '  {"chapter_number": "x", "new_summary": "坏章号应被丢弃"}'
    ' ],'
    ' "suggest_retire": ["男二"]}'
)


def test_parse_directive_ok(client):
    """mock LLM:返回结构化预览;幻觉/坏章号被过滤;prompt 注入指令与蓝图。"""
    from unittest.mock import patch

    headers = _auth(client, "dir_parse_ok")
    p = _create_project(client, headers)
    _seed_outlines(p["id"])

    captured: dict = {}
    with patch(
        "app.api.edit_directive.get_adapter_for",
        return_value=_FakeAdapter(_LLM_JSON, captured),
    ):
        r = client.post(
            f"/api/projects/{p['id']}/edit-directive",
            headers=headers,
            json={"directive": "不要男二,让他的戏份并给女主"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "男二" in body["analysis"]
    assert [i["chapter_number"] for i in body["items"]] == [1, 2]
    item1 = body["items"][0]
    assert item1["new_title"] == "拜师奇遇"
    assert item1["new_summary"] == "男主拜反派为师,女主出场相助"
    assert item1["change_reason"]
    assert body["items"][1]["new_title"] is None
    assert body["suggest_retire"] == ["男二"]
    # prompt 注入了指令、架构简报占位与全部章蓝图
    assert "不要男二" in captured["prompt"]
    assert "第1章《拜师》" in captured["prompt"]
    assert "第2章《下山》" in captured["prompt"]


def test_parse_directive_no_hit(client):
    """items 为空(无章节受影响)是合法结果,不是错误。"""
    from unittest.mock import patch

    headers = _auth(client, "dir_parse_nohit")
    p = _create_project(client, headers, "无命中书")
    _seed_outlines(p["id"])

    raw = '{"analysis": "指令与现有章节无关", "items": [], "suggest_retire": []}'
    with patch(
        "app.api.edit_directive.get_adapter_for", return_value=_FakeAdapter(raw)
    ):
        r = client.post(
            f"/api/projects/{p['id']}/edit-directive",
            headers=headers,
            json={"directive": "把序章改成倒叙"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert "无关" in body["analysis"]


def test_parse_directive_json_tolerance(client):
    """容错:markdown 围栏 + 前后解释文字也能解析(走 parse_llm_json)。"""
    from unittest.mock import patch

    headers = _auth(client, "dir_parse_tol")
    p = _create_project(client, headers, "容错书")
    _seed_outlines(p["id"])

    raw = (
        "好的,分析如下:\n```json\n"
        '{"analysis": "第2章受影响", "items": ['
        '{"chapter_number": 2, "new_summary": "男主独自下山",'
        ' "change_reason": "男二被删"}], "suggest_retire": []}\n'
        "```\n以上。"
    )
    with patch(
        "app.api.edit_directive.get_adapter_for", return_value=_FakeAdapter(raw)
    ):
        r = client.post(
            f"/api/projects/{p['id']}/edit-directive",
            headers=headers,
            json={"directive": "不要男二"},
        )
    assert r.status_code == 200, r.text
    assert [i["chapter_number"] for i in r.json()["items"]] == [2]


def test_parse_directive_unparseable_502(client):
    """模型输出完全不是 JSON → 502 提示重试。"""
    from unittest.mock import patch

    headers = _auth(client, "dir_parse_bad")
    p = _create_project(client, headers, "坏输出书")
    _seed_outlines(p["id"])

    with patch(
        "app.api.edit_directive.get_adapter_for",
        return_value=_FakeAdapter("我不太理解你的意思"),
    ):
        r = client.post(
            f"/api/projects/{p['id']}/edit-directive",
            headers=headers,
            json={"directive": "不要男二"},
        )
    assert r.status_code == 502


def test_parse_directive_validation(client):
    """空指令/超长指令 → 400;无蓝图 → 400。"""
    headers = _auth(client, "dir_parse_val")
    p = _create_project(client, headers, "校验书")
    _seed_outlines(p["id"])

    assert client.post(
        f"/api/projects/{p['id']}/edit-directive",
        headers=headers, json={"directive": "   "},
    ).status_code == 400
    assert client.post(
        f"/api/projects/{p['id']}/edit-directive",
        headers=headers, json={"directive": "长" * 501},
    ).status_code == 400

    empty = _create_project(client, headers, "无蓝图书")
    r = client.post(
        f"/api/projects/{empty['id']}/edit-directive",
        headers=headers, json={"directive": "不要男二"},
    )
    assert r.status_code == 400
    assert "蓝图" in r.json()["detail"]


def test_apply_directive_versioning_and_stale(client):
    """应用:升版本 + OutlineVersion(change_summary=修改指令)+ 正文标失配。"""
    from app.db.models import Chapter, Outline, OutlineVersion
    from app.db.session import SessionLocal

    headers = _auth(client, "dir_apply")
    p = _create_project(client, headers, "应用书")
    _seed_outlines(p["id"], with_chapter_content=True)

    r = client.post(
        f"/api/projects/{p['id']}/edit-directive/apply",
        headers=headers,
        json={"items": [
            {"chapter_number": 1, "new_title": "拜师奇遇",
             "new_summary": "男主拜反派为师,女主出场相助"},
            {"chapter_number": 2, "new_summary": "男主与女主下山历练"},
            {"chapter_number": 77, "new_summary": "不存在的章应被跳过"},
        ]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == [1, 2]
    assert body["stale_chapters"] == [1]  # 只有第1章有正文

    db = SessionLocal()
    try:
        o1 = db.query(Outline).filter_by(project_id=p["id"], chapter_number=1).one()
        assert o1.title == "拜师奇遇"
        assert o1.summary == "男主拜反派为师,女主出场相助"
        assert o1.current_version == 2
        v = db.query(OutlineVersion).filter_by(outline_id=o1.id, version=2).one()
        assert v.change_summary == "修改指令"
        assert v.snapshot["title"] == "拜师奇遇"

        o2 = db.query(Outline).filter_by(project_id=p["id"], chapter_number=2).one()
        assert o2.title == "下山"  # 未传 new_title → 标题不变
        assert o2.current_version == 2

        ch = db.query(Chapter).filter_by(project_id=p["id"], chapter_number=1).one()
        assert ch.is_stale is True
        assert ch.status == "stale"
    finally:
        db.close()

    # 内容无实质变化 → 不产生新版本,不在 updated 里
    r = client.post(
        f"/api/projects/{p['id']}/edit-directive/apply",
        headers=headers,
        json={"items": [
            {"chapter_number": 1, "new_title": "拜师奇遇",
             "new_summary": "男主拜反派为师,女主出场相助"},
        ]},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": [], "stale_chapters": []}
    db = SessionLocal()
    try:
        o1 = db.query(Outline).filter_by(project_id=p["id"], chapter_number=1).one()
        assert o1.current_version == 2
    finally:
        db.close()


def test_apply_directive_empty_items_422(client):
    headers = _auth(client, "dir_apply_empty")
    p = _create_project(client, headers, "空应用书")
    _seed_outlines(p["id"])
    assert client.post(
        f"/api/projects/{p['id']}/edit-directive/apply",
        headers=headers, json={"items": []},
    ).status_code == 422


def test_directive_not_owner_404(client):
    """非 owner 调两个端点 → 404(不泄露存在性)。"""
    from unittest.mock import patch

    a = _auth(client, "dir_own_a")
    b = _auth(client, "dir_own_b")
    p = _create_project(client, a, "别人的指令书")
    _seed_outlines(p["id"])

    with patch(
        "app.api.edit_directive.get_adapter_for",
        return_value=_FakeAdapter(_LLM_JSON),
    ):
        assert client.post(
            f"/api/projects/{p['id']}/edit-directive",
            headers=b, json={"directive": "不要男二"},
        ).status_code == 404
    assert client.post(
        f"/api/projects/{p['id']}/edit-directive/apply",
        headers=b,
        json={"items": [{"chapter_number": 1, "new_summary": "x"}]},
    ).status_code == 404
