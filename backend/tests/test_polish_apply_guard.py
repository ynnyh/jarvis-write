# tests/test_polish_apply_guard.py
# -*- coding: utf-8 -*-
"""整章优化「应用」的乐观并发守卫。

整章优化耗时数分钟,其间用户可能在正文里手改某段(写进 final_content)。
若应用润色稿时不校验,就会整章覆盖、静默吞掉手改。带 base_content(优化基线)时:
- 当前定稿仍等于基线 → 正常应用;
- 当前定稿已变(手改过)→ 409,不覆盖,手改仍在;
- 不传 base_content(用户在冲突提示里确认强制)→ 照常覆盖,旧内容进版本历史可回退。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _make_chapter(project_id: int, n: int, final: str) -> int:
    """直接落一章定稿(绕过 LLM 生成)。"""
    from app.db.models import Chapter
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.add(Chapter(
            project_id=project_id, chapter_number=n,
            final_content=final, draft_content=final,
            word_count=len(final), status="approved",
        ))
        db.commit()
    finally:
        db.close()
    return n


def _apply(client, h, pid, n, polished, base=None):
    body = {"polished_text": polished}
    if base is not None:
        body["base_content"] = base
    return client.post(
        f"/api/projects/{pid}/polish/chapter/{n}/apply", headers=h, json=body
    )


def _final(client, h, pid, n) -> str:
    return client.get(f"/api/projects/{pid}/chapters/{n}", headers=h).json()["final_content"]


def test_apply_with_matching_base_succeeds(client):
    u = _register(client, "polish_ok")
    h = _auth(u["token"])
    pid = client.post("/api/projects", headers=h, json={"title": "润色书"}).json()["id"]
    _make_chapter(pid, 1, "原始正文")

    r = _apply(client, h, pid, 1, "润色后的正文", base="原始正文")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    assert _final(client, h, pid, 1) == "润色后的正文"


def test_apply_with_stale_base_conflicts_and_preserves_edit(client):
    """优化期间用户手改了正文 → 当前定稿已不等于优化基线 → 409,手改不被覆盖。"""
    u = _register(client, "polish_conflict")
    h = _auth(u["token"])
    pid = client.post("/api/projects", headers=h, json={"title": "冲突书"}).json()["id"]
    _make_chapter(pid, 1, "优化开始时的正文")

    # 模拟优化耗时期间用户手改了正文
    r = client.put(f"/api/projects/{pid}/chapters/1/content",
                   headers=h, json={"final_content": "用户手改后的正文"})
    assert r.status_code == 200, r.text

    # 拿"优化开始时"的旧基线去应用 → 409,且手改仍在(没被静默覆盖)
    r = _apply(client, h, pid, 1, "基于旧基线的润色稿", base="优化开始时的正文")
    assert r.status_code == 409, r.text
    assert _final(client, h, pid, 1) == "用户手改后的正文"


def test_apply_without_base_force_overwrites(client):
    """不传 base_content = 用户确认强制覆盖 → 照常写回,旧内容留痕版本历史可回退。"""
    u = _register(client, "polish_force")
    h = _auth(u["token"])
    pid = client.post("/api/projects", headers=h, json={"title": "强制书"}).json()["id"]
    _make_chapter(pid, 1, "任何旧正文")

    r = _apply(client, h, pid, 1, "强制覆盖的润色稿")  # 不传 base
    assert r.status_code == 200, r.text
    assert _final(client, h, pid, 1) == "强制覆盖的润色稿"

    vs = client.get(f"/api/projects/{pid}/chapters/1/versions", headers=h).json()
    assert any(v["source"] == "polished" for v in vs), "旧内容应留痕(source=polished)"
