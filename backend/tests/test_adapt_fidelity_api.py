# tests/test_adapt_fidelity_api.py
# -*- coding: utf-8 -*-
"""改编质量验收的 API 面(docs/15 §5.3):剧本线 + 漫剧线两个保真度端点。

端点只读、确定性、零 LLM——本文件验证:
- 没有改编产物时不报 0%(报 404 / 「未评估」语义)
- 有产物时算出保真度,并把关键未落地事实挑出来
- 漫剧线按各集 source_chapters 的并集限定取材范围
- 归属隔离

端点级测试用 `with TestClient(app)`(裸 TestClient 不走 lifespan/迁移)。
"""
from __future__ import annotations

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
        json={"username": username, "password": "pw123456", "invite_code": INVITE},
    )
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _project(client: TestClient, headers: dict, title: str) -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _seed_facts(pid: int) -> None:
    """种角色 + 三条事实(供改编核对的「原著关键设定」)。"""
    from app.db.session import SessionLocal
    from app.db.models import Entity, Fact

    with SessionLocal() as s:
        hero = Entity(project_id=pid, entity_type="character", name="沈砚",
                      aliases=[], base_profile={})
        s.add(hero)
        s.flush()
        for i, (content, imp) in enumerate([
            ("沈砚的左臂被刀贯穿,伤口已结痂", "critical"),
            ("断锋刀在黑衣人手上", "major"),
            ("落脚在荒山破庙", "minor"),
        ], 1):
            s.add(Fact(project_id=pid, entity_id=hero.id, fact_type="state",
                       content=content, valid_from=i, importance=imp,
                       source_chapter=i))
        s.commit()


def _seed_chapter(pid: int, n: int = 1) -> None:
    from app.db.session import SessionLocal
    from app.db.models import Chapter

    with SessionLocal() as s:
        s.add(Chapter(project_id=pid, chapter_number=n,
                      final_content="沈砚按刀立于雪中。" * 40,
                      word_count=400, status="approved"))
        s.commit()


def _seed_script(pid: int, user_id: int, *contents: str, note: str = "") -> int:
    """种一部改编剧本(绕过 LLM:直接建 Script + ScriptEpisode)。"""
    from app.db.session import SessionLocal
    from app.db.models import Script, ScriptEpisode

    with SessionLocal() as s:
        script = Script(user_id=user_id, source_project_id=pid,
                        title="改编稿", target_episodes=len(contents),
                        status="outlined", style_memo=note)
        s.add(script)
        s.flush()
        for i, body in enumerate(contents, 1):
            s.add(ScriptEpisode(script_id=script.id, episode_number=i,
                                title=f"第{i}集", content=body, status="drafted"))
        s.commit()
        return script.id


def _me_id(client: TestClient, headers: dict) -> int:
    return client.get("/api/auth/me", headers=headers).json()["id"]


# ---------------- 剧本线 ----------------

def test_script_fidelity_404_without_adaptation(client):
    headers = _auth(client, "fid_script_none")
    pid = _project(client, headers, "还没改编的书")
    r = client.get(f"/api/projects/{pid}/adapt-fidelity", headers=headers)
    assert r.status_code == 404, r.text
    assert "改编" in r.json()["detail"]


