# tests/test_pipeline.py
# -*- coding: utf-8 -*-
"""生成流水线测试(mock LLM,无需 API key)。

由 scripts/stage1_test.py 改造而来,mock 方式一致:
按调用顺序返回预置回复的 MockAdapter,patch 掉引擎模块里的 get_adapter_for。
数据库用独立内存库,不碰开发库。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch


# ---------- 倾向目录 ----------
def test_catalog():
    from app.engines.tendency import get_catalog, get_node_catalog

    catalog = get_catalog()
    assert set(catalog) == {"outline", "chapter", "polish"}
    outline_dims = {d["key"] for d in get_node_catalog("outline")["dimensions"]}
    assert outline_dims == {"genre", "pace", "structure", "tone", "length_style"}


# ---------- 拼装器 ----------
def test_assembler():
    from app.engines.tendency import assemble_tendency, merge_tendency
    from app.engines.tendency.assembler import render_style_block

    # chips → 指令
    a = assemble_tendency(
        "outline", {"genre": "赛博朋克", "pace": "快节奏爽文", "tone": ["悬疑", "暗黑"]}
    )
    assert "赛博朋克" in a.directives_text
    assert "钩子和爽点" in a.directives_text
    assert "悬疑" in a.directives_text
    assert "阴暗面" in a.directives_text

    # 自定义值(不在预设里的 chip 文案 + _custom)
    b = assemble_tendency(
        "outline",
        {"genre": "克苏鲁蒸汽朋克", "_custom": {"tone": "带一点黑色幽默"}},
    )
    assert "用户额外要求" in b.directives_text
    assert "克苏鲁蒸汽朋克" in b.directives_text
    assert "黑色幽默" in b.directives_text

    # 两层作用域:临时覆盖全局,未指定回落
    merged = merge_tendency({"pace": "慢热铺垫", "tone": ["治愈"]}, {"pace": "快节奏爽文"})
    assert merged["pace"] == "快节奏爽文"
    assert merged["tone"] == ["治愈"]

    # 空倾向 → 空块
    assert render_style_block(assemble_tendency("outline", None)) == ""


# ---------- 蓝图解析器 ----------
SAMPLE_BLUEPRINT = """\
以下是章节蓝图:

第1章 - 雨夜的義体维修师
本章定位:开篇
核心作用:建立主角与世界观
悬念密度:中
伏笔操作:埋设:神秘芯片的来历
认知颠覆:★☆☆☆☆
涉及人物:林晚,老周
关键道具:神秘芯片
场景地点:九龙城寨下层维修铺
本章简述:义体维修师林晚在一具报废军用义体中发现神秘芯片,当晚维修铺被不明武装人员盯上。

