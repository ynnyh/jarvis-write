# tests/test_search_api.py
# -*- coding: utf-8 -*-
"""全书全文检索接口测试(FTS5 trigram,迁移 0008 + /search)。

覆盖:三条路径(MATCH ≥3 字 / LIKE <3 字 / 别名 JSON 解码)、索引随写路径
实时同步(ORM 增改删)、项目隔离与归属校验。
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


def _seed(client: TestClient, username: str) -> tuple[int, dict]:
    """注册 + 建项目 + 落一章/一条大纲/一个实体(含中文别名),返回 (pid, headers)。"""
    from app.db.models import Chapter, Entity, Outline
    from app.db.session import SessionLocal

    headers = _auth(_register(client, username)["token"])
    r = client.post("/api/projects", headers=headers, json={"title": f"{username}的书"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    db = SessionLocal()
    try:
        db.add(Chapter(
            project_id=pid, chapter_number=1, status="approved",
            final_content="陆辰摸了摸左耳。耳后玉佩上刻着「寒字七十二」,是娘亲留下的遗物。",
        ))
        db.add(Outline(project_id=pid, chapter_number=1, title="崖下重逢",
                       summary="陆辰在崖底拾得寒玉,三日之限自此起算"))
        db.add(Entity(project_id=pid, entity_type="character", name="陆辰",
                      aliases=["小辰", "辰哥儿"]))
        db.commit()
    finally:
        db.close()
    return pid, headers


def test_match_path_and_grouping(client):
    """≥3 字走 MATCH:正文/大纲/实体别名都能命中并分组。"""
    pid, headers = _seed(client, "fts_owner_a")
    r = client.get(f"/api/projects/{pid}/search", params={"q": "寒字七十二"}, headers=headers)
    assert r.status_code == 200, r.text
    chs = r.json()["grouped"]["chapter"]
    assert chs and chs[0]["chapter_number"] == 1 and "寒字七十二" in chs[0]["snippet"]

    # 大纲组:标题+摘要都在索引里
    r = client.get(f"/api/projects/{pid}/search", params={"q": "三日之限"}, headers=headers)
    outs = r.json()["grouped"]["outline"]
    assert outs and outs[0]["chapter_number"] == 1 and outs[0]["title"] == "崖下重逢"

    # 别名 JSON 解码:alias 存的是 \uXXXX 转义,必须解出原文才能命中
    r = client.get(f"/api/projects/{pid}/search", params={"q": "辰哥儿"}, headers=headers)
    ents = r.json()["grouped"]["entity"]
    assert ents and ents[0]["name"] == "陆辰"


def test_like_path_short_query(client):
    """<3 字走 LIKE 降级:2 字词照样命中正文。"""
    pid, headers = _seed(client, "fts_owner_b")
    r = client.get(f"/api/projects/{pid}/search", params={"q": "左耳"}, headers=headers)
    assert r.status_code == 200
    chs = r.json()["grouped"]["chapter"]
    assert chs and chs[0]["chapter_number"] == 1


def test_index_syncs_with_writes(client):
    """索引随写路径实时同步:改正文立即生效,删章即消失。"""
    from app.db.models import Chapter
    from app.db.session import SessionLocal

    pid, headers = _seed(client, "fts_owner_c")

    # UPDATE:第 2 章写入新词
    db = SessionLocal()
    try:
        db.add(Chapter(project_id=pid, chapter_number=2, status="approved",
                       final_content="灰衣人自雾中走出,袖口绣着半枚残玉。"))
        db.commit()
    finally:
        db.close()
    r = client.get(f"/api/projects/{pid}/search", params={"q": "残玉"}, headers=headers)
    assert any(h["chapter_number"] == 2 for h in r.json()["grouped"]["chapter"])

    # UPDATE:清空正文 → 索引行随之消失
    db = SessionLocal()
    try:
        ch = db.query(Chapter).filter_by(project_id=pid, chapter_number=2).one()
        ch.final_content = ""
        db.commit()
    finally:
        db.close()
    r = client.get(f"/api/projects/{pid}/search", params={"q": "残玉"}, headers=headers)
    assert r.json()["grouped"]["chapter"] == []

    # DELETE:删第 1 章 → 命中清零
    db = SessionLocal()
    try:
        db.query(Chapter).filter_by(project_id=pid, chapter_number=1).delete()
        db.commit()
    finally:
        db.close()
    r = client.get(f"/api/projects/{pid}/search", params={"q": "寒字七十二"}, headers=headers)
    assert r.json()["grouped"]["chapter"] == []


def test_project_isolation_and_ownership(client):
    """命中只在本书范围;别人的项目按不存在处理(404)。"""
    pid_a, headers_a = _seed(client, "fts_owner_d")
    pid_b, _ = _seed(client, "fts_owner_e")

    r = client.get(f"/api/projects/{pid_a}/search", params={"q": "寒字七十二"}, headers=headers_a)
    assert r.status_code == 200
    assert all(h["chapter_number"] == 1 for h in r.json()["grouped"]["chapter"])

    r = client.get(f"/api/projects/{pid_a}/search", params={"q": "寒字七十二"}, headers=_auth(_register(client, "fts_intruder")["token"]))
    assert r.status_code == 404

    # B 的新书还没有"寒字七十二"(数据在 A 书)
    r = client.get(f"/api/projects/{pid_b}/search", params={"q": "寒字七十二"}, headers=_auth(_register(client, "fts_owner_f")["token"]))
    assert r.status_code == 404  # 不是 B 的项目


def test_empty_query_rejected(client):
    pid, headers = _seed(client, "fts_owner_g")
    r = client.get(f"/api/projects/{pid}/search", params={"q": "   "}, headers=headers)
    assert r.status_code == 422


def test_case_insensitive_english(client):
    """英文大小写:SQL(LIKE/trigram)不分大小写,Python 计数与摘要同样不分,
    大小写相异的英文命中不再被 n_hits==0 过滤误丢。"""
    from app.db.models import Chapter
    from app.db.session import SessionLocal

    pid, headers = _seed(client, "fts_owner_h")
    db = SessionLocal()
    try:
        db.add(Chapter(project_id=pid, chapter_number=3, status="approved",
                       final_content="Kern 学长把校音器递过来,寒字七十二的拓片压在箱底。"))
        db.commit()
    finally:
        db.close()

    # MATCH 路径(4 字):小写查询命中大写原文,计数 ≥1
    r = client.get(f"/api/projects/{pid}/search", params={"q": "kern"}, headers=headers)
    chs = r.json()["grouped"]["chapter"]
    assert any(h["chapter_number"] == 3 and h["hits"] == 1 for h in chs)

    # LIKE 路径(2 字):同样命中,且摘要保留原文大小写
    r = client.get(f"/api/projects/{pid}/search", params={"q": "ke"}, headers=headers)
    chs = r.json()["grouped"]["chapter"]
    assert any(h["chapter_number"] == 3 and "Kern" in h["snippet"] for h in chs)
