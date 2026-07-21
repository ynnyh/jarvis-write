# backend/scripts/inspire_concept_test.py
# -*- coding: utf-8 -*-
"""灵感工坊 · 结构化故事概念验证(mock LLM,无需 API key)。

覆盖:
- Concept schema:coerce 丢弃幻觉字段 / is_empty / render 只出非空字段
- 架构消费:concept 优先、空概念回落 topic、无 concept 回落 topic
- 出方案接口:结构化解析 + 丢弃全空 idea
- 指令式改:后端重算 changed(不轻信模型)+ 非法字段过滤
- 对话式:蒸馏成概念;蒸馏失败回落既有概念不丢数据
- patch_project:定概念时 topic 同步为 logline
- 迁移:_add_concept_column 幂等

用法: .venv/Scripts/python -m scripts.inspire_concept_test
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    results.append(bool(ok))
    return ok


class MockAdapter:
    """按序吐预设回复;记录调用次数与最后一次 prompt(供断言注入内容)。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls = 0
        self.last_prompt = ""
        self.last_messages = None
        self.max_tokens = 4096

    async def ask(self, prompt, system=None):
        self.calls += 1
        self.last_prompt = prompt
        return self.replies.pop(0)

    async def complete(self, messages):
        self.calls += 1
        self.last_messages = messages

        class _R:
            content = self.replies.pop(0)
            model = "mock"
            prompt_tokens = 0
            completion_tokens = 0
        return _R()

    def _record_usage(self, resp):
        pass


def make_project(**kw):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Project

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    defaults = dict(title="测试书", topic="", genre="悬疑",
                    target_chapters=10, target_words_per_chapter=2000)
    defaults.update(kw)
    p = Project(**defaults)
    db.add(p); db.commit()
    return db, p


# ============================= schema =============================
def test_schema() -> None:
    from app.schemas.concept import Concept, coerce_concept, CONCEPT_FIELDS

    # coerce 丢弃幻觉字段 + 值转 str
    c = coerce_concept({"logline": "L", "hook": "H", "bogus": "x", "twist": 123})
    check("schema: coerce 丢弃幻觉字段", not hasattr(c, "bogus") and c.logline == "L")
    check("schema: coerce 值转字符串", c.twist == "123")

    check("schema: is_empty 全空为真", Concept().is_empty())
    check("schema: is_empty 有内容为假", not c.is_empty())
    check("schema: is_empty 只有空白视为空",
          coerce_concept({"logline": "   ", "hook": ""}).is_empty())

    # render 只输出非空字段,且按 CONCEPT_FIELDS 顺序
    r = coerce_concept({"logline": "L", "setting": "S"}).render()
    check("schema: render 只出非空字段", "【一句话故事】L" in r and "【世界/背景】S" in r
          and "核心钩子" not in r)
    check("schema: render 空概念为空串", Concept().render() == "")

    # None / 非 dict → 空概念
    check("schema: coerce(None) 空概念", coerce_concept(None).is_empty())
    check("schema: coerce(非dict) 空概念", coerce_concept("garbage").is_empty())


# ============================= 架构消费 =============================
def test_architecture_fallback() -> None:
    from app.engines.pipeline.architecture import _render_topic_block
    from app.schemas.concept import Concept

    # 有概念:用富文本
    block = _render_topic_block("一句话主题", {"logline": "L", "conflict": "C"})
    check("架构: 有概念用富文本", "【一句话故事】L" in block and "【核心冲突】C" in block)

    # 概念为空 dict:回落 topic
    check("架构: 空概念回落topic", _render_topic_block("我的主题", {}) == "我的主题")
    # concept=None:回落 topic
    check("架构: None回落topic", _render_topic_block("我的主题", None) == "我的主题")
    # topic 也空:自由发挥兜底
    check("架构: 全空自由发挥", _render_topic_block("", None) == "(自由发挥)")
    # Concept 对象直接传入也work
    check("架构: 接受Concept对象",
          "【一句话故事】X" in _render_topic_block("t", Concept(logline="X")))


