# tests/test_retitle.py
# -*- coding: utf-8 -*-
"""章节标题改名回归(mock LLM,无需 API key)。

锁住两条本次修复的行为:
1. 只改标题是「纯展示性改动」:不标正文失配、不跑 LLM 精判、不需要影响分析。
   —— 修复前 title 在 _PLOT_FIELDS 里,改个名就把已有正文标 stale、还白烧一次 LLM。
2. 对照组:改情节字段(summary)仍照旧走精判 + 标正文失配 —— 别把正常路径改坏。
另测 suggest_chapter_titles 的候选清洗(去重 / 去空 / 剥《》/ 去掉与当前雷同的)。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch


def _make_project_with_written_chapter(title: str = "惊天逆转!命运的终极审判"):
    """建一个「第 1 章已有正文」的项目,返回 (pid, outline_id)。"""
    from app.db.base import Base
    import app.db.models  # noqa: F401 — 注册全部表
    from app.db.models import Chapter, Outline, Project
    from app.db.session import SessionLocal, engine

    Base.metadata.create_all(engine)
    s = SessionLocal()
    try:
        proj = Project(title="retitle-test", target_chapters=1)
        s.add(proj)
        s.flush()
        pid = proj.id
        o = Outline(
            project_id=pid, chapter_number=1, title=title,
            summary="少年在雨夜接到一通电话,决定回乡", foreshadowing="无",
            content_hash="", current_version=1,
        )
        s.add(o)
        s.flush()
        oid = o.id
        s.add(Chapter(
            project_id=pid, outline_id=oid, chapter_number=1,
            draft_content="正文", final_content="第 1 章已有正文", word_count=6,
            status="approved",
        ))
        s.commit()
        return pid, oid
    finally:
        s.close()


class _CountingAdapter:
    """记录被调用次数;可指定 ask() 的返回。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    async def ask(self, prompt: str, system: str | None = None) -> str:
        self.calls += 1
        return self.reply


# ---------------------------------------------------------------------------
# 1. 只改标题 → 不标失配 / 不跑精判 / 不需要影响分析
# ---------------------------------------------------------------------------
async def _cosmetic_title_case() -> None:
    from app.db.models import Chapter, Outline
    from app.db.session import SessionLocal
    from app.engines.cascade import differ as differ_mod
    from app.engines.cascade.differ import apply_outline_edit

    pid, oid = _make_project_with_written_chapter()

    s = SessionLocal()
    outline = s.get(Outline, oid)
    # 精判若被误调用会返回 major —— 既能被计数抓到,也会污染 change_type
    probe = _CountingAdapter('{"change_type": "major", "summary": "不该被调用"}')
    with patch.object(differ_mod, "get_adapter_for", return_value=probe):
        result = await apply_outline_edit(s, outline, {"title": "归乡"})
    s.commit()

    assert probe.calls == 0, "只改标题不应触发 LLM 精判"
    assert result["status"] == "saved"
    assert result["change_type"] == "minor", result
    assert result["changed_fields"] == ["title"], result
    assert result["own_chapter_stale"] is False, "只改标题不应把正文标失配"
    assert result["needs_impact_analysis"] is False, "只改标题不需要影响分析"

    ch = (
        s.query(Chapter)
        .filter(Chapter.project_id == pid, Chapter.chapter_number == 1)
        .first()
    )
    assert ch.is_stale is False and ch.status == "approved", "正文状态不应被改名波及"
    assert outline.title == "归乡"
    assert outline.current_version == 2, "标题改动仍要版本化(可回溯)"
    s.close()


def test_cosmetic_title_edit_no_stale_no_llm():
    asyncio.run(_cosmetic_title_case())


# ---------------------------------------------------------------------------
# 2. 对照组:改 summary(情节字段)仍走精判 + 标正文失配
# ---------------------------------------------------------------------------
async def _plot_field_case() -> None:
    from app.db.models import Chapter, Outline
    from app.db.session import SessionLocal
    from app.engines.cascade import differ as differ_mod
    from app.engines.cascade.differ import apply_outline_edit

    pid, oid = _make_project_with_written_chapter()

    s = SessionLocal()
    outline = s.get(Outline, oid)
    probe = _CountingAdapter('{"change_type": "major", "summary": "改了主角的动机"}')
    with patch.object(differ_mod, "get_adapter_for", return_value=probe):
        result = await apply_outline_edit(
            s, outline, {"summary": "少年在雨夜接到电话,决定留下不回乡"}
        )
    s.commit()

    assert probe.calls == 1, "改情节字段应触发一次 LLM 精判"
    assert result["change_type"] == "major", result
    assert result["own_chapter_stale"] is True, "改情节字段应把已有正文标失配"
    assert result["needs_impact_analysis"] is True, "major 改动应提示做影响分析"

    ch = (
        s.query(Chapter)
        .filter(Chapter.project_id == pid, Chapter.chapter_number == 1)
        .first()
    )
    assert ch.is_stale is True and ch.status == "stale"
    s.close()


def test_plot_field_edit_still_marks_stale_and_classifies():
    asyncio.run(_plot_field_case())