def test_script_fidelity_reports_lost_and_kept(client):
    headers = _auth(client, "fid_script_ok")
    pid = _project(client, headers, "已改编的书")
    uid = _me_id(client, headers)
    _seed_facts(pid)
    # 保住 critical,丢掉 major/minor。改编稿要够长才进比对(短稿=未评估)
    _seed_script(pid, uid, "沈砚的左臂被刀贯穿,伤口已结痂。他咬着牙站起来。" * 8)

    r = client.get(f"/api/projects/{pid}/adapt-fidelity", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["facts_total"] == 3
    assert body["facts_kept"] == 1
    assert body["ratio"] == round(1 / 3, 4)
    assert body["episodes_drafted"] == 1
    # critical 那一条在稿里保住了(不是 lost_critical)。丢掉的是 major 那条,
    # 而它并非 critical,所以 lost_critical 为空——这正是要区分的语义。
    assert body["lost_critical"] == []
    lost = {c["content"] for c in body["lost"]}
    assert "断锋刀在黑衣人手上" in lost
    assert "落脚在荒山破庙" in lost
    assert body["adapted_chars"] > 0
    assert "%" in body["render"]


def test_script_fidelity_unevaluated_when_nothing_drafted(client):
    """有剧本但一集都没写 → 报「未评估」(facts_total=0),不是 0% 保真度。"""
    headers = _auth(client, "fid_script_empty")
    pid = _project(client, headers, "只有大纲的书")
    uid = _me_id(client, headers)
    _seed_facts(pid)
    _seed_script(pid, uid, "")  # 空正文

    r = client.get(f"/api/projects/{pid}/adapt-fidelity", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["facts_total"] == 0
    assert body["episodes_drafted"] == 0
    assert body["render"] == ""


def test_script_fidelity_note_issues_surface(client):
    headers = _auth(client, "fid_script_note")
    pid = _project(client, headers, "声明不一致的书")
    uid = _me_id(client, headers)
    _seed_facts(pid)
    _seed_script(
        pid, uid,
        "断锋刀在黑衣人手上,寒光一闪。" * 8,
        note="保留了沈砚的左臂被刀贯穿这条设定。",
    )
    body = client.get(f"/api/projects/{pid}/adapt-fidelity", headers=headers).json()
    assert any("找不到" in i for i in body["note_issues"]), body["note_issues"]


def test_script_fidelity_ownership_isolation(client):
    headers = _auth(client, "fid_script_owner")
    pid = _project(client, headers, "归属校验的书")
    other = _auth(client, "fid_script_other")
    assert client.get(f"/api/projects/{pid}/adapt-fidelity", headers=other).status_code == 404


# ---------------- 漫剧线 ----------------

def _seed_drama_episode(pid: int, ep_index: int, source: list[int], lines: list[dict]) -> None:
    from app.db.session import SessionLocal
    from app.db.models import DramaEpisode

    with SessionLocal() as s:
        s.add(DramaEpisode(
            project_id=pid, ep_index=ep_index, title=f"第{ep_index}集",
            source_chapter=source[0], source_chapters=source,
            mode="dialogue", duration_target_s=90,
            script={"mode": "dialogue", "lines": lines},
        ))
        s.commit()


def test_drama_fidelity_scopes_to_source_chapters(client):
    headers = _auth(client, "fid_drama_ok")
    pid = _project(client, headers, "漫剧保真书")
    _seed_facts(pid)
    # 只有第 1 集,取材第 1 章 → 只核第 1 章为止的事实(1 条)
    _seed_drama_episode(pid, 1, [1], [
        {"speaker": "沈砚", "text": "我的左臂被刀贯穿,伤口已结痂,不碍事。", "action": "他抬起缠布的手臂"},
        {"speaker": "旁白", "text": "雪还在下,破庙的屋顶漏着风,火堆快灭了。",
         "action": "冷风灌进庙门,火光晃了一下"},
        {"speaker": "沈砚", "text": "断锋刀不在我手上,我把它留在了雪地里。", "action": "他盯着门外"},
        {"speaker": "旁白", "text": "远处传来马蹄声,越来越近,像是三骑并辔。",
         "action": "雪地上多了一行蹄印"},
    ])

    r = client.get(f"/api/projects/{pid}/drama/adapt-fidelity", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["covered_chapters"] == [1]
    assert body["adapted_chars"] > 0, body
    assert body["facts_total"] == 1, body
    assert body["facts_kept"] == 1
    assert body["ratio"] == 1.0
    assert body["episodes_scripted"] == 1


def test_drama_fidelity_no_script_is_unevaluated(client):
    headers = _auth(client, "fid_drama_empty")
    pid = _project(client, headers, "还没写剧本的漫剧书")
    _seed_facts(pid)
    _seed_drama_episode(pid, 1, [1], [])  # 空台词

    body = client.get(f"/api/projects/{pid}/drama/adapt-fidelity", headers=headers).json()
    assert body["episodes_scripted"] == 0
    assert body["facts_total"] == 0
    assert body["render"] == ""


def test_drama_fidelity_ownership_isolation(client):
    headers = _auth(client, "fid_drama_owner")
    pid = _project(client, headers, "漫剧归属校验书")
    other = _auth(client, "fid_drama_other")
    assert client.get(
        f"/api/projects/{pid}/drama/adapt-fidelity", headers=other
    ).status_code == 404
