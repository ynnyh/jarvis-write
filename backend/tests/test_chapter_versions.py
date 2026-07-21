# tests/test_chapter_versions.py
# -*- coding: utf-8 -*-
"""章节正文版本快照:覆盖前留痕 + 新旧对比 + 回滚(接口级,无需 LLM)。

覆盖:
- 手动编辑正文 → 覆盖前把旧版存进 chapter_versions(source=edited)
- 版本列表按版本号倒序、不含全文;取某版含全文(供对比)
- 回滚:先把当前版留痕(source=restored),再换回目标版正文
- 空章不留痕(无内容可回退)
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
    """直接落一章正文(绕过 LLM 生成),返回 chapter_number。"""
    from app.db.models import Chapter
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        ch = Chapter(
            project_id=project_id, chapter_number=n,
            final_content=final, draft_content=final,
            word_count=len(final), status="finalized",
        )
        db.add(ch)
        db.commit()
    finally:
        db.close()
    return n


def test_edit_snapshots_and_restore(client):
    u = _register(client, "ver_user")
    h = _auth(u["token"])
    pid = client.post("/api/projects", headers=h, json={"title": "版本书"}).json()["id"]
    n = _make_chapter(pid, 1, "初版正文内容")

    # 两次手改 → 两版快照(覆盖前的"初版""二版")
    r = client.put(f"/api/projects/{pid}/chapters/{n}/content",
                   headers=h, json={"final_content": "二版正文内容"})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/projects/{pid}/chapters/{n}/content",
                   headers=h, json={"final_content": "三版正文内容"})
    assert r.status_code == 200

    # 版本列表:2 条,最新在前,均 source=edited,不含全文
    vs = client.get(f"/api/projects/{pid}/chapters/{n}/versions", headers=h).json()
    assert [v["version"] for v in vs] == [2, 1]
    assert all(v["source"] == "edited" for v in vs)
    assert "final_content" not in vs[0]

    # 取最早一版全文用于对比
    v1 = next(v for v in vs if v["version"] == 1)
    detail = client.get(
        f"/api/projects/{pid}/chapters/{n}/versions/{v1['id']}", headers=h
    ).json()
    assert detail["final_content"] == "初版正文内容"

    # 回滚到初版:当前(三版)再留一痕 source=restored,正文换回初版
    r = client.post(
        f"/api/projects/{pid}/chapters/{n}/versions/{v1['id']}/restore", headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["final_content"] == "初版正文内容"

    vs2 = client.get(f"/api/projects/{pid}/chapters/{n}/versions", headers=h).json()
    assert [v["version"] for v in vs2] == [3, 2, 1]
    assert vs2[0]["source"] == "restored"  # 回滚前的三版被留痕
    restored_detail = client.get(
        f"/api/projects/{pid}/chapters/{n}/versions/{vs2[0]['id']}", headers=h
    ).json()
    assert restored_detail["final_content"] == "三版正文内容"


def test_empty_chapter_leaves_no_version(client):
    """空章被首次写入正文,不该产生历史版本(无旧内容可留痕)。"""
    u = _register(client, "ver_empty_user")
    h = _auth(u["token"])
    pid = client.post("/api/projects", headers=h, json={"title": "空章书"}).json()["id"]

    from app.db.models import Chapter
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.add(Chapter(project_id=pid, chapter_number=1, status="empty"))
        db.commit()
    finally:
        db.close()

    r = client.put(f"/api/projects/{pid}/chapters/1/content",
                   headers=h, json={"final_content": "第一次写入"})
    assert r.status_code == 200
    vs = client.get(f"/api/projects/{pid}/chapters/1/versions", headers=h).json()
    assert vs == []


def test_restore_missing_version_404(client):
    u = _register(client, "ver_404_user")
    h = _auth(u["token"])
    pid = client.post("/api/projects", headers=h, json={"title": "404书"}).json()["id"]
    _make_chapter(pid, 1, "正文")
    r = client.post(
        f"/api/projects/{pid}/chapters/1/versions/99999/restore", headers=h
    )
    assert r.status_code == 404