# ---------------------------------------------------------------------------
# 3. suggest_chapter_titles 候选清洗
# ---------------------------------------------------------------------------
async def _suggest_titles_case() -> None:
    from app.engines import outline_retitle as rt_mod
    from app.engines.outline_retitle import suggest_chapter_titles

    # 含:带《》的重复项、纯重复、空串、与当前标题雷同的 —— 都要被清掉
    reply = (
        '{"titles": ["《归乡》", "归乡", "雨夜的电话", "", '
        '"惊天逆转!命运的终极审判", "雨夜的电话", "旧信"]}'
    )
    probe = _CountingAdapter(reply)
    with patch.object(rt_mod, "get_adapter_for", return_value=probe):
        titles = await suggest_chapter_titles(
            chapter_number=1,
            architecture_brief="brief",
            outline_block="block",
            current_title="惊天逆转!命运的终极审判",
        )

    assert probe.calls == 1
    # 《归乡》剥壳后与「归乡」去重;空串丢弃;与当前雷同的丢弃;「雨夜的电话」去重
    assert titles == ["归乡", "雨夜的电话", "旧信"], titles


def test_suggest_titles_cleans_candidates():
    asyncio.run(_suggest_titles_case())


# ---------------------------------------------------------------------------
# 4. suggest_chapter_titles 解析失败 → ValueError(前端转 400 提示重试)
# ---------------------------------------------------------------------------
async def _suggest_titles_bad_json_case() -> None:
    from app.engines import outline_retitle as rt_mod
    from app.engines.outline_retitle import suggest_chapter_titles

    probe = _CountingAdapter("对不起我不会说 JSON")
    raised = False
    with patch.object(rt_mod, "get_adapter_for", return_value=probe):
        try:
            await suggest_chapter_titles(
                chapter_number=1,
                architecture_brief="brief",
                outline_block="block",
                current_title="随便",
            )
        except ValueError:
            raised = True
    assert raised, "解析失败应抛 ValueError"


def test_suggest_titles_bad_json_raises():
    asyncio.run(_suggest_titles_bad_json_case())


# ---------------------------------------------------------------------------
# 5. 容错解析:模型不照约定 {"titles":[...]} 输出时,仍尽力捞出候选(修「改名老失败」)
#    —— 正文/大纲生成不走 JSON,唯独改名要求严格 JSON,是「老是失败」的高发点。
# ---------------------------------------------------------------------------
async def _suggest_titles_tolerant_case() -> None:
    from app.engines import outline_retitle as rt_mod
    from app.engines.outline_retitle import suggest_chapter_titles

    # 每种都是真实模型跑偏形态:修复前会 400 或产出垃圾标题,修复后都要捞出干净候选
    shapes = {
        "裸数组": '["归乡", "旧信", "雨夜来电"]',
        "别名键": '{"候选标题": ["归乡", "旧信", "雨夜来电"]}',
        "键名没照约定": '{"result": ["归乡", "旧信"]}',
        "推理模型思考里混花括号": '我先想格式 {titles:...},给出:\n{"titles": ["归乡", "旧信"]}',
        "markdown 裹裸数组": '```json\n["归乡", "旧信"]\n```',
        "列表项是对象": '{"titles": [{"title": "归乡"}, {"title": "旧信"}]}',
        "全角引号包裹": '{"titles": ["“归乡”", "「旧信」"]}',
    }
    for name, reply in shapes.items():
        probe = _CountingAdapter(reply)
        with patch.object(rt_mod, "get_adapter_for", return_value=probe):
            titles = await suggest_chapter_titles(
                chapter_number=1, architecture_brief="b",
                outline_block="o", current_title="惊天逆转",
            )
        assert titles and all(isinstance(t, str) for t in titles), name
        assert "归乡" in titles and "旧信" in titles, f"{name} -> {titles}"
        # 对象项/全角引号都要清成干净标题,不能残留 {'title' 或 引号
        assert all("{" not in t and "“" not in t and "「" not in t for t in titles), \
            f"{name} 未清洗干净 -> {titles}"


def test_suggest_titles_tolerant_of_offspec_shapes():
    asyncio.run(_suggest_titles_tolerant_case())


# ---------------------------------------------------------------------------
# 6. 真·垃圾输入(无任何数组可捞)仍抛 ValueError → 400,不吞成空成功
# ---------------------------------------------------------------------------
async def _suggest_titles_true_garbage_case() -> None:
    from app.engines import outline_retitle as rt_mod
    from app.engines.outline_retitle import suggest_chapter_titles

    for reply in ("对不起我不会说 JSON", "", "标题就用现在这个挺好的,不用改"):
        probe = _CountingAdapter(reply)
        raised = False
        with patch.object(rt_mod, "get_adapter_for", return_value=probe):
            try:
                await suggest_chapter_titles(
                    chapter_number=1, architecture_brief="b",
                    outline_block="o", current_title="随便",
                )
            except ValueError:
                raised = True
        assert raised, f"无可用候选应抛 ValueError,却没抛;reply={reply!r}"


def test_suggest_titles_true_garbage_still_raises():
    asyncio.run(_suggest_titles_true_garbage_case())
