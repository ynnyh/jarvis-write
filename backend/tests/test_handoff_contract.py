# tests/test_handoff_contract.py
# -*- coding: utf-8 -*-
"""章末交接契约测试(mock LLM,无需 API key)。

验证点(docs/08 §5.2 + P0 第一棒任务):
- validate_contract 结构校验/归一化:坏输入 → None,有效输入 → 规范结构
- 契约提取:LLM 返回合法 JSON → chapter_states 落 ok 行(含正文指纹)
- 失败降级:坏 JSON / LLM 抛异常 → 落 failed 行留痕(error 非空),不抛异常阻塞
- 重写幂等:重复提取先 purge 旧契约,一章永远只有一条当前契约
- 下章注入:有契约 → 草稿 prompt 含契约文本;无契约/指纹不符 → 不注入(回退现状)
- 端到端:generate_chapter 生成第 1 章后,第 2 章草稿 prompt 注入第 1 章契约
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

# docs/08 §5.2 的契约样例(沈墨/破庙)
CONTRACT = {
    "in_story_time": "第三日 深夜",
    "location": "破庙内",
    "scene_continues": False,
    "ambient": "破庙内一片死寂,连虫鸣鸟叫都没有,只有夜风穿过断梁的呜咽",
    "characters": [
        {
            "name": "沈墨",
            "location": "破庙内",
            "physical": "左臂刀伤未愈",
            "emotional": "戒备、疲惫",
            "doing": "刚入睡",
            "knows": ["黑衣人来自听雨楼"],
            "unresolved_intent": "明日动身去渡口",
        }
    ],
    "open_threads": ["庙外脚步声未查明"],
    "time_jump_hint": "next_morning",
}
CONTRACT_JSON = json.dumps(CONTRACT, ensure_ascii=False)

CH1_TEXT = "夜深了,沈墨在破庙里睡去。" * 20


def _make_db(with_ch1: bool = True):
    """独立内存库:一个项目 + 两章大纲 + (可选)第 1 章正文。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, Outline, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    project = Project(title="契约测试书", target_chapters=2, target_words_per_chapter=3000)
    db.add(project)
    db.flush()
    for n, title in ((1, "破庙夜宿"), (2, "渡口清晨")):
        db.add(Outline(
            project_id=project.id, chapter_number=n, title=title,
            chapter_purpose="推进主线", summary=f"第{n}章剧情", current_version=1,
        ))
    db.flush()
    ch1 = None
    if with_ch1:
        ch1 = Chapter(
            project_id=project.id, outline_id=1, chapter_number=1,
            final_content=CH1_TEXT, word_count=len(CH1_TEXT), status="approved",
        )
        db.add(ch1)
    db.commit()
    return db, project, ch1