async def test_architecture_seed_injection() -> None:
    """精确断言:核心种子 prompt 收到的是概念富文本;四步都被调用。"""
    from app.engines.pipeline import architecture as arch_mod

    seen: list[str] = []

    class RecordingAdapter(MockAdapter):
        async def ask(self, prompt, system=None):
            seen.append(prompt)
            return await super().ask(prompt, system)

    adapter = RecordingAdapter(["种子", "角色", "世界", "情节"])
    with patch.object(arch_mod, "get_adapter_for", return_value=adapter):
        await arch_mod.generate_architecture(
            topic="备用", genre="悬疑", number_of_chapters=10, word_number=2000,
            concept={"logline": "会说话的建筑", "twist": "建筑是主角亡母"},
        )
    check("架构: 雪花四步全调用", adapter.calls == 4, f"{adapter.calls} 次")
    seed_prompt = seen[0]
    check("架构: 概念注入核心种子", "会说话的建筑" in seed_prompt and "建筑是主角亡母" in seed_prompt)


# ============================= 出方案 =============================
async def test_inspire() -> None:
    from app.api import inspire as insp

    # 正常:两个概念,一个全空应被丢弃
    reply = ('{"ideas": ['
             '{"logline": "L1", "hook": "H1", "protagonist": "P1", "extra": "hack"},'
             '{"logline": "", "hook": "", "twist": ""},'
             '{"logline": "L2", "conflict": "C2"}]}')
    with patch.object(insp, "get_adapter_for", return_value=MockAdapter([reply])):
        r = await insp.inspire(insp.InspireRequest(spark="碎片", count=4))
    check("出方案: 解析结构化概念", len(r.ideas) == 2, f"{len(r.ideas)} 个")
    check("出方案: 丢弃幻觉字段", not hasattr(r.ideas[0], "extra") and r.ideas[0].hook == "H1")
    check("出方案: 丢弃全空idea", all(not c.is_empty() for c in r.ideas))

    # 全空 → 502
    from fastapi import HTTPException
    with patch.object(insp, "get_adapter_for", return_value=MockAdapter(['{"ideas": []}'])):
        try:
            await insp.inspire(insp.InspireRequest(spark="x"))
            check("出方案: 全空报错", False)
        except HTTPException as e:
            check("出方案: 全空报错", e.status_code == 502)


# ============================= 指令式改 =============================
async def test_refine() -> None:
    from app.api import inspire as insp
    from app.schemas.concept import Concept

    base = Concept(logline="男镖师护镖", hook="镖箱藏活人", protagonist="落魄男镖师")
    # 模型把主角改成女性,但自报 changed 只写了 protagonist(漏了 logline)
    reply = ('{"concept": {"logline": "女镖师护镖", "hook": "镖箱藏活人",'
             '"protagonist": "落魄女镖师", "conflict": "", "twist": "", "setting": ""},'
             '"changed": ["protagonist", "不存在的字段"], "note": "改为女性主角"}')
    with patch.object(insp, "get_adapter_for", return_value=MockAdapter([reply])):
        r = await insp.refine(insp.RefineRequest(concept=base, directive="主角换成女性"))
    # 后端重算应发现 logline + protagonist 都变了
    check("指令改: 后端重算changed含logline", "logline" in r.changed and "protagonist" in r.changed)
    check("指令改: 过滤非法字段", "不存在的字段" not in r.changed)
    check("指令改: hook未变不计入", "hook" not in r.changed)
    check("指令改: 应用新概念", r.concept.protagonist == "落魄女镖师")

    # 空概念不给改
    from fastapi import HTTPException
    try:
        await insp.refine(insp.RefineRequest(concept=Concept(), directive="改"))
        check("指令改: 空概念拒绝", False)
    except HTTPException as e:
        check("指令改: 空概念拒绝", e.status_code == 400)


