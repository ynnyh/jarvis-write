# tests/test_premise_dossier.py
# -*- coding: utf-8 -*-
"""核心梗卡 + 本章作战图:梗卡 CRUD(AI 建议不覆盖 human)、作战图聚合与降级。"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class _FakeAdapter:
    """返回预置回复,记录收到的 prompt(断言梗块注入用)。"""

    def __init__(self, raw: str):
        self.raw = raw
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system: str | None = None) -> str:
        self.prompts.append(prompt)
        return self.raw


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mkproject(client: TestClient, headers: dict, title: str) -> int:
    r = client.post("/api/projects", headers=headers,
                    json={"title": title, "target_chapters": 5, "genre": "都市"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


_PREMISE = {
    "high_concept": "救人一次,寿命减一年",
    "payoff": "代价累积→危机→反转,每卷抬一级",
    "beats": ["代价显形", "初次反转", "对手升级"],
    "boundaries": ["能力无代价", "寿命账不兑现"],
    "hook_plan": {"opening": "第一次减寿的恐惧", "mid": "导师身份反转", "climax": "寿命归零之择"},
}


def test_premise_put_get_roundtrip(client):
    """保存后可读回;作者保存即 human;空项目返回 null。"""
    headers = _auth(client, f"pm_{uuid.uuid4().hex[:6]}")

    r = client.get(f"/api/projects/{_mkproject(client, headers, '梗书')}/premise", headers=headers)
    assert r.status_code == 200 and r.json() is None  # 未建:如实 null,不装样子

    pid = _mkproject(client, headers, "梗书2")
    r = client.put(f"/api/projects/{pid}/premise", headers=headers, json=_PREMISE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["high_concept"] == "救人一次,寿命减一年"
    assert body["source"] == "human"
    assert body["beats"] == ["代价显形", "初次反转", "对手升级"]

    r = client.get(f"/api/projects/{pid}/premise", headers=headers)
    assert r.json()["source"] == "human"


def test_suggest_premise_does_not_persist(client):
    """AI 提炼只返回草稿不落库:GET 仍为 null,确认保存是显式的第二次动作。"""
    headers = _auth(client, f"pm_sug_{uuid.uuid4().hex[:6]}")
    pid = _mkproject(client, headers, "提炼书")
    # 概念留空 → 400(先有概念才谈梗)
    r = client.post(f"/api/projects/{pid}/suggest-premise", headers=headers)
    assert r.status_code == 400

    client.patch(f"/api/projects/{pid}", headers=headers,
                 json={"topic": "急诊科医生救人减寿"})
    fake = _FakeAdapter(
        '{"high_concept": "救人减寿", "payoff": "代价换命", "beats": ["代价显形"],'
        ' "boundaries": ["无代价"], "hook_plan": {"opening": "首救"}}'
    )
    with patch("app.api.projects.premise.get_adapter_for", return_value=fake):
        r = client.post(f"/api/projects/{pid}/suggest-premise", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["high_concept"] == "救人减寿"
    assert "急救" in fake.prompts[0] or "减寿" in fake.prompts[0] or "题材" in fake.prompts[0]
    # 未落库
    r = client.get(f"/api/projects/{pid}/premise", headers=headers)
    assert r.json() is None


def test_dossier_aggregates_and_degrades(client):
    """作战图聚合:梗卡/大纲/人物匹配/伏笔账/承上钩子;缺数据如实缺省。"""
    from app.db.models import Entity, Foreshadowing, Outline, Relationship
    from app.db.session import SessionLocal

    headers = _auth(client, f"pm_dos_{uuid.uuid4().hex[:6]}")
    pid = _mkproject(client, headers, "档案书")
    client.put(f"/api/projects/{pid}/premise", headers=headers, json=_PREMISE)

    # 直接入库:6 章大纲 + 两个实体 + 一条自第2章生效的关系 + 一章伏笔
    with SessionLocal() as db:
        from app.db.models import Project
        p = db.query(Project).filter(Project.title == "档案书").first()
        for n in range(1, 7):
            db.add(Outline(project_id=p.id, chapter_number=n, title=f"章{n}",
                           summary=f"第{n}章", characters_involved=["林夏", "顾衍"],
                           premise_beat=f"第{n}拍·测试" if n == 1 else ""))
        e1 = Entity(project_id=p.id, entity_type="character", name="林夏")
        e2 = Entity(project_id=p.id, entity_type="character", name="顾衍")
        db.add_all([e1, e2]); db.flush()
        db.add(Relationship(project_id=p.id, from_entity_id=e1.id, to_entity_id=e2.id,
                            relation="师徒", valid_from=2))
        db.add(Foreshadowing(project_id=p.id, description="腕上倒计时",
                             chapter_planted=1, expected_payoff_chapter=3))
        db.commit()

    # 第 1 章:伏笔本章埋;顾衍关系尚未生效(valid_from=2);承上无
    r = client.get(f"/api/projects/{pid}/chapters/1/dossier", headers=headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["premise"]["high_concept"] == "救人一次,寿命减一年"
    assert d["outline"]["premise_beat"] == "第1拍·测试"
    names = {c["name"]: c for c in d["characters"]}
    assert names["林夏"]["matched"] and names["顾衍"]["matched"]
    assert all(not c["relations"] for c in d["characters"])  # 关系第 2 章才生效
    assert d["foreshadows"]["planted"][0]["description"] == "腕上倒计时"
    assert d["prev_threads"] == []

    # 第 2 章:关系生效;伏笔未到回收期不入逾期;本章无埋设
    r = client.get(f"/api/projects/{pid}/chapters/2/dossier", headers=headers)
    d = r.json()
    rels = [rel for c in d["characters"] for rel in c["relations"]]
    assert rels and rels[0]["relation"] == "师徒"
    assert d["foreshadows"]["planted"] == []

    # 第 5 章:预期回收(3)已过 → 逾期账
    r = client.get(f"/api/projects/{pid}/chapters/5/dossier", headers=headers)
    d = r.json()
    assert d["foreshadows"]["overdue"][0]["description"] == "腕上倒计时"


def test_blueprint_prompt_injects_premise_block():
    """梗卡非空 → 蓝图 prompt 带【全书核心梗】块与梗兑现格式行;空 → 零变化。"""
    from app.prompts import CHAPTER_BLUEPRINT_PROMPT, CHUNKED_BLUEPRINT_PROMPT

    assert "{core_premise_block}" in CHAPTER_BLUEPRINT_PROMPT
    assert "{core_premise_block}" in CHUNKED_BLUEPRINT_PROMPT
    assert "梗兑现" in CHAPTER_BLUEPRINT_PROMPT  # 格式行里有字段说明


def test_plot_map_and_premise_health(client):
    """情节推进图数据 + 体检梗健康度:投影完整、缺数据如实缺省。"""
    from app.db.models import Chapter, PremiseLedger
    from app.db.session import SessionLocal

    headers = _auth(client, f"pm_map_{uuid.uuid4().hex[:6]}")
    pid = _mkproject(client, headers, "推进图书")
    client.put(f"/api/projects/{pid}/premise", headers=headers, json=_PREMISE)

    # 3 章大纲;其中 1、2 章已写;第 2 章有场景卡;第 1 章有兑现账、第 3 章无账
    from app.db.session import SessionLocal as SL
    with SL() as db:
        from app.db.models import Outline, Project
        p = db.query(Project).filter(Project.title == "推进图书").first()
        for n in range(1, 4):
            db.add(Outline(project_id=p.id, chapter_number=n, title=f"章{n}",
                           summary=f"第{n}章", chapter_role="常规推进",
                           emotional_tone="紧绷", premise_beat=f"第{n}拍" if n <= 2 else "",
                           beats=[f"拍{n}"]))
        db.add(Chapter(project_id=p.id, chapter_number=1,
                       draft_content="正文一", final_content="正文一", word_count=3))
        db.add(Chapter(project_id=p.id, chapter_number=2,
                       draft_content="正文二", final_content="正文二", word_count=3))
        db.add(Chapter(project_id=p.id, chapter_number=3,
                       draft_content="正文三", final_content="正文三", word_count=3))
        db.add(PremiseLedger(project_id=p.id, chapter_number=1,
                             fulfilled=True, beat="代价显形", strength=4))
        db.commit()

    # 情节推进图
    r = client.get(f"/api/projects/{pid}/plot-map", headers=headers)
    assert r.status_code == 200, r.text
    m = r.json()
    assert [c["chapter_number"] for c in m["chapters"]] == [1, 2, 3]
    assert m["chapters"][0]["written"] and m["chapters"][2]["written"] is True
    assert m["chapters"][0]["premise_beat"] == "第1拍"
    assert len(m["foreshadows"]) == 0

    # 梗健康度:第 1 章有账兑现,第 2 章有账未兑现,第 3 章已写无账
    client.post(f"/api/projects/{pid}/chapters/2/reconciliation/confirm",
                headers=headers, json={"confirmed_relation_ids": [], "rejected_relation_ids": []})
    db.add(PremiseLedger(project_id=p.id, chapter_number=2,
                         fulfilled=False, beat="", strength=2))
    db.commit()
    r = client.get(f"/api/projects/{pid}/health-report", headers=headers)
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["premise_defined"] is True
    assert h["premise_high_concept"] == "救人一次,寿命减一年"
    assert h["premise_unfulfilled_streak"] >= 1          # 第 2 章未兑现计入连击
    assert 3 in h["premise_uncovered_chapters"]          # 第 3 章已写无账
    assert h["premise_fulfilled_ratio"] is not None
    assert "核心梗健康度" in h["markdown"]


def test_epub_export_has_metadata_and_title_page(client):
    """epub 导出(P2-3):元数据带作者/简介/修改时间,书名页进 spine 首位。"""
    headers = _auth(client, f"pm_epub_{uuid.uuid4().hex[:6]}")
    pid = _mkproject(client, headers, "元数据书")
    # 简介走 topic(synopsis 为空时回落)
    client.patch(f"/api/projects/{pid}", headers=headers,
                 json={"topic": "拿命换命的急救爽文"})
    from app.db.models import Chapter
    from app.db.session import SessionLocal as SL
    with SL() as db:
        from app.db.models import Project
        p = db.query(Project).filter(Project.title == "元数据书").first()
        db.add(Chapter(project_id=p.id, chapter_number=1,
                       draft_content="正文", final_content="第一章正文内容。", word_count=8))
        db.commit()

    r = client.get(f"/api/projects/{pid}/export/epub", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/epub+zip")

    import io as _io
    import zipfile
    z = zipfile.ZipFile(_io.BytesIO(r.content))
    opf = z.read("OEBPS/content.opf").decode("utf-8")
    assert "<dc:creator>pm_epub_" in opf            # 作者 = 用户名
    assert "拿命换命的急救爽文" in opf                # 简介
    assert "dcterms:modified" in opf
    assert "<dc:description>" in opf
    title_page = z.read("OEBPS/title.xhtml").decode("utf-8")
    assert "元数据书" in title_page and "作者:" in title_page
    # 书名页在 spine 首位(先于第一章)
    assert opf.find('idref="titlepage"') < opf.find('idref="c1"')
