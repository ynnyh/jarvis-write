# tests/test_word_guard_split.py
# -*- coding: utf-8 -*-
"""拆章事务原子性回归(mock LLM,无需 API key)。

背景:拆章发生在 generate_chapter 落库之前。_split_chapter 内部会调
extract_and_apply / 摘要重建,而 extract_and_apply 入口即 commit(并发纪律硬约束)。
若结构改动(章号顺移 + 新建 N+1=后半)与「第 N 章正文改成 part_a」不在同一事务里
先提交,那次入口 commit 会把「第 N 章=全文 + 第 N+1 章=后半」的重复态刷上磁盘,
且此重复态横跨随后数分钟的 LLM 调用 —— 中途崩溃则正文重复/章号错乱,书就烂了。

本测试锁住修复后的行为:_split_chapter 返回时(以及圣经/摘要 LLM 调用发生的那一刻),
第 N 章正文必须已经是 part_a,而不是原始全文。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch


# 断点索引:段落列表里第 2 段之后切开(idx=1 → part_a=[0,1], part_b=[2,3,4,5])
_SPLIT_JSON = (
    '{"split_paragraph_index": 1,'
    ' "chapter_a_title": "上半章",'
    ' "chapter_a_summary": "前半剧情",'
    ' "chapter_b_title": "下半章",'
    ' "chapter_b_summary": "后半剧情",'
    ' "foreshadowing_goes_to": "a",'
    ' "reason": "篇幅过长"}'
)


def _build_full_text() -> str:
    # 6 段,每段足够长,保证拆出的两半都过 _MIN_SPLIT_HALF_RATIO 门槛
    paras = [f"这是第{i}段正文。" + "内容" * 200 for i in range(6)]
    return "\n".join(paras)


async def _split_case() -> None:
    from app.db.base import Base
    import app.db.models  # noqa: F401 — 注册全部表
    from app.db.models import Chapter, Outline, Project
    from app.db.session import SessionLocal, engine
    from app.engines.pipeline import word_guard as wg
    from app.engines.consistency import extractor as extractor_mod

    Base.metadata.create_all(engine)

    full_text = _build_full_text()
    target = 300  # full_text 远超 target*2 → 触发拆章分支

    setup = SessionLocal()
    proj = Project(
        title="split-test",
        target_chapters=1,
        target_words_per_chapter=target,
        word_guard_enabled=True,
        word_guard_ratio=1.5,
        auto_split_enabled=True,
    )
    setup.add(proj)
    setup.flush()
    pid = proj.id
    outline = Outline(
        project_id=pid, chapter_number=1, title="原章", summary="原摘要",
        content_hash="", current_version=1,
    )
    setup.add(outline)
    setup.flush()
    # 第 1 章正文此刻是全文(拆章在落库前跑,generate_chapter 尚未写 part_a)
    ch = Chapter(
        project_id=pid, outline_id=outline.id, chapter_number=1,
        draft_content=full_text, final_content=full_text,
        word_count=len(full_text), status="finalized",
    )
    setup.add(ch)
    setup.commit()
    outline_id = outline.id
    setup.close()

    # LLM 调用期间的观测:每次 SUMMARY/FACT_EXTRACT 调用发生时,
    # 用独立连接读第 1 章正文,确认已是 part_a(不再是重复态全文)。
    seen: dict = {"during_llm_ch1_is_full": False, "llm_calls": 0}
    part_a_len = None  # 拆章后 part_a 的期望长度,断点 idx=1 → 前两段

    from sqlalchemy import text

    class _SplitAdapter:
        """断点查询返回拆章 JSON;抽取返回空;摘要返回定长——每次都探测磁盘态。"""

        async def ask(self, prompt: str, system: str | None = None) -> str:
            # 断点查询(拆章前,尚未落库,不探测)
            if "split_paragraph_index" in prompt or "断点" in prompt:
                return _SPLIT_JSON
            # 其余 LLM 调用(圣经抽取 / 摘要重建)都在结构落库之后:探测第 1 章正文
            seen["llm_calls"] += 1
            with engine.connect() as c:
                row = c.execute(
                    text("SELECT final_content FROM chapters WHERE project_id=:p AND chapter_number=1"),
                    {"p": pid},
                ).first()
            if row and row[0] == full_text:
                seen["during_llm_ch1_is_full"] = True
            if "抽取" in prompt and "事实" in prompt:
                return "{}"
            return "本章摘要。"

    sa = SessionLocal()
    proj2 = sa.get(Project, pid)
    outline2 = sa.get(Outline, outline_id)
    with patch.object(wg, "get_adapter_for", return_value=_SplitAdapter()), \
         patch.object(extractor_mod, "get_adapter_for", return_value=_SplitAdapter()):
        result = await wg.word_count_guard(
            sa, proj2, 1, outline2, full_text, style_block="", report=None
        )
    sa.close()

    # 拆章确实发生
    assert result.action == "split", f"应触发拆章,实际 action={result.action}"

    # 期望的 part_a:与生产同口径(按 \n 去空行 split → 前两段用 \n join)
    paragraphs = [p for p in full_text.split("\n") if p.strip()]
    expected_part_a = "\n".join(paragraphs[:2])

    # 落库后校验:第 1 章=part_a,第 2 章=part_b,总章数 +1
    check = SessionLocal()
    ch1 = (
        check.query(Chapter)
        .filter(Chapter.project_id == pid, Chapter.chapter_number == 1)
        .first()
    )
    ch2 = (
        check.query(Chapter)
        .filter(Chapter.project_id == pid, Chapter.chapter_number == 2)
        .first()
    )
    proj_after = check.get(Project, pid)
    assert ch1 is not None and ch2 is not None, "拆章后应有第 1、2 两章"
    assert ch1.final_content == expected_part_a, "第 1 章正文应为 part_a(前半),不应仍是全文"
    assert ch1.final_content != full_text, "第 1 章不应还是拆前全文"
    assert ch2.final_content == "\n".join(paragraphs[2:]), "第 2 章正文应为 part_b(后半)"
    assert proj_after.target_chapters == 2, "总章数应 +1"
    check.close()

    # 核心:任何圣经/摘要 LLM 调用发生时,磁盘上第 1 章都不该还是重复态全文。
    assert seen["llm_calls"] > 0, "应发生过圣经/摘要 LLM 调用"
    assert not seen["during_llm_ch1_is_full"], (
        "LLM 收尾调用期间,磁盘上第 1 章正文仍是拆前全文 —— 说明结构落库时正文没同步改成 "
        "part_a,存在「全文 + 后半」重复态窗口,中途崩溃会损坏正文"
    )


def test_split_chapter_no_duplicate_window():
    asyncio.run(_split_case())
