# tests/test_import_and_sequel.py
# -*- coding: utf-8 -*-
"""导入修复与开续集(docs/20 同批作者反馈):
- 导入解析:章标题不再带「第N章」前缀(UI 统一前置,重名即 bug)
- 续集:POST /{pid}/sequel 复制文风/架构/梗卡/人物档案,前情提要落第 0 章摘要;
  导入书无摘要 → 起 digest 任务;蓝图生成注入前情块
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.engines.book_import import parse_chapters
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


def _mkproject(client: TestClient, headers: dict, title: str) -> int:
    r = client.post("/api/projects", headers=headers,
                    json={"title": title, "target_chapters": 6, "genre": "悬疑"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


class TestImportParsing:
    def test_chapter_title_strips_prefix(self):
        """「第2章 白色连衣裙」→ 标题只剩「白色连衣裙」;UI 前置章号不再重名。"""
        text = "第1章 可乐\n正文A\n\n第2章 白色连衣裙\n正文B\n\n序章 楔子\n正文C"
        chs = parse_chapters(text)
        assert [c["title"] for c in chs] == ["可乐", "白色连衣裙", "序章 楔子"]
        assert "第2章" not in chs[1]["title"]

    def test_fallback_chapters_have_empty_title(self):
        """无章标题兜底切章(按 4000 字阈值):标题留空,章号由 UI 统一渲染。"""
        para = "她推开门,雨声灌了进来。" * 400  # 单段 4400 字,超阈值即切
        chs = parse_chapters(para + "\n\n" + para)
        assert len(chs) == 2
        assert all(c["title"] == "" for c in chs)

    def test_import_lands_readable(self, client: TestClient):
        """导入的书:正文 approved、可直接检索;不进起步向导(setup_state 为空)。"""
        h = _auth(client, f"imp-{uuid.uuid4().hex[:6]}")
        r = client.post(
            "/api/projects/import-book",
            headers=h,
            files={"file": ("旧书.txt", "第1章 开局\n她推开门。\n\n第2章 追踪\n雨夜跟进。".encode("utf-8"))},
            data={"title": "导入测试"},
        )
        assert r.status_code == 200, r.text
        pid = r.json()["project_id"]
        proj = client.get(f"/api/projects/{pid}", headers=h).json()
        assert proj["title"] == "导入测试"
        chs = client.get(f"/api/projects/{pid}/chapters", headers=h).json()
        assert len(chs) == 2
        assert all(c["status"] == "approved" for c in chs)


class TestSequel:
    def _seed_source(self, client: TestClient, username: str):
        from app.db.models import (
            Architecture, Chapter, ChapterSummary, Entity, Outline, Premise, Project, Relationship,
        )
        from app.db.session import SessionLocal

        h = _auth(client, username)
        pid = _mkproject(client, h, "前作")
        s = SessionLocal()
        try:
            s.add(Architecture(project_id=pid, core_seed="一个循环梗",
                               character_dynamics="双主角", world_building="近未来",
                               plot_architecture="三幕"))
            s.add(Premise(project_id=pid, kind="main", high_concept="救人减寿",
                          payoff="账本循环", beats=["一拍"], boundaries=[], hook_plan={}, source="ai"))
            e1 = Entity(project_id=pid, entity_type="person", name="陈默")
            e2 = Entity(project_id=pid, entity_type="person", name="林晚")
            s.add_all([e1, e2])
            s.flush()
            s.add(Relationship(project_id=pid, from_entity_id=e1.id, to_entity_id=e2.id,
                               relation="搭档", valid_from=1, valid_until=None,
                               evidence_fact_id=None, status="confirmed"))
            data = {"title": "第1章", "summary": "s", "characters_involved": ["陈默"]}
            from app.engines.pipeline.blueprint import _outline_content_hash
            o = Outline(project_id=pid, chapter_number=1,
                        content_hash=_outline_content_hash(data), **data)
            s.add(o)
            s.flush()
            s.add(Chapter(project_id=pid, outline_id=o.id, chapter_number=1,
                          final_content="正文" * 100, word_count=200, status="approved"))
            s.add(ChapterSummary(project_id=pid, chapter_number=1,
                                 rolling_summary="第一部:陈默与林晚结盟,伏笔·账本未收。"))
            # style_memo 给足篇幅:走「不需要文风分析」的路径
            proj = s.get(Project, pid)
            proj.style_memo = "冷峻短句,对话密,章末必留钩。" * 10
            s.commit()
        finally:
            s.close()
        return h, pid

    def test_sequel_copies_assets_and_digest(self, client: TestClient):
        from app.db.models import Architecture, ChapterSummary, Entity, Premise, Project, Relationship
        from app.db.session import SessionLocal

        h, pid = self._seed_source(client, f"seq-{uuid.uuid4().hex[:6]}")
        r = client.post(f"/api/projects/{pid}/sequel", headers=h,
                        json={"title": "第二部", "direction": "账本之谜全面爆发"})
        assert r.status_code == 200, r.text
        new_pid = r.json()["project_id"]
        # 提要已有 + 文风 memo 已足 → 无需分析任务
        assert r.json()["analyze_job_id"] is None

        s = SessionLocal()
        try:
            proj = s.query(Project).filter(Project.id == new_pid).first()
            assert proj is not None and proj.sequel_of_id == pid
            # 字数对齐:目标章内字数 = 前作实际章节字数中位数(200),不是项目设置值
            assert proj.target_words_per_chapter == 200
            assert "续集" in proj.topic or proj.topic == "账本之谜全面爆发"
            assert proj.genre == "悬疑"
            # 架构/梗卡/人物/关系都复制了
            assert s.query(Architecture).filter(Architecture.project_id == new_pid).count() == 1
            assert s.query(Premise).filter(Premise.project_id == new_pid).count() == 1
            ents = s.query(Entity).filter(Entity.project_id == new_pid).all()
            assert {e.name for e in ents} == {"陈默", "林晚"}
            edges = s.query(Relationship).filter(Relationship.project_id == new_pid).all()
            assert len(edges) == 1 and edges[0].valid_from == 0  # 续集开场现状
            # 前情提要落第 0 章摘要行
            row = s.query(ChapterSummary).filter(
                ChapterSummary.project_id == new_pid,
                ChapterSummary.chapter_number == 0,
            ).first()
            assert row is not None and "账本未收" in row.rolling_summary
        finally:
            s.close()

    def test_sequel_blueprint_injects_digest(self, client: TestClient):
        """蓝图生成 prompt 注入前情块;普通书(无第 0 章摘要行)不注入。"""
        from app.api.projects.blueprint import _sequel_prev_block
        from app.db.models import ChapterSummary
        from app.db.session import SessionLocal

        h, pid = self._seed_source(client, f"seq2-{uuid.uuid4().hex[:6]}")
        r = client.post(f"/api/projects/{pid}/sequel", headers=h,
                        json={"title": "第三部", "direction": ""})
        new_pid = r.json()["project_id"]

        s = SessionLocal()
        try:
            block = _sequel_prev_block(s, new_pid)
            assert "前情提要" in block and "账本未收" in block
            # 普通书无第 0 章行 → 空串(prompt 字节级不变)
            assert _sequel_prev_block(s, pid) == ""
        finally:
            s.close()

    def test_sequel_of_imported_book_spawns_analyze_job(self, client: TestClient):
        """导入书没有滚动摘要、没有文风 memo:续集起前作分析任务。"""
        h = _auth(client, f"seqimp-{uuid.uuid4().hex[:6]}")
        r = client.post(
            "/api/projects/import-book",
            headers=h,
            files={"file": ("旧书.txt", "第1章 开局\n正文".encode("utf-8"))},
            data={"title": "导入续集前作"},
        )
        pid = r.json()["project_id"]
        r = client.post(f"/api/projects/{pid}/sequel", headers=h,
                        json={"title": "", "direction": ""})
        assert r.status_code == 200
        assert r.json()["analyze_job_id"] is not None

    def test_sequel_directions_eight_cards(self, client: TestClient):
        """方向卡:AI 出 8 个方向;avoid 名单注入 prompt 防重复。"""
        import json as _json
        from unittest.mock import patch

        h, pid = self._seed_source(client, f"dirs-{uuid.uuid4().hex[:6]}")
        fake = _json.dumps({"directions": [
            {"title": f"方向{i}", "desc": f"第{i}个方向的展开"} for i in range(1, 9)
        ]}, ensure_ascii=False)
        captured: list[str] = []

        class _A:
            async def ask(self, prompt, system=None):
                captured.append(prompt)
                return fake

        with patch("app.llm.router.get_adapter_for", return_value=_A()):
            r = client.post(f"/api/projects/{pid}/sequel-directions-async", headers=h,
                            json={"avoid": ["旧方向甲", "旧方向乙"]})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        import time
        for _ in range(60):
            jr = client.get(f"/api/jobs/{job_id}", headers=h)
            if jr.json().get("status") != "running":
                break
            time.sleep(0.05)
        assert jr.json()["status"] == "done", jr.text
        cards = jr.json()["result"]["directions"]
        assert len(cards) == 8
        assert cards[0]["title"] == "方向1"
        # avoid 注入:防重摇重复
        assert "旧方向甲" in captured[0] and "旧方向乙" in captured[0]
        # 采样与字数口径进了 prompt
        assert "正文采样" in captured[0]


class TestSamplingAndLimits:
    def test_sample_source_stratified_and_bounded(self, client: TestClient):
        """采样全书均匀分层(首/中/尾都覆盖),总输入量恒定,与书体量无关。"""
        from unittest.mock import MagicMock

        from app.api.projects.sequel import _SLICE_CHARS, _SLICE_COUNT, _sample_source

        db = MagicMock()
        # 1000 章的书:采样章数恒为 ≤ 6,且首尾都在
        chapters = [
            MagicMock(chapter_number=i + 1, final_content="字" * 5000) for i in range(1000)
        ]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = chapters
        samples, sizes = _sample_source(db, project_id=1)
        assert samples is not None
        assert samples.count("【第") <= _SLICE_COUNT
        assert "【第1章(节选)】" in samples and "【第1000章(节选)】" in samples
        # 恒定开销:总量封顶(6 × 2500 + 标签开销)
        assert len(samples) < _SLICE_COUNT * _SLICE_CHARS + 500
        assert sizes == [5000] * 1000

        # 少于分层数:有几分层采几分
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = chapters[:3]
        samples3, _ = _sample_source(db, project_id=1)
        assert samples3.count("【第") == 3

    def test_docx_xml_size_guard(self):
        """DOCX 解压后正文 XML 超限要能在读取前拦住(防 zip 解压爆内存)。"""
        from app.engines.book_import import _MAX_DOCX_XML_BYTES

        assert _MAX_DOCX_XML_BYTES == 256 * 1024 * 1024
        from app.engines.book_import import MAX_IMPORT_BYTES

        # 120MB ≈ 4000 万字(UTF-8 中文 3 字节/字),三千万字体量进得来
        assert MAX_IMPORT_BYTES == 120 * 1024 * 1024
        assert MAX_IMPORT_BYTES // 3 >= 30_000_000
