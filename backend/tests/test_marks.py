# tests/test_marks.py
# -*- coding: utf-8 -*-
"""跨章标记 + 全书批修单测(mock LLM,无需 API key)。

覆盖:
- 标记 CRUD:记标记(同段同快照幂等改意见)、清单按章/段序、删除销账、
  无正文章节记标记 404
- 全书批修核心(revise_marks):总描述与该处意见合并下发给 polish_fragment、
  快照失配跳过计入 stale、单条失败不拖垮整批、跨章分组有序、job 不落库
  (标记保持 open,由前端验收后销账)、无标记直接空结果
- 接口层:revise-async 无标记 400
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

TEXT1 = "第一段铺垫。\n他摩挲着那朵铁锈玫瑰,铁片硌得指腹生疼。\n第三段收尾。"
TEXT2 = "她低头不语。\n又想起扎进胸口的铁片,一阵钝痛。\n窗外天快亮了。"


def _make_db(*chapters: tuple[int, str]):
    """独立内存库:一个项目 + 若干章正文。chapters: [(章号, 正文)]。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(title="标记测试书", target_chapters=10, target_words_per_chapter=3000)
    db.add(project)
    db.flush()
    for n, text in chapters:
        db.add(Chapter(
            project_id=project.id, outline_id=n, chapter_number=n,
            final_content=text, word_count=len(text), status="approved",
        ))
    db.commit()
    return db, project


def _add_mark(db, project_id, chapter_number, para_idx, snapshot, note=""):
    from app.db.models import ChapterMark

    row = ChapterMark(
        project_id=project_id, chapter_number=chapter_number,
        para_idx=para_idx, snapshot=snapshot, note=note, status="open",
    )
    db.add(row)
    db.commit()
    return row


class _FakePolish:
    """polish_fragment 替身:记录调用参数,回写缓存结果(可按 old 文本定制)。"""

    def __init__(self, results: dict[str, str] | None = None, fail: set[str] | None = None):
        self.calls: list[tuple[str, str]] = []  # (fragment, direction)
        self.results = results or {}
        self.fail = fail or set()

    async def __call__(self, fragment, direction="", chapter_summary="", voice_block=""):
        self.calls.append((fragment, direction))
        if fragment in self.fail:
            raise ValueError("片段超长" if len(fragment) > 3000 else "模拟失败")
        polished = self.results.get(fragment, fragment + "(改)")
        return {"polished": polished, "notes": "改动说明"}


# ---------- 批修核心 ----------

def test_revise_marks_groups_and_merges_directive():
    from app.engines.marks import revise_marks

    db, p = _make_db((1, TEXT1), (2, TEXT2))
    paras1 = TEXT1.split("\n")
    paras2 = TEXT2.split("\n")
    _add_mark(db, p.id, 2, 1, paras2[1], note="这个动作太重复")
    _add_mark(db, p.id, 1, 1, paras1[1], note="意象陈旧")
    _add_mark(db, p.id, 1, 2, paras1[2])  # 无意见的标记
    fake = _FakePolish()

    # revise_marks 惰性导入 polish_fragment,补丁打在来源模块上
    with patch("app.engines.polish.polish_fragment", fake):
        result = asyncio.run(revise_marks(db, p.id, "把铁锈玫瑰相关描写全部替换成全新意象"))

    assert result["total"] == 3 and result["stale"] == 0
    assert [c["chapter_number"] for c in result["chapters"]] == [1, 2]  # 跨章有序
    ch1, ch2 = result["chapters"]
    assert len(ch1["pairs"]) == 2 and len(ch2["pairs"]) == 1
    # 总描述在前、该处意见在后,合并成 direction 下发
    assert fake.calls[0][1] == "把铁锈玫瑰相关描写全部替换成全新意象\n意象陈旧"
    assert fake.calls[1][1].startswith("把铁锈玫瑰相关描写全部替换成全新意象")
    assert fake.calls[1][1] == "把铁锈玫瑰相关描写全部替换成全新意象"  # 无意见不带空行
    # new 回填、old = 段落原文,标记未被销账(job 不落库)
    assert ch1["pairs"][0]["new"] == paras1[1] + "(改)"
    assert ch1["pairs"][0]["old"] == paras1[1]
    from app.db.models import ChapterMark

    assert db.query(ChapterMark).filter(ChapterMark.status == "open").count() == 3
    db.close()


