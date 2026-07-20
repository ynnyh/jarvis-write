# tests/test_api.py
# -*- coding: utf-8 -*-
"""接口级测试(TestClient + 临时库):注册边界与任务归属隔离。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    # 进入上下文才会跑 lifespan(建表 + 幂等迁移),全程在临时库上
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str, password: str = "pass123") -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_rejects_overlong_password(client):
    """bcrypt 只取前 72 字节,超长密码应返回 400 而非 500。"""
    r = client.post(
        "/api/auth/register",
        json={"username": "long_pw_user", "password": "a" * 73, "invite_code": INVITE},
    )
    assert r.status_code == 400
    assert "密码过长" in r.json()["detail"]


def test_register_wrong_invite_code(client):
    r = client.post(
        "/api/auth/register",
        json={"username": "bad_invite", "password": "pass123", "invite_code": "nope"},
    )
    assert r.status_code == 403


def test_job_ownership_isolation(client):
    """别人的 job 按"不存在"处理(404),不泄露存在性;本人可查。"""
    from app.auth import current_user_id
    from app.jobs import create_job

    a = _register(client, "job_owner_a")
    b = _register(client, "job_owner_b")
    me_a = client.get("/api/auth/me", headers=_auth(a["token"])).json()

    # 以 A 的身份建任务(create_job 同步读取 contextvar)
    token_ctx = current_user_id.set(me_a["id"])
    try:
        job_id = create_job("test-kind")
    finally:
        current_user_id.reset(token_ctx)

    r = client.get(f"/api/jobs/{job_id}", headers=_auth(b["token"]))
    assert r.status_code == 404

    r = client.get(f"/api/jobs/{job_id}", headers=_auth(a["token"]))
    assert r.status_code == 200
    assert r.json()["kind"] == "test-kind"


def test_unauthenticated_401(client):
    assert client.get("/api/projects").status_code == 401


# ---------- 项目重命名 / 删除 ----------


def _create_project(client: TestClient, headers: dict, title: str = "测试书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def test_rename_project_ok(client):
    headers = _auth(_register(client, "rename_owner")["token"])
    p = _create_project(client, headers, "旧书名")

    r = client.patch(f"/api/projects/{p['id']}", headers=headers, json={"title": "新书名"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "新书名"

    r = client.get(f"/api/projects/{p['id']}", headers=headers)
    assert r.json()["title"] == "新书名"


def test_rename_project_rejects_bad_title(client):
    headers = _auth(_register(client, "rename_bad")["token"])
    p = _create_project(client, headers)

    assert client.patch(
        f"/api/projects/{p['id']}", headers=headers, json={"title": "   "}
    ).status_code == 400
    assert client.patch(
        f"/api/projects/{p['id']}", headers=headers, json={"title": "长" * 101}
    ).status_code == 400


def test_rename_project_not_owner_404(client):
    """非 owner 改他人项目标题 → 404(不泄露存在性)。"""
    a = _auth(_register(client, "rename_a")["token"])
    b = _auth(_register(client, "rename_b")["token"])
    p = _create_project(client, a)

    r = client.patch(f"/api/projects/{p['id']}", headers=b, json={"title": "抢书名"})
    assert r.status_code == 404


def test_delete_project_cascades(client):
    """删除后项目、大纲、章节、摘要、事实库全部查不到。"""
    from app.db.models import Chapter, ChapterSummary, Entity, Outline, Project
    from app.db.session import SessionLocal

    headers = _auth(_register(client, "del_owner")["token"])
    p = _create_project(client, headers, "要删的书")

    # 直接落库一些关联数据(走 API 生成太慢且依赖 LLM)
    db = SessionLocal()
    try:
        outline = Outline(project_id=p["id"], chapter_number=1, title="第一章")
        db.add(outline)
        db.flush()
        db.add(Chapter(project_id=p["id"], outline_id=outline.id, chapter_number=1,
                       final_content="正文", status="finalized"))
        db.add(ChapterSummary(project_id=p["id"], chapter_number=1, rolling_summary="摘要"))
        db.add(Entity(project_id=p["id"], entity_type="character", name="张三"))
        db.commit()
    finally:
        db.close()

    r = client.delete(f"/api/projects/{p['id']}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted_chapters"] == 1

    # 接口层:项目 404
    assert client.get(f"/api/projects/{p['id']}", headers=headers).status_code == 404
    # 数据库层:关联数据全部清空
    db = SessionLocal()
    try:
        assert db.get(Project, p["id"]) is None
        for model in (Chapter, ChapterSummary, Entity, Outline):
            assert db.query(model).filter_by(project_id=p["id"]).count() == 0
    finally:
        db.close()


def test_delete_project_not_owner_404(client):
    """非 owner 删他人项目 → 404,且数据不受影响。"""
    a = _auth(_register(client, "del_a")["token"])
    b = _auth(_register(client, "del_b")["token"])
    p = _create_project(client, a, "别删我")

    r = client.delete(f"/api/projects/{p['id']}", headers=b)
    assert r.status_code == 404

    assert client.get(f"/api/projects/{p['id']}", headers=a).status_code == 200


# ---------- embedding 可用性探测 ----------

class _FakeResp:
    model = "deepseek-chat"
    content = "连接成功"
    prompt_tokens = 5
    completion_tokens = 3


class _FakeAdapter:
    def to_messages(self, prompt):
        return []

    async def complete(self, messages):
        return _FakeResp()


def _setup_user_with_key(client, username: str) -> dict:
    """注册并给 deepseek 存一个假 key(走数据库,不碰 .env)。"""
    user = _register(client, username)
    headers = _auth(user["token"])
    r = client.put(
        "/api/settings/providers/deepseek",
        headers=headers,
        json={
            "api_key": "sk-test",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "is_default": True,
        },
    )
    assert r.status_code == 200, r.text
    return headers


def test_provider_test_reports_embedding_ok(client):
    """embed 成功 → 测试连接返回 embedding_ok=true。"""
    from unittest.mock import AsyncMock, patch

    headers = _setup_user_with_key(client, "emb_ok_user")
    with (
        patch("app.api.settings.create_llm_adapter", return_value=_FakeAdapter()),
        patch(
            "app.llm.embeddings.EmbeddingClient.embed",
            new=AsyncMock(return_value=[[0.1, 0.2, 0.3]]),
        ),
    ):
        r = client.post("/api/settings/providers/deepseek/test", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["embedding_ok"] is True
    assert body["embedding_error"] == ""


def test_provider_test_reports_embedding_failure(client):
    """embed 失败 → embedding_ok=false 且带原因,聊天测试不受影响。"""
    from unittest.mock import AsyncMock, patch

    headers = _setup_user_with_key(client, "emb_fail_user")
    with (
        patch("app.api.settings.create_llm_adapter", return_value=_FakeAdapter()),
        patch(
            "app.llm.embeddings.EmbeddingClient.embed",
            new=AsyncMock(side_effect=RuntimeError("404: no embeddings endpoint")),
        ),
    ):
        r = client.post("/api/settings/providers/deepseek/test", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True  # 聊天模型仍通
    assert body["embedding_ok"] is False
    assert "404" in body["embedding_error"]


def test_ping_llm_reports_embedding(client):
    """ping-llm 同样携带 embedding_ok 字段。"""
    from unittest.mock import AsyncMock, patch

    headers = _setup_user_with_key(client, "emb_ping_user")
    with (
        patch("app.api.system.create_llm_adapter", return_value=_FakeAdapter()),
        patch(
            "app.llm.embeddings.EmbeddingClient.embed",
            new=AsyncMock(return_value=[[0.1]]),
        ),
    ):
        r = client.post(
            "/api/ping-llm",
            headers=headers,
            json={"prompt": "hi", "provider": "deepseek"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["embedding_ok"] is True
