# tests/test_memory_store.py
# -*- coding: utf-8 -*-
"""章节记忆库真实往返:入库 → 语义检索命中,不是只测配置解析。

用一个确定性的假 embedder(把文本映射成向量)替换真实 /embeddings 调用,
其余全走真实 Chroma(临时目录,conftest 已隔离)。验证:
  1) add_chapter 真把段落写进 Chroma,retrieve 能按相似度召回。
  2) exclude_after 真的按章号过滤,最近章不被重复召回。
  3) 重写同一章会覆盖旧段(先删后加)。
  4) embedding 抛错时优雅降级:add 返回 0、retrieve 返回 [],都不抛。

这是补上此前唯一的功能空洞——语义记忆这条路此前一直因中转站 403 跑空,
从没被端到端验证过入库/检索确实工作。
"""
import asyncio

import pytest


def _fake_vector(text: str, dim: int = 16) -> list[float]:
    """把文本确定性地映射成一个 dim 维向量。

    做法:按字符 ord 分桶累加,再归一化。相同文本得相同向量;
    共享词多的文本向量更接近(cosine 更大),足以让相似检索有区分度。
    """
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


class _FakeEmbed:
    """替换 EmbeddingClient.embed 的异步桩:走确定性假向量。"""

    calls = 0

    async def __call__(self, texts):
        _FakeEmbed.calls += 1
        return [_fake_vector(t) for t in texts]


@pytest.fixture
def memory(monkeypatch):
    """返回一个绑定假 embedder 的 ChapterMemory(项目号隔离,避免串集合)。"""
    from app.engines.memory import ChapterMemory
    from app.engines.memory.store import EmbeddingClient

    monkeypatch.setattr(EmbeddingClient, "embed", _FakeEmbed())
    # 每个测试用独立 project_id,collection 名不同,互不干扰
    _fixture_state["pid"] += 1
    return ChapterMemory(_fixture_state["pid"])


_fixture_state = {"pid": 9000}


def test_add_then_retrieve_hits(memory):
    """入库两章内容,检索一个与第 1 章高度重合的 query,应召回第 1 章的段。"""

    async def run():
        await memory.add_chapter(
            1, "程小雨在旧仓库找到了那把生锈的黄铜钥匙,钥匙上刻着奇怪的符号。"
        )
        await memory.add_chapter(
            2, "夜里下起大雨,街角的霓虹灯在水洼里碎成一片模糊的红光。"
        )
        hits = await memory.retrieve("黄铜钥匙 符号 旧仓库", k=2)
        return hits

    hits = asyncio.run(run())
    assert hits, "应至少召回一段"
    # 与钥匙相关的那一章应被召回(命中文本里带章号前缀)
    assert any("钥匙" in h for h in hits), hits
    assert any(h.startswith("[第1章]") for h in hits), hits


def test_exclude_after_filters_recent(memory):
    """exclude_after 应只召回章号 < 该值的段,最近章不被重复注入。"""

    async def run():
        await memory.add_chapter(1, "第一章:黄铜钥匙与旧仓库的符号线索。")
        await memory.add_chapter(2, "第二章:黄铜钥匙再次出现在符号旁边。")
        await memory.add_chapter(3, "第三章:黄铜钥匙的符号终于被破译。")
        # 生成第 4 章、直接窗口=2 时,exclude_after = 4 - 2 = 2,只该看到第 1 章
        return await memory.retrieve("黄铜钥匙 符号", exclude_after=2, k=5)

    hits = asyncio.run(run())
    assert hits, "第 1 章应被召回"
    chapters = {h[:6] for h in hits}
    assert "[第1章]" in {h[:5] for h in hits} or any(
        h.startswith("[第1章]") for h in hits
    ), hits
    # 关键:第 2、3 章(章号 >= 2)一律不出现
    assert not any(h.startswith("[第2章]") for h in hits), hits
    assert not any(h.startswith("[第3章]") for h in hits), hits


def test_rewrite_overwrites_old_segments(memory):
    """重写同一章:旧段应被清掉,只剩新内容,不出现旧文本。"""

    async def run():
        await memory.add_chapter(1, "旧版本:主角叫程小雨,住在城东。")
        await memory.add_chapter(1, "新版本:主角叫林川,住在城西的公寓。")
        # 检索一个旧版才有的词,不应召回旧段(章内已被覆盖)
        col = memory._collection()
        all_docs = col.get(where={"chapter_number": 1}).get("documents") or []
        return all_docs

    docs = asyncio.run(run())
    joined = "\n".join(docs)
    assert "林川" in joined, docs
    assert "程小雨" not in joined, "重写后旧段应被删除"


def test_graceful_degrade_when_embed_fails(monkeypatch):
    """embedding 抛错时:add 返回 0、retrieve 返回 [],都不抛异常(生成不被阻塞)。"""
    from app.engines.memory import ChapterMemory
    from app.engines.memory.store import EmbeddingClient

    async def boom(self, texts):
        raise RuntimeError("403 embeddings not available")

    monkeypatch.setattr(EmbeddingClient, "embed", boom)
    mem = ChapterMemory(9999)

    async def run():
        n = await mem.add_chapter(1, "任意内容,反正 embedding 会挂。")
        hits = await mem.retrieve("任意 query", k=3)
        return n, hits

    n, hits = asyncio.run(run())
    assert n == 0
    assert hits == []