def test_revise_marks_skips_stale_snapshot():
    from app.engines.marks import revise_marks

    db, p = _make_db((1, TEXT1))
    _add_mark(db, p.id, 1, 1, "这段正文早就被改掉了", note="x")
    fake = _FakePolish()
    with patch("app.engines.polish.polish_fragment", fake):
        result = asyncio.run(revise_marks(db, p.id, "统一换掉重复描写"))
    assert result["stale"] == 1 and result["total"] == 1
    pair = result["chapters"][0]["pairs"][0]
    assert pair["ok"] is False and "对不上" in pair["notes"]
    assert fake.calls == []  # 失效条目不调 LLM
    db.close()


def test_revise_marks_single_failure_does_not_kill_batch():
    from app.engines.marks import revise_marks

    db, p = _make_db((1, TEXT1))
    paras = TEXT1.split("\n")
    _add_mark(db, p.id, 1, 0, paras[0])
    _add_mark(db, p.id, 1, 1, paras[1])
    fake = _FakePolish(fail={paras[0]})
    with patch("app.engines.polish.polish_fragment", fake):
        result = asyncio.run(revise_marks(db, p.id, "去 AI 味"))
    pairs = result["chapters"][0]["pairs"]
    assert [pp["ok"] for pp in pairs] == [False, True]
    assert "模拟失败" in pairs[0]["notes"]
    db.close()


def test_revise_marks_empty_and_missing_chapter_body():
    from app.engines.marks import revise_marks

    db, p = _make_db()
    assert asyncio.run(revise_marks(db, p.id, "x")) == {"total": 0, "stale": 0, "chapters": []}
    # 标记指向的章没有正文(被清空)→ 整体不进结果
    from app.db.models import Chapter

    db.add(Chapter(project_id=p.id, outline_id=9, chapter_number=2,
                   final_content="", word_count=0, status="empty"))
    db.commit()
    _add_mark(db, p.id, 2, 0, "正文没了")
    result = asyncio.run(revise_marks(db, p.id, "x"))
    assert result["chapters"] == [] and result["total"] == 0
    db.close()


# ---------- 接口层 ----------

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _auth(client, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_project(client, headers, title: str) -> int:
    return client.post("/api/projects", json={"title": title}, headers=headers).json()["id"]


def _mk_chapter(pid: int, n: int, text: str) -> None:
    """直接往测试库塞一章正文(TestClient 与 pytest 共用同一 DATABASE_URL)。"""
    from app.db.session import SessionLocal
    from app.db.models import Chapter

    s = SessionLocal()
    try:
        s.add(Chapter(project_id=pid, outline_id=n, chapter_number=n,
                      final_content=text, word_count=len(text), status="approved"))
        s.commit()
    finally:
        s.close()


def test_mark_crud_roundtrip(client):
    headers = _auth(client, "marks_crud_user")
    pid = _mk_project(client, headers, "标记 CRUD 书")
    _mk_chapter(pid, 1, TEXT1)

    para = TEXT1.split("\n")[1]
    # 无正文章 404
    r = client.post(f"/api/projects/{pid}/marks", headers=headers,
                    json={"chapter_number": 5, "para_idx": 0, "snapshot": "x", "note": ""})
    assert r.status_code == 404
    # 记一条
    r = client.post(f"/api/projects/{pid}/marks", headers=headers,
                    json={"chapter_number": 1, "para_idx": 1, "snapshot": para, "note": "意象陈旧"})
    assert r.status_code == 200
    mark = r.json()
    assert mark["chapter_number"] == 1 and mark["note"] == "意象陈旧"
    # 同段同快照再记 → 幂等改意见,不堆重复行
    r = client.post(f"/api/projects/{pid}/marks", headers=headers,
                    json={"chapter_number": 1, "para_idx": 1, "snapshot": para, "note": "换掉"})
    assert r.json()["id"] == mark["id"] and r.json()["note"] == "换掉"
    # 清单
    r = client.get(f"/api/projects/{pid}/marks", headers=headers)
    assert [m["id"] for m in r.json()] == [mark["id"]]
    # 删除(销账)+ 重复删 404
    assert client.delete(f"/api/projects/{pid}/marks/{mark['id']}", headers=headers).json() == {"ok": True}
    assert client.delete(f"/api/projects/{pid}/marks/{mark['id']}", headers=headers).status_code == 404
    assert client.get(f"/api/projects/{pid}/marks", headers=headers).json() == []


def test_marks_revise_async_400_without_marks(client):
    headers = _auth(client, "marks_revise_400")
    pid = _mk_project(client, headers, "批修空标记")
    r = client.post(f"/api/projects/{pid}/marks/revise-async", headers=headers,
                    json={"directive": "统一换掉重复描写"})
    assert r.status_code == 400
