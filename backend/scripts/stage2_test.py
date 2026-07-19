# backend/scripts/stage2_test.py
# -*- coding: utf-8 -*-
"""阶段 2 验证:逐章生成 + 基础记忆(mock LLM + 假向量,无需 API key)。

验证项:
  1. 正文分段(split_text)
  2. 记忆库:入库/检索(真 chromadb + 假向量)、embedding 失败优雅降级
  3. 逐章生成全链路(mock LLM):上下文组装 → 草稿 → 定稿 → 滚动摘要 → 落库
  4. 连续生成第 2 章:滚动摘要传递、最近章节结尾注入

用法: .venv/Scripts/python -m scripts.stage2_test
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from unittest.mock import patch

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    results.append(ok)
    return ok


def test_split() -> None:
    from app.engines.memory.store import split_text

    text = "\n\n".join(f"段落{i}:" + "字" * 300 for i in range(5))
    segs = split_text(text, target=500)
    ok = len(segs) >= 3 and all(len(s) <= 700 for s in segs)
    check("正文分段", ok, f"{len(segs)} 段")


async def test_memory() -> None:
    from app.engines.memory.store import ChapterMemory

    tmp = tempfile.mkdtemp(prefix="jw_chroma_")

    def fake_vec(texts):
        # 确定性假向量:同词头相似(前两字符相同 → 向量接近)
        out = []
        for t in texts:
            h = float(ord(t[0]) % 97) / 97.0
            out.append([h, 1.0 - h, 0.5])
        return out

    async def fake_embed(self, texts):
        return fake_vec(texts)

    from app.config import get_settings
    with patch("app.engines.memory.store.EmbeddingClient") as MockEC, \
         patch.object(get_settings(), "chroma_persist_dir", tmp):
        MockEC.return_value.embed = lambda texts: _async(fake_vec(texts))

        mem = ChapterMemory(project_id=999)
        long_a = "A测试段落一。" + "内容" * 300
        long_b = "B测试段落二。" + "剧情" * 300
        n = await mem.add_chapter(1, f"{long_a}\n\n{long_b}")
        check("记忆库: 入库", n == 2, f"{n} 段")

        hits = await mem.retrieve("A查询", exclude_after=None)
        check("记忆库: 检索", len(hits) > 0 and "[第1章]" in hits[0], str(hits[:1]))

        hits2 = await mem.retrieve("A查询", exclude_after=1)
        check("记忆库: 章节窗口过滤", hits2 == [], str(hits2))

    # 降级:embedding 抛异常
    with patch("app.engines.memory.store.EmbeddingClient") as MockEC2, \
         patch.object(get_settings(), "chroma_persist_dir", tmp):
        async def boom(texts):
            raise RuntimeError("no embedding endpoint")
        MockEC2.return_value.embed = boom
        mem2 = ChapterMemory(project_id=998)
        n = await mem2.add_chapter(1, "内容")
        hits = await mem2.retrieve("查询")
        check("记忆库: embedding 失败降级", n == 0 and hits == [])

    shutil.rmtree(tmp, ignore_errors=True)


async def _async(v):
    return v


class MockAdapter:
    def __init__(self, reply_fn):
        self.reply_fn = reply_fn
        self.prompts: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.prompts.append(prompt)
        return self.reply_fn(len(self.prompts), prompt)


async def test_chapter_flow() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    import app.db.models  # noqa: F401
    from app.db.models import Chapter, ChapterSummary, Outline, Project
    from app.db.models.project import Architecture
    from app.engines.pipeline import chapter as ch_mod

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    p = Project(title="测试书", topic="测试", genre="赛博朋克",
                target_chapters=3, target_words_per_chapter=500,
                global_tendency={"pov": "第三人称限知"})
    db.add(p); db.commit()
    p.architecture = Architecture(project_id=p.id, core_seed="种子",
                                  character_dynamics="角色", world_building="世界",
                                  plot_architecture="情节")
    for n, t in [(1, "开端"), (2, "冲突"), (3, "结局")]:
        db.add(Outline(project_id=p.id, chapter_number=n, title=t,
                       chapter_role="推进", chapter_purpose="推进剧情",
                       summary=f"第{n}章剧情", characters_involved=["林晚"],
                       key_items=[], scene_location="城市",
                       content_hash=f"h{n}", current_version=1))
    db.commit()

    def reply(i, prompt):
        if "现在开始写" in prompt:
            return "这是草稿正文。" * 30
        if "修订后的" in prompt:
            return "这是定稿正文。" * 30
        return "前情摘要:主角完成了第一步。"

    adapter = MockAdapter(reply)

    async def no_add(self, n, text):
        return 0

    async def no_retrieve(self, q, **kw):
        return []

    async def no_check(*a, **kw):
        return []

    async def no_extract(*a, **kw):
        return {}

    with patch.object(ch_mod, "get_adapter_for", return_value=adapter), \
         patch.object(ch_mod.ChapterMemory, "add_chapter", no_add), \
         patch.object(ch_mod.ChapterMemory, "retrieve", no_retrieve), \
         patch.object(ch_mod, "check_chapter", no_check), \
         patch.object(ch_mod, "extract_and_apply", no_extract):
        c1, _issues, _stats = await ch_mod.generate_chapter(
            db, p, 1, {"emotion_intensity": "平实"}
        )
        db.commit()

        ok = (c1.status == "finalized" and "定稿" in c1.final_content
              and c1.word_count > 0 and c1.outline_version_used == 1)
        check("逐章: 第1章生成落库", ok,
              f"status={c1.status}, {c1.word_count}字")

        # 3 次调用:草稿/定稿/摘要;倾向注入草稿 prompt
        ok = (len(adapter.prompts) == 3
              and "第三人称" in adapter.prompts[0]
              and "平实" in adapter.prompts[0]
              and "本章是第一章,无上文" in adapter.prompts[0])
        check("逐章: 3 次调用+倾向注入+首章无上文", ok,
              f"{len(adapter.prompts)} 次调用")

        s1 = db.query(ChapterSummary).filter_by(project_id=p.id, chapter_number=1).first()
        check("逐章: 滚动摘要落库", s1 is not None and "前情摘要" in s1.rolling_summary)

        # 第 2 章:应注入第 1 章结尾与滚动摘要
        c2, _, _ = await ch_mod.generate_chapter(db, p, 2)
        db.commit()
        draft2 = adapter.prompts[3]
        ok = ("(第1章结尾)" in draft2 and "前情摘要:主角完成了第一步" in draft2
              and c2.chapter_number == 2)
        check("逐章: 第2章注入上文+前情", ok)

        # 无大纲的章应报错
        try:
            await ch_mod.generate_chapter(db, p, 99)
            check("逐章: 无大纲报错", False)
        except ValueError:
            check("逐章: 无大纲报错", True)


def main() -> int:
    print("=" * 56)
    print("阶段 2 验证:逐章生成 + 基础记忆")
    print("=" * 56)
    test_split()
    asyncio.run(test_memory())
    asyncio.run(test_chapter_flow())
    print("-" * 56)
    passed, total = sum(results), len(results)
    print(f"结果: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