class _Adapter:
    """固定返回一条回复的假 LLM。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply


class _BoomAdapter:
    """ask 直接抛异常的假 LLM(模拟调用失败)。"""

    async def ask(self, prompt: str, system=None) -> str:
        raise RuntimeError("上游 502")


def _state_row(db, chapter_id: int):
    from app.db.models import ChapterState

    return db.query(ChapterState).filter(ChapterState.chapter_id == chapter_id).first()


# ---------- validate_contract 纯函数 ----------
def test_validate_contract_normalizes():
    from app.engines.pipeline.handoff import validate_contract

    # 合法输入归一化:缺字段补 null,scene_continues 转 bool
    c = validate_contract(CONTRACT)
    assert c["in_story_time"] == "第三日 深夜"
    assert c["location"] == "破庙内"
    assert c["scene_continues"] is False
    assert c["ambient"] == "破庙内一片死寂,连虫鸣鸟叫都没有,只有夜风穿过断梁的呜咽"
    assert c["characters"][0]["doing"] == "刚入睡"
    assert c["time_jump_hint"] == "next_morning"

    # 缺 time_jump_hint → "none";characters 非 list → 空;缺 ambient → None
    c2 = validate_contract({"location": "渡口", "characters": "沈墨"})
    assert c2["time_jump_hint"] == "none"
    assert c2["characters"] == []
    assert c2["ambient"] is None

    # 三项核心全空 → 无价值,视为失败(ambient 只是补充,单有环境不足以成契约)
    assert validate_contract({"characters": []}) is None
    assert validate_contract({}) is None
    assert validate_contract({"characters": [{"name": ""}]}) is None
    assert validate_contract({"ambient": "下着瓢泼大雨"}) is None


# ---------- Phase 2:故事时钟字段归一 + 章末时钟渲染 ----------
def test_validate_contract_normalizes_story_clock_fields():
    from app.engines.pipeline.handoff import validate_contract

    # 合法:int / 字符串数字 都归一成 int
    c = validate_contract(dict(CONTRACT, story_day=3, days_remaining="20"))
    assert c["story_day"] == 3
    assert c["days_remaining"] == 20
    # 脏值 / None → None(不回落 0,让下游区分"无数据"与"第 0 天")
    c2 = validate_contract(dict(CONTRACT, story_day="不知道", days_remaining=None))
    assert c2["story_day"] is None
    assert c2["days_remaining"] is None
    # 负数 → None
    assert validate_contract(dict(CONTRACT, story_day=-2))["story_day"] is None
    # 老契约无这俩键 → None(向后兼容,不误当 0)
    base = {k: v for k, v in CONTRACT.items() if k not in ("story_day", "days_remaining")}
    c4 = validate_contract(base)
    assert c4["story_day"] is None and c4["days_remaining"] is None


def test_format_contract_block_renders_clock_line():
    from app.engines.pipeline.handoff import format_contract_block, validate_contract

    c = validate_contract(dict(CONTRACT, story_day=3, days_remaining=29))
    block = format_contract_block(c, 1)
    assert "章末时钟" in block
    assert "故事第 3 天" in block
    assert "倒计时剩 29 天" in block
    # 无时钟字段 → 不出时钟行(向后兼容,老契约块形态不变)
    base = {k: v for k, v in CONTRACT.items() if k not in ("story_day", "days_remaining")}
    block2 = format_contract_block(validate_contract(base), 1)
    assert "章末时钟" not in block2



# ---------- 提取落库:成功 ----------
def test_extract_success_writes_row():
    from app.engines.editorial import content_hash
    from app.engines.pipeline.handoff import extract_handoff_contract

    db, _project, ch1 = _make_db()
    asyncio.run(extract_handoff_contract(db, ch1, 1, CH1_TEXT, _Adapter(CONTRACT_JSON)))

    row = _state_row(db, ch1.id)
    assert row is not None
    assert row.extract_status == "ok"
    assert row.extract_error == ""
    assert row.content_hash == content_hash(CH1_TEXT)
    saved = json.loads(row.contract)
    assert saved["location"] == "破庙内"
    assert saved["characters"][0]["doing"] == "刚入睡"


# ---------- 提取落库:失败留痕不阻塞 ----------
def test_extract_bad_json_records_failure_not_raise():
    from app.engines.pipeline.handoff import extract_handoff_contract

    db, _project, ch1 = _make_db()
    # 坏 JSON:不抛异常,落 failed 行
    asyncio.run(extract_handoff_contract(db, ch1, 1, CH1_TEXT, _Adapter("这不是JSON")))

    row = _state_row(db, ch1.id)
    assert row is not None
    assert row.extract_status == "failed"
    assert row.extract_error
    assert row.contract == ""


def test_extract_llm_error_records_failure_not_raise():
    from app.engines.pipeline.handoff import extract_handoff_contract

    db, _project, ch1 = _make_db()
    # LLM 调用抛异常:不传播,落 failed 行
    asyncio.run(extract_handoff_contract(db, ch1, 1, CH1_TEXT, _BoomAdapter()))

    row = _state_row(db, ch1.id)
    assert row is not None
    assert row.extract_status == "failed"
    assert "502" in row.extract_error


# ---------- 重写幂等:purge 旧契约 ----------
def test_reextract_purges_old_contract():
    from app.engines.pipeline.handoff import extract_handoff_contract

    db, _project, ch1 = _make_db()
    asyncio.run(extract_handoff_contract(db, ch1, 1, CH1_TEXT, _Adapter(CONTRACT_JSON)))
    # 重写后重新提取(新地点)
    new_contract = dict(CONTRACT, location="渡口", in_story_time="第四日 清晨")
    new_text = "天亮了,沈墨赶往渡口。" * 20
    ch1.final_content = new_text
    db.commit()
    asyncio.run(extract_handoff_contract(
        db, ch1, 1, new_text, _Adapter(json.dumps(new_contract, ensure_ascii=False))
    ))

    from app.db.models import ChapterState

    rows = db.query(ChapterState).filter(ChapterState.chapter_id == ch1.id).all()
    assert len(rows) == 1  # 一章一条当前契约
    saved = json.loads(rows[0].contract)
    assert saved["location"] == "渡口"


# ---------- 下章注入 ----------
def test_load_handoff_block_injects_when_contract_fresh():
    from app.engines.pipeline.handoff import extract_handoff_contract, load_handoff_block

    db, project, ch1 = _make_db()
    asyncio.run(extract_handoff_contract(db, ch1, 1, CH1_TEXT, _Adapter(CONTRACT_JSON)))

    block = load_handoff_block(db, project.id, 2)
    assert "章末交接契约" in block
    assert "第三日 深夜" in block
    assert "破庙内" in block
    assert "刚入睡" in block
    assert "庙外脚步声未查明" in block
    assert "next_morning" in block
    # 环境氛围锚注入 + 反翻转提示(治"上一章无鸟、下一章被鸟叫醒"跨章穿帮)
    assert "连虫鸣鸟叫都没有" in block
    assert "章末环境氛围" in block
    assert "别无缘由地翻转" in block
    # 文案须明确"开头衔接必须与之吻合"
    assert "必须与之吻合" in block


def test_load_handoff_block_falls_back_without_contract():
    from app.engines.pipeline.handoff import load_handoff_block

    # 无契约行(老章节)→ 空串,回退现状
    db, project, _ch1 = _make_db()
    assert load_handoff_block(db, project.id, 2) == ""
    # 第 1 章无上章 → 空串
    assert load_handoff_block(db, project.id, 1) == ""


def test_load_handoff_block_skips_failed_and_stale():
    from app.engines.pipeline.handoff import extract_handoff_contract, load_handoff_block

    # 提取失败的章 → 不注入
    db, project, ch1 = _make_db()
    asyncio.run(extract_handoff_contract(db, ch1, 1, CH1_TEXT, _BoomAdapter()))
    assert load_handoff_block(db, project.id, 2) == ""

    # 提取后正文被手改(指纹不符)→ 契约自动失效,不注入
    db2, project2, ch1b = _make_db()
    asyncio.run(extract_handoff_contract(db2, ch1b, 1, CH1_TEXT, _Adapter(CONTRACT_JSON)))
    ch1b.final_content = "被人工改过的正文。"
    db2.commit()
    assert load_handoff_block(db2, project2.id, 2) == ""


# ---------- 端到端:generate_chapter 提取 + 下章注入 ----------
async def _fake_check(*a, **k):
    return []


async def _fake_extract(*a, **k):
    return {}


async def _fake_proofread(*a, **k):
    return {"issues": []}


async def _fake_preflight(*a, **k):
    """写前审核默认无警告(该引擎的专门用例在 test_review_workflow)。"""
    return []


async def _fake_review(*a, **k):
    return {
        "scores": {"plot": 9, "prose": 9, "pacing": 9, "character": 9},
        "comment": "",
        "suggestions": [],
    }


class _PipelineAdapter:
    """按 prompt 内容回复:草稿/定稿/契约/摘要;记录全部 prompt。"""

    def __init__(self):
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        if "现在开始写" in prompt:
            return "草稿正文。" * 30
        if "修订后的" in prompt:
            return "定稿正文。" * 30
        if "场记" in prompt:
            return CONTRACT_JSON
        return "前情摘要。"


def _gen(db, project, n, adapter):
    from app.engines.pipeline import chapter as ch_mod

    with (
        patch.object(ch_mod, "get_adapter_for", return_value=adapter),
        patch.object(ch_mod, "check_chapter", new=_fake_check),
        patch.object(ch_mod, "extract_and_apply", new=_fake_extract),
        patch.object(ch_mod, "proofread_chapter", new=_fake_proofread),
        patch.object(ch_mod, "review_chapter", new=_fake_review),
        patch.object(ch_mod, "preflight_chapter", new=_fake_preflight),
    ):
        asyncio.run(ch_mod.generate_chapter(db, project, n))


def test_generate_extracts_and_next_chapter_injects():
    db, project, _ch1 = _make_db(with_ch1=False)
    adapter = _PipelineAdapter()

    # 生成第 1 章:契约落库;其草稿 prompt 不含契约(第 1 章无上章)
    _gen(db, project, 1, adapter)
    ch1_draft_prompt = next(p for p in adapter.prompts if "现在开始写" in p)
    assert "章末交接契约" not in ch1_draft_prompt

    from app.db.models import Chapter

    ch1 = db.query(Chapter).filter(
        Chapter.project_id == project.id, Chapter.chapter_number == 1
    ).first()
    row = _state_row(db, ch1.id)
    assert row is not None and row.extract_status == "ok"

    # 生成第 2 章:草稿 prompt 注入第 1 章契约(与 recent_tail 并存)
    adapter2 = _PipelineAdapter()
    _gen(db, project, 2, adapter2)
    ch2_draft_prompt = next(p for p in adapter2.prompts if "现在开始写" in p)
    assert "章末交接契约" in ch2_draft_prompt
    assert "刚入睡" in ch2_draft_prompt
    assert "第三日 深夜" in ch2_draft_prompt
    assert "连虫鸣鸟叫都没有" in ch2_draft_prompt  # 环境氛围锚随契约注入下章
    assert "【最近章节结尾" in ch2_draft_prompt  # recent_tail 依然注入

    # 第 2 章自己也落了契约行(生成闭环)
    ch2 = db.query(Chapter).filter(
        Chapter.project_id == project.id, Chapter.chapter_number == 2
    ).first()
    assert _state_row(db, ch2.id).extract_status == "ok"
