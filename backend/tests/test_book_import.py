# tests/test_book_import.py
# -*- coding: utf-8 -*-
"""整本旧书导入(TXT/DOCX):文本解码、卷/章解析、兜底切章、docx 抽取、API 上传。"""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.engines.book_import import (
    decode_text,
    extract_docx_text,
    import_book_to_project,
    parse_chapters,
)
from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_session(client):
    """依赖 client fixture 以触发 lifespan 建表;用完即关。"""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------- 解码 ----------

def test_decode_utf8_with_bom():
    assert decode_text("第一章 测试\n正文".encode("utf-8-sig")).startswith("第一章")


def test_decode_gbk():
    assert decode_text("第十二章 灰塔之下".encode("gb18030")) == "第十二章 灰塔之下"


def test_decode_utf16():
    assert decode_text("序章\n开始".encode("utf-16")).startswith("序章")


# ---------- 章/卷解析 ----------

def test_parse_volumes_and_chapters():
    text = """第一卷 长夜

第一章 灰塔之下
雨水沿着灰塔的铜檐坠落。

第二章 旧怨
林砚没有抬头。

第二卷 破晓

第三章 选拔
封泉台前人山人海。"""
    chapters = parse_chapters(text)
    titles = [c["title"] for c in chapters]
    assert len(chapters) == 3
    assert titles[0] == "第一卷 长夜 · 第一章 灰塔之下"
    assert titles[2] == "第二卷 长夜 破晓 · 第三章 选拔" or titles[2].startswith("第二卷")
    assert "雨水沿着灰塔的铜檐坠落" in chapters[0]["body"]
    assert "封泉台前人山人海" in chapters[2]["body"]


def test_parse_special_chapters():
    text = """序章
一切开始之前。

第一章 启程
出发。

后记
写完啦。"""
    chapters = parse_chapters(text)
    titles = [c["title"] for c in chapters]
    assert titles == ["序章", "第一章 启程", "后记"]


def test_parse_fallback_by_length():
    body = "\n\n".join("这是第%d段普通段落,没有任何章节标题标记。" % i + "字" * 60 for i in range(300))
    chapters = parse_chapters(body)
    assert len(chapters) >= 3
    assert all(c["title"].startswith("第") and c["title"].endswith("章") for c in chapters)
    # 兜底按 ~4000 字/章切;末章是余量,允许偏短
    assert all(len(c["body"]) >= 3000 for c in chapters[:-1])
    assert chapters[-1]["body"]


def test_parse_preserves_inner_blank_lines():
    text = "第一章 试炼\n\n开头一段。\n\n\n结尾一段。"
    chapters = parse_chapters(text)
    assert len(chapters) == 1
    assert "\n\n" in chapters[0]["body"]


# ---------- DOCX 抽取 ----------

def _make_docx(paragraphs: list[str]) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_extract_docx_text():
    raw = _make_docx(["第一章 开始", "正文第一段。", "", "正文第二段。"])
    text = extract_docx_text(raw)
    assert "第一章 开始" in text
    assert "正文第二段。" in text


def test_extract_docx_invalid():
    with pytest.raises(ValueError):
        extract_docx_text(b"not a zip")


# ---------- 落库 ----------

def test_import_book_to_project(db_session):
    from app.db.models import Chapter, Outline, Project, User

    user = User(username="bi_user", password_hash="x")
    db_session.add(user)
    db_session.commit()

    text = "第一章 起步\n他推开门。\n\n第二章 深入\n路还很长。"
    project = import_book_to_project(db_session, user.id, "我的老书.txt", text)
    db_session.commit()

    assert project.title == "我的老书"
    assert project.target_chapters == 2
    chapters = (
        db_session.query(Chapter)
        .filter(Chapter.project_id == project.id)
        .order_by(Chapter.chapter_number)
        .all()
    )
    assert [c.chapter_number for c in chapters] == [1, 2]
    assert all(c.status == "approved" for c in chapters)
    assert chapters[0].final_content.startswith("他推开门。")
    assert chapters[0].word_count == len(chapters[0].final_content)
    outlines = db_session.query(Outline).filter(Outline.project_id == project.id).all()
    assert [o.title for o in outlines] == ["第一章 起步", "第二章 深入"]
    assert outlines[0].summary.startswith("他推开门")


# ---------- API ----------

def _register(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_api_import_book_txt(client):
    headers = _register(client, "bi_api")
    content = "第一章 山门\n拜入山门。\n\n第二章 下山\n下山历练。".encode("gb18030")
    r = client.post(
        "/api/projects/import-book",
        headers=headers,
        files={"file": ("老书.txt", content, "text/plain")},
        data={"title": "我的旧作"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "我的旧作"
    assert body["chapters"] == 2

    # 书架可见,章节数正确
    r2 = client.get("/api/projects", headers=headers)
    titles = [p["title"] for p in r2.json()]
    assert "我的旧作" in titles


def test_api_import_book_rejects_bad_type_and_empty(client):
    headers = _register(client, "bi_api_bad")
    r = client.post(
        "/api/projects/import-book",
        headers=headers,
        files={"file": ("书.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 400

    r2 = client.post(
        "/api/projects/import-book",
        headers=headers,
        files={"file": ("空.txt", b"", "text/plain")},
    )
    assert r2.status_code == 400
