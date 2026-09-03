# tests/test_motifs.py
# -*- coding: utf-8 -*-
"""桥段台账 + 雷区清单(跨章重复描写治理)单测(纯算术 + mock LLM,无需 API key)。

覆盖:
- 台账落库:单章内同标签去重、标签归一(去空白)、脏标签(<2 字)丢弃、
  重抽幂等(purge 本章旧账再插,重写不累积)
- ledger 聚合:按标签合并章号与次数,次数降序;upto 排除当前章
- 注入块:出现 1 次不列;2 次「已显重复」;3 次「已写滥」;已禁标签不再列;
  空台账/无雷区 → 空串(开篇零影响)
- 雷区:登记幂等(归一后同标签)、撤销、台账标签一键升格(说明沿用台账)
- 事后软报:雷区命中 major、台账 ≥2 次命中 minor(次数口径 = 此前章数 + 1)、
  幂等重建(source="repeat",正文改后旧告警消失)
- 全书扫描:多章批喂、逐章落账、单批 LLM 失败跳过不拖垮整次扫描
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest


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

    project = Project(title="桥段测试书", target_chapters=20, target_words_per_chapter=3000)
    db.add(project)
    db.flush()
    for n, text in chapters:
        db.add(Chapter(
            project_id=project.id, outline_id=n, chapter_number=n,
            final_content=text, word_count=len(text), status="approved",
        ))
    db.commit()
    return db, project


CH1 = "他摩挲着那朵铁锈玫瑰,铁片边缘硌得指腹生疼。"
CH2 = "她又想起那朵铁锈玫瑰,别在襟前,凉得像一块废铁。"


# ---------- 台账落库与聚合 ----------

def test_apply_extraction_dedupes_and_normalizes():
    from app.engines.consistency import motifs

    db, p = _make_db((1, CH1), (2, CH2))
    # 同章内「铁锈 玫瑰」与「铁锈玫瑰」归一后同标签只留一条;<2 字脏标签丢弃
    n = motifs.apply_extraction(db, p.id, 1, [
        {"label": "铁锈 玫瑰", "detail": "锈色铁片玫瑰"},
        {"label": "铁锈玫瑰", "detail": "重复的"},
        {"label": "扎", "detail": "太短丢弃"},
    ])
    db.commit()
    assert n == 1
    assert motifs.apply_extraction(db, p.id, 2, [{"label": "铁锈玫瑰", "detail": ""}]) == 1
    db.commit()
    led = motifs.ledger(db, p.id)
    assert len(led) == 1
    assert led[0]["label"] == "铁锈玫瑰"
    assert led[0]["chapters"] == [1, 2]
    assert led[0]["count"] == 2
    # detail 取最早一条有值的
    assert led[0]["detail"] == "锈色铁片玫瑰"
    db.close()


def test_apply_extraction_idempotent_on_rewrite():
    """重写第 1 章 → 重抽:先清本章旧账再插,不与新标签叠加累积。"""
    from app.engines.consistency import motifs

    db, p = _make_db((1, CH1))
    motifs.apply_extraction(db, p.id, 1, [
        {"label": "铁锈玫瑰", "detail": "旧"},
        {"label": "扎手", "detail": "旧动作"},
    ])
    db.commit()
    n = motifs.apply_extraction(db, p.id, 1, [{"label": "铁锈玫瑰", "detail": "新"}])
    db.commit()
    assert n == 1
    led = {it["label"]: it for it in motifs.ledger(db, p.id)}
    assert "扎手" not in led  # 本章旧账已清
    assert led["铁锈玫瑰"]["detail"] == "新"
    db.close()


def test_ledger_upto_excludes_current_chapter():
    from app.engines.consistency import motifs

    db, p = _make_db()
    motifs.apply_extraction(db, p.id, 3, [{"label": "躺下等天亮", "detail": ""}])
    motifs.apply_extraction(db, p.id, 5, [{"label": "躺下等天亮", "detail": ""}])
    db.commit()
    # 生成第 5 章时只看 5 之前的账
    led = motifs.ledger(db, p.id, upto=5)
    assert led[0]["chapters"] == [3]
    db.close()


# ---------- 注入块 ----------

def test_ledger_avoid_block_thresholds_and_banned_exclusion():
    from app.engines.consistency import motifs

    db, p = _make_db()
    motifs.apply_extraction(db, p.id, 1, [{"label": "铁锈玫瑰", "detail": "锈玫瑰"}])
    motifs.apply_extraction(db, p.id, 3, [{"label": "躺下等天亮", "detail": "章末收束"}])
    motifs.apply_extraction(db, p.id, 4, [{"label": "躺下等天亮", "detail": ""}])
    motifs.apply_extraction(db, p.id, 5, [{"label": "躺下等天亮", "detail": ""}])
    db.commit()

    # 只出现 1 次 → 不列;空字符串省 token
    assert motifs.ledger_avoid_block(db, p.id, 2) == ""
    assert motifs.ledger_avoid_block(db, p.id, 4) == ""  # upto=4 只算第 3 章,1 次
    # 2 次 → 已显重复;3 次 → 已写滥(upto 不含当前章)
    block2 = motifs.ledger_avoid_block(db, p.id, 5)
    assert "铁锈玫瑰" not in block2
    assert "躺下等天亮" in block2 and "已显重复" in block2
    block3 = motifs.ledger_avoid_block(db, p.id, 6)
    assert "已写滥" in block3 and "第3章" in block3 and "第5章" in block3

    # 雷区里的标签不再进台账块(更强的禁令块已覆盖)
    motifs.add_banned(db, p.id, "躺下等天亮")
    db.commit()
    assert "躺下等天亮" not in motifs.ledger_avoid_block(db, p.id, 6)
    db.close()


def test_banned_block_empty_when_no_bans():
    from app.engines.consistency import motifs

    db, p = _make_db()
    assert motifs.banned_block(db, p.id) == ""
    db.close()


# ---------- 雷区 ----------

def test_banned_upsert_remove_and_promote():
    from app.engines.consistency import motifs

    db, p = _make_db()
    motifs.apply_extraction(db, p.id, 2, [{"label": "扎胸膛", "detail": "自残式扎胸"}])
    motifs.apply_extraction(db, p.id, 5, [{"label": "躺下等天亮", "detail": "章末收束套路"}])
    db.commit()

    m = motifs.add_banned(db, p.id, "扎胸膛", "作者批注:写烦了")
    db.commit()
    # 归一后同标签幂等,只更新说明
    again = motifs.add_banned(db, p.id, " 扎 胸 膛 ")
    db.commit()
    assert again.id == m.id
    assert again.detail == "作者批注:写烦了"
    block = motifs.banned_block(db, p.id)
    assert "扎胸膛" in block and "写烦了" in block and "不得再出现" in block

    # 升格台账标签:沿用台账 detail
    promoted = motifs.promote_to_banned(db, p.id, "躺下等天亮")
    assert promoted is not None and promoted.detail == "章末收束套路"

    # 撤销雷区:台账历史不受影响
    assert motifs.remove_banned(db, p.id, m.id)
    db.commit()
    assert "扎胸膛" not in motifs.banned_block(db, p.id)
    assert any(it["label"] == "扎胸膛" for it in motifs.ledger(db, p.id))
    # 短标签拒绝
    with pytest.raises(ValueError):
        motifs.add_banned(db, p.id, "扎")
    db.close()


def test_promote_missing_label_returns_none():
    from app.engines.consistency import motifs

    db, p = _make_db()
    assert motifs.promote_to_banned(db, p.id, "不存在的标签") is None
    db.close()


# ---------- 事后软报 ----------

def test_check_motif_repeats_severities_and_idempotent_persist():
    from app.db.models import Chapter, ChapterIssue
    from app.engines.consistency import motifs

    db, p = _make_db((1, CH1), (2, CH2))
    motifs.apply_extraction(db, p.id, 1, [{"label": "铁锈玫瑰", "detail": ""}])
    motifs.apply_extraction(db, p.id, 2, [{"label": "铁锈玫瑰", "detail": ""}])
    motifs.add_banned(db, p.id, "扎胸膛")
    db.commit()

    ch3 = Chapter(
        project_id=p.id, outline_id=3, chapter_number=3,
        final_content="他握着铁锈玫瑰,忽然想扎胸膛。", word_count=15, status="approved",
    )
    db.add(ch3)
    db.commit()

    issues = motifs.check_motif_repeats(db, p.id, 3, ch3.final_content)
    by_sev = {i["severity"]: i for i in issues}
    assert set(by_sev) == {"major", "minor"}
    assert "扎胸膛" in by_sev["major"]["description"]
    assert "铁锈玫瑰" in by_sev["minor"]["description"] and "第3次" in by_sev["minor"]["description"]
    # 证据取自正文
    assert "铁锈玫瑰" in by_sev["minor"]["evidence"]

    motifs.persist_motif_issues(db, p.id, ch3, ch3.final_content)
    rows = db.query(ChapterIssue).filter(
        ChapterIssue.chapter_id == ch3.id, ChapterIssue.source == "repeat"
    ).all()
    assert len(rows) == 2
    # 幂等重建:改文后重跑,只反映当前正文
    ch3.final_content = "他合上书,出门散步去了。"
    db.commit()
    motifs.persist_motif_issues(db, p.id, ch3, ch3.final_content)
    rows = db.query(ChapterIssue).filter(
        ChapterIssue.chapter_id == ch3.id, ChapterIssue.source == "repeat"
    ).all()
    assert rows == []
    db.close()


def test_check_motif_repeats_ignores_first_occurrence_and_empty():
    from app.engines.consistency import motifs

    db, p = _make_db()
    motifs.apply_extraction(db, p.id, 1, [{"label": "铁锈玫瑰", "detail": ""}])
    db.commit()
    # 台账只 1 次 → 不软报;雷区为空
    assert motifs.check_motif_repeats(db, p.id, 2, "他又摩挲起铁锈玫瑰。") == []
    assert motifs.check_motif_repeats(db, p.id, 2, "") == []
    db.close()


# ---------- 全书扫描 ----------

class _Adapter:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else '{"chapters": []}'


def test_scan_book_motifs_backfills_ledger():
    from app.engines.consistency import motifs

    db, p = _make_db((1, CH1), (2, CH2), (3, CH1))
    payload = json.dumps({"chapters": [
        {"chapter_number": 1, "motifs": [{"label": "铁锈玫瑰", "detail": "锈玫瑰"}]},
        {"chapter_number": 2, "motifs": [{"label": "铁锈玫瑰", "detail": ""}]},
        {"chapter_number": 3, "motifs": []},
    ]}, ensure_ascii=False)
    adapter = _Adapter([payload])
    with patch("app.llm.router.get_adapter_for", return_value=adapter):
        result = asyncio.run(motifs.scan_book_motifs(db, p.id))
    assert result == {"chapters_scanned": 3, "motifs_added": 2}
    led = motifs.ledger(db, p.id)
    assert led[0]["label"] == "铁锈玫瑰" and led[0]["count"] == 2
    # prompt 里带了已有标签与章号语料
    assert "第1章" in adapter.prompts[0]
    db.close()


def test_scan_skips_failed_batch_and_rejects_empty_book():
    from app.engines.consistency import motifs

    db, p = _make_db((1, CH1), (2, CH2), (3, CH1))

    class _Boom:
        async def ask(self, prompt: str, system=None) -> str:
            raise RuntimeError("网络炸了")

    # 单批失败跳过(不抛、不清旧账),整次扫描正常收尾
    with patch("app.llm.router.get_adapter_for", return_value=_Boom()):
        result = asyncio.run(motifs.scan_book_motifs(db, p.id))
    assert result == {"chapters_scanned": 0, "motifs_added": 0}

    # 空书:直接零结果,不调 LLM
    db2, p2 = _make_db()
    result2 = asyncio.run(motifs.scan_book_motifs(db2, p2.id))
    assert result2 == {"chapters_scanned": 0, "motifs_added": 0}
    db.close()
    db2.close()


# ---------- 抽取集成:EXTRACTION_PROMPT 带台账上下文 ----------

def test_extraction_prompt_formats_with_known_motifs():
    from app.prompts.consistency import EXTRACTION_PROMPT

    text = EXTRACTION_PROMPT.format(
        known_entities="(暂无)", active_facts="(无)",
        open_foreshadowings="(暂无)", known_motifs="铁锈玫瑰、扎胸膛",
        chapter_number=3, chapter_text="正文",
    )
    assert "铁锈玫瑰、扎胸膛" in text
    assert "motifs" in text and "躺下去等天亮" in text  # JSON 契约与示例都在


def test_extract_and_apply_writes_motifs():
    """章后抽取端到端(mock LLM):motifs 与圣经同事务落台账,幂等重抽不累积。"""
    from unittest.mock import patch as _patch

    from app.db.models import WritingMotif
    from app.engines.consistency.extractor import extract_and_apply

    db, p = _make_db((1, CH1))
    reply = json.dumps({
        "new_entities": [], "fact_changes": [], "foreshadow_ops": [],
        "knowledge_updates": [],
        "motifs": [{"label": "铁锈玫瑰", "detail": "锈色铁片玫瑰"}],
        "canon_suggestions": [],
    }, ensure_ascii=False)

    class _A:
        async def ask(self, prompt: str, system=None) -> str:
            return reply

    # extractor 在模块顶层绑定了 get_adapter_for,补丁要打在它的命名空间上
    with _patch("app.engines.consistency.extractor.get_adapter_for", return_value=_A()):
        stats = asyncio.run(extract_and_apply(db, p.id, 1, CH1))
    assert stats["motifs"] == 1
    rows = db.query(WritingMotif).filter(
        WritingMotif.project_id == p.id, WritingMotif.banned.is_(False)
    ).all()
    assert len(rows) == 1 and rows[0].label == "铁锈玫瑰"

    # 重抽(重写场景):幂等,不累积
    with _patch("app.engines.consistency.extractor.get_adapter_for", return_value=_A()):
        asyncio.run(extract_and_apply(db, p.id, 1, CH1))
    rows = db.query(WritingMotif).filter(
        WritingMotif.project_id == p.id, WritingMotif.banned.is_(False)
    ).all()
    assert len(rows) == 1
    db.close()
