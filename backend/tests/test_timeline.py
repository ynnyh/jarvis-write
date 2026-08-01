# tests/test_timeline.py
# -*- coding: utf-8 -*-
"""全书剧情时间线测试(mock LLM,无需 API key)。

验证点(docs/08 §7 P2-⑨ 轻量落地):
- book_timeline:只收有效契约(提取 ok + 指纹对应当前正文);无契约/失败/
  指纹失效的章自然断档;按章号升序
- timeline_block:upto 过滤(不含本章)、最近 N 条截断、空占位文案、跳跃提示渲染
- prompt 注入:门禁检查(check_chapter)与写前审核(preflight_chapter)的
  prompt 带全书时间线块
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

CONTRACT = {
    "in_story_time": "第三日 深夜",
    "location": "破庙内",
    "scene_continues": False,
    "characters": [
        {
            "name": "沈墨", "location": "破庙内", "physical": None,
            "emotional": "疲惫", "doing": "刚入睡", "knows": [],
            "unresolved_intent": None,
        }
    ],
    "open_threads": [],
    "time_jump_hint": "next_morning",
}
CONTRACT_JSON = json.dumps(CONTRACT, ensure_ascii=False)

CH1_TEXT = "夜深了,沈墨在破庙里睡去。" * 20
CH2_TEXT = "沈墨在破庙里醒来,看着篝火发呆。" * 20


def _make_db(chapters: int = 2, stale_ch2: bool = False, ch3_no_contract: bool = True):
    """独立内存库:一个项目 + N 章正文;第 1 章有效契约,第 2 章(可选)指纹失效契约。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, ChapterState, Outline, Project
    from app.engines.editorial import content_hash

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(title="时间线测试书", target_chapters=chapters,
                      target_words_per_chapter=3000)
    db.add(project)
    db.flush()
    for n in range(1, chapters + 1):
        db.add(Outline(
            project_id=project.id, chapter_number=n, title=f"第{n}章",
            chapter_purpose="推进主线", summary=f"第{n}章剧情", current_version=1,
        ))
        text = CH1_TEXT if n == 1 else CH2_TEXT
        ch = Chapter(
            project_id=project.id, outline_id=n, chapter_number=n,
            final_content=text + str(n), word_count=len(text), status="approved",
        )
        db.add(ch)
        db.flush()
        if n == 1:
            db.add(ChapterState(
                chapter_id=ch.id, contract=CONTRACT_JSON,
                content_hash=content_hash(text + str(n)), extract_status="ok",
            ))
        elif n == 2 and stale_ch2:
            # 指纹对不上当前正文:契约失效,不应进时间线
            db.add(ChapterState(
                chapter_id=ch.id, contract=CONTRACT_JSON,
                content_hash="stale-hash-0000", extract_status="ok",
            ))
        elif n == 2:
            db.add(ChapterState(
                chapter_id=ch.id, contract=CONTRACT_JSON,
                content_hash=content_hash(text + str(n)), extract_status="ok",
            ))
        # 第 3 章起默认无契约(老书断档)
    db.commit()
    return db, project


class _Adapter:
    """固定返回一条回复的假 LLM,记录全部 prompt。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply


# ---------- book_timeline:只收有效契约 ----------
def test_book_timeline_collects_fresh_contracts_only():
    from app.engines.timeline import book_timeline

    db, project = _make_db(chapters=3, stale_ch2=True)
    items = book_timeline(db, project.id)
    # ch1 有效;ch2 指纹失效跳过;ch3 无契约跳过
    assert [i["chapter"] for i in items] == [1]
    assert items[0]["in_story_time"] == "第三日 深夜"
    assert items[0]["location"] == "破庙内"
    assert items[0]["time_jump_hint"] == "next_morning"


# ---------- timeline_block:upto 过滤 / 截断 / 占位 ----------
def test_timeline_block_upto_excludes_current_and_empty_placeholder():
    from app.engines.timeline import timeline_block

    db, project = _make_db()
    # 第 2 章视角:只见第 1 章
    block = timeline_block(db, project.id, upto=2)
    assert "第1章末:第三日 深夜 @ 破庙内" in block
    assert "(下章跳跃:next_morning)" in block
    # 第 1 章视角:前面什么都没有 → 占位文案
    assert timeline_block(db, project.id, upto=1).startswith("(无全书时间线")


def test_timeline_block_truncates_to_recent_entries():
    from app.engines.timeline import _PROMPT_MAX_ENTRIES, timeline_block

    db, project = _make_db(chapters=20)
    # 给 2-20 章都补有效契约(内容同上,指纹按各章正文算)
    from app.db.models import Chapter, ChapterState
    from app.engines.editorial import content_hash

    for ch in db.query(Chapter).filter(Chapter.chapter_number > 2):
        db.add(ChapterState(
            chapter_id=ch.id, contract=CONTRACT_JSON,
            content_hash=content_hash(ch.final_content), extract_status="ok",
        ))
    db.commit()

    block = timeline_block(db, project.id, upto=99)
    lines = [ln for ln in block.splitlines() if ln.startswith("第")]
    assert len(lines) == _PROMPT_MAX_ENTRIES
    assert "第20章末" in block  # 最近的保留
    assert "第5章末" not in block  # 超出窗口的被截掉


# ---------- prompt 注入:门禁 + 写前审核 ----------
def test_check_chapter_prompt_includes_timeline():
    from app.engines.consistency import checker as checker_mod

    db, project = _make_db()
    adapter = _Adapter('{"issues": []}')
    with patch.object(checker_mod, "get_adapter_for", return_value=adapter):
        asyncio.run(checker_mod.check_chapter(db, project.id, 2, CH2_TEXT))

    assert len(adapter.prompts) == 1
    prompt = adapter.prompts[0]
    assert "全书剧情时间线" in prompt
    assert "第1章末:第三日 深夜" in prompt


def test_preflight_prompt_includes_timeline():
    from app.db.models import Outline
    from app.engines.consistency import preflight as preflight_mod

    db, project = _make_db()
    outline = db.query(Outline).filter(Outline.chapter_number == 2).first()
    adapter = _Adapter('{"warnings": []}')
    with patch.object(preflight_mod, "get_adapter_for", return_value=adapter):
        asyncio.run(preflight_mod.preflight_chapter(db, project.id, 2, outline))

    assert len(adapter.prompts) == 1
    assert "全书剧情时间线" in adapter.prompts[0]
    assert "第1章末:第三日 深夜" in adapter.prompts[0]