**第2章 - 猎杀名单**
本章定位:冲突升级
核心作用:主角被卷入阴谋
悬念密度:高
伏笔操作:强化:神秘芯片的来历
认知颠覆:★★☆☆☆
涉及人物:林晚、K
关键道具:无
场景地点:霓虹市集
本章简述:林晚发现自己上了企业的猎杀名单,神秘掮客K主动接触,提出交易。
"""


def test_parser():
    from app.engines.pipeline.blueprint_parser import parse_blueprint, validate_blueprint

    chapters = parse_blueprint(SAMPLE_BLUEPRINT)
    assert len(chapters) == 2

    c1, c2 = chapters
    assert c1["chapter_number"] == 1
    assert c1["title"] == "雨夜的義体维修师"
    assert c1["chapter_role"] == "开篇"
    assert c1["characters_involved"] == ["林晚", "老周"]
    assert c1["key_items"] == ["神秘芯片"]
    assert "义体维修师" in c1["summary"]

    # markdown 加粗的章节头 + 顿号分隔 + "无"→空列表
    assert c2["chapter_number"] == 2
    assert c2["title"] == "猎杀名单"
    assert c2["characters_involved"] == ["林晚", "K"]
    assert c2["key_items"] == []

    # 校验:范围过滤与缺章警告
    valid, warnings = validate_blueprint(chapters, 1, 3)
    assert len(valid) == 2
    assert any("缺少章节" in w for w in warnings)


# ---------- 全链路(mock LLM)----------
MOCK_ARCH_REPLIES = [
    "当义体维修师林晚捡到记录企业罪证的芯片,必须在猎杀中活下去,否则真相永埋;与此同时,芯片正在改写她的神经。",  # 种子
    "[主角]林晚:背景创伤:妹妹死于义体排异……深层渴望:找回身而为人的实感。\n[反派]徐总监:表面追求:回收芯片……",  # 角色
    "1. 物理维度:九龙城寨式垂直贫民窟与云端企业塔……法则体系:义体化程度越高,神经侵蚀越深……",  # 世界观
    "第一幕(第1-2章):林晚捡到芯片,被列入猎杀名单……主要伏笔:芯片来历[埋设1-2章,回收6-8章]……",  # 情节
]

MOCK_BLUEPRINT_REPLY = SAMPLE_BLUEPRINT + """\
第3章 - 交易与背叛
本章定位:第一幕收束
核心作用:主角接受交易,阵营初现
悬念密度:高
伏笔操作:回收:K的真实身份
认知颠覆:★★★☆☆
涉及人物:林晚,K,徐总监
关键道具:神秘芯片
场景地点:云端企业塔
本章简述:交易当晚K暴露双重身份,林晚在背叛中意识到芯片里藏着的不是数据,而是一个人格。
"""


class MockAdapter:
    """按调用顺序返回预置回复的假 LLM。"""

    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls: list[str] = []

    async def ask(self, prompt: str, system: str | None = None) -> str:
        self.calls.append(prompt)
        return self._replies.pop(0)


async def _full_pipeline() -> None:
    # 独立内存库,不碰开发库
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Outline, OutlineVersion, Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    project = Project(
        title="霓虹深渊",
        topic="义体维修师捡到改写命运的芯片",
        genre="赛博朋克",
        target_chapters=3,
        target_words_per_chapter=3000,
        global_tendency={"pace": "快节奏爽文"},
    )
    db.add(project)
    db.commit()

    from app.engines.pipeline import architecture as arch_mod
    from app.engines.pipeline import blueprint as bp_mod

    # --- 架构生成(mock 4 次调用) ---
    arch_adapter = MockAdapter(MOCK_ARCH_REPLIES)
    with patch.object(arch_mod, "get_adapter_for", return_value=arch_adapter):
        result = await arch_mod.generate_architecture(
            topic=project.topic,
            genre=project.genre,
            number_of_chapters=3,
            word_number=3000,
            tendency={"tone": ["暗黑"]},
            global_tendency=project.global_tendency,
        )

    assert len(arch_adapter.calls) == 4
    assert "林晚" in result.core_seed
    # 倾向注入:全局 pace + 临时 tone 都要出现在第一步 prompt 里
    p0 = arch_adapter.calls[0]
    assert "钩子和爽点" in p0 and "阴暗面" in p0 and "本次写作倾向" in p0

    arch_mod.save_architecture(db, project, result)
    db.commit()
    assert project.architecture is not None
    assert project.architecture.core_seed.startswith("当义体维修师")

    # --- 蓝图生成(mock 1 次调用,3 章一块装下) ---
    bp_adapter = MockAdapter([MOCK_BLUEPRINT_REPLY])
    with patch.object(bp_mod, "get_adapter_for", return_value=bp_adapter):
        chapters, warnings = await bp_mod.generate_blueprint(
            novel_architecture=result.full_text,
            number_of_chapters=3,
            global_tendency=project.global_tendency,
        )

    assert len(chapters) == 3
    assert not warnings

    bp_mod.save_blueprint(db, project, chapters)
    db.commit()

    outlines = (
        db.query(Outline)
        .filter(Outline.project_id == project.id)
        .order_by(Outline.chapter_number)
        .all()
    )
    assert len(outlines) == 3
    assert outlines[2].title == "交易与背叛"
    assert outlines[2].characters_involved == ["林晚", "K", "徐总监"]
    assert all(o.content_hash for o in outlines)

    # 版本快照 v1(级联基线)
    assert db.query(OutlineVersion).count() == 3
    assert project.status == "writing"


def test_full_pipeline():
    asyncio.run(_full_pipeline())