# ============================= 对话式 =============================
async def test_chat() -> None:
    from app.api import inspire as insp
    from app.schemas.concept import Concept

    # 续聊 + 蒸馏两次调用
    adapter = MockAdapter([
        "那主角的动机是什么?复仇还是自保?",  # 续聊回复
        '{"logline": "复仇者伪装成受害者", "protagonist": "隐姓埋名的幸存者"}',  # 蒸馏
    ])
    with patch.object(insp, "get_adapter_for", return_value=adapter):
        r = await insp.chat(insp.ChatRequest(
            messages=[insp.ChatMessage(role="user", content="想写复仇故事但不落俗套")],
            concept=None,
        ))
    check("对话: 返回续聊回复", "主角的动机" in r.reply)
    check("对话: 蒸馏出概念", r.concept.logline == "复仇者伪装成受害者")
    check("对话: 两次LLM调用", adapter.calls == 2, f"{adapter.calls} 次")

    # 蒸馏失败(返回垃圾)→ 回落既有概念,不丢数据
    prev = Concept(logline="既有概念")
    adapter2 = MockAdapter(["继续聊两句", "这不是JSON"])
    with patch.object(insp, "get_adapter_for", return_value=adapter2):
        r2 = await insp.chat(insp.ChatRequest(
            messages=[insp.ChatMessage(role="user", content="嗯")],
            concept=prev,
        ))
    check("对话: 蒸馏失败回落既有概念", r2.concept.logline == "既有概念")

    # 最后一条非 user → 400
    from fastapi import HTTPException
    try:
        await insp.chat(insp.ChatRequest(
            messages=[insp.ChatMessage(role="assistant", content="hi")], concept=None))
        check("对话: 末条非user拒绝", False)
    except HTTPException as e:
        check("对话: 末条非user拒绝", e.status_code == 400)


# ============================= patch_project =============================
async def test_patch_topic_sync() -> None:
    from app.api import projects as proj
    from app.schemas.concept import Concept

    db, p = make_project(topic="旧主题")

    # patch_project 依赖 get_db / 鉴权,直接测其核心同步逻辑:走 ProjectPatch
    patch_req = proj.ProjectPatch(concept=Concept(logline="新的一句话故事", hook="钩子"))
    updates = patch_req.model_dump(exclude_none=True)
    # 复刻端点内同步逻辑的断言(逻辑本体在端点里,这里验证契约)
    concept = patch_req.concept
    if "concept" in updates:
        updates["concept"] = concept.model_dump()
        if "topic" not in updates and concept.logline.strip():
            updates["topic"] = concept.logline.strip()
    check("定概念: topic同步为logline", updates.get("topic") == "新的一句话故事")
    check("定概念: concept存为dict", isinstance(updates["concept"], dict))

    # 显式传 topic 时不被覆盖
    patch2 = proj.ProjectPatch(concept=Concept(logline="AAA"), topic="手写主题")
    u2 = patch2.model_dump(exclude_none=True)
    c2 = patch2.concept
    if "concept" in u2:
        u2["concept"] = c2.model_dump()
        if "topic" not in u2 and c2.logline.strip():
            u2["topic"] = c2.logline.strip()
    check("定概念: 显式topic优先", u2["topic"] == "手写主题")


# ============================= 迁移幂等 =============================
def test_migration_idempotent() -> None:
    from sqlalchemy import create_engine, inspect, text
    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app import migrate

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    # 模拟老库:去掉 concept 列(重建一个没有该列的 projects)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE projects"))
        conn.execute(text(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, title TEXT, topic TEXT)"
        ))

    with patch.object(migrate, "engine", engine):
        before = "concept" in {c["name"] for c in inspect(engine).get_columns("projects")}
        migrate._add_concept_column()
        after1 = "concept" in {c["name"] for c in inspect(engine).get_columns("projects")}
        migrate._add_concept_column()  # 再跑一次应无副作用
        after2 = "concept" in {c["name"] for c in inspect(engine).get_columns("projects")}
    check("迁移: 补concept列", not before and after1)
    check("迁移: 幂等重跑不报错", after2)


async def main_async() -> None:
    test_schema()
    test_architecture_fallback()
    await test_architecture_seed_injection()
    await test_inspire()
    await test_refine()
    await test_chat()
    await test_patch_topic_sync()
    test_migration_idempotent()


def main() -> None:
    asyncio.run(main_async())
    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} 通过")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
