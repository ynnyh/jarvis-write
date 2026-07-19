# app/engines/memory/store.py
# -*- coding: utf-8 -*-
"""章节记忆库:Chroma 持久化存储 + 语义检索。

设计要点:
- 每个项目一个 collection(chapters_{project_id}),阶段 3 扩为 6 桶。
- 章节正文分段入库,metadata 带 chapter_number。
- 检索时排除最近 N 章(它们已作为直接上下文注入,不用检索重复)。
- Embedding 走用户配置的 provider;embedding 不可用时优雅降级:
  add 静默跳过、retrieve 返回空列表,逐章生成仍可工作(只靠最近章节)。
"""
from __future__ import annotations

import logging
import re

from app.config import get_settings
from app.llm.embeddings import EmbeddingClient

logger = logging.getLogger("jarvis-write.memory")

# 分段:每段目标字数(中文),过长切开,过短并入前段
_SEG_TARGET = 500


def split_text(text: str, target: int = _SEG_TARGET) -> list[str]:
    """按段落聚合切分正文,每段约 target 字。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    segments: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) <= target or not buf:
            buf = f"{buf}\n{p}".strip()
        else:
            segments.append(buf)
            buf = p
    if buf:
        segments.append(buf)
    return segments


class ChapterMemory:
    """项目级章节记忆。"""

    def __init__(self, project_id: int):
        self.project_id = project_id
        self.settings = get_settings()
        self._client = None  # 惰性初始化,chromadb 导入较重

    def _collection(self):
        if self._client is None:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._client = chromadb.PersistentClient(
                path=self.settings.chroma_persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        # 不用 chroma 内置 embedding(避免离线下载模型),向量自己算
        return self._client.get_or_create_collection(
            name=f"chapters_{self.project_id}",
            metadata={"hnsw:space": "cosine"},
        )

    async def add_chapter(self, chapter_number: int, text: str) -> int:
        """章节正文分段入库(重复调用会先删旧段,支持重写场景)。

        返回入库段数;embedding 失败返回 0 并告警,不抛异常。
        """
        segments = split_text(text)
        if not segments:
            return 0
        try:
            vectors = await EmbeddingClient().embed(segments)
        except Exception as exc:  # noqa: BLE001 — 记忆降级不阻塞生成
            logger.warning("embedding 不可用,本章不入向量库: %s", exc)
            return 0

        col = self._collection()
        # 删掉本章旧段(重写场景)
        col.delete(where={"chapter_number": chapter_number})
        ids = [f"ch{chapter_number}_seg{i}" for i in range(len(segments))]
        col.add(
            ids=ids,
            documents=segments,
            embeddings=vectors,
            metadatas=[
                {"chapter_number": chapter_number, "segment": i}
                for i in range(len(segments))
            ],
        )
        logger.info("第 %d 章入库 %d 段。", chapter_number, len(segments))
        return len(segments)

    async def retrieve(
        self,
        query: str,
        *,
        exclude_after: int | None = None,
        k: int | None = None,
    ) -> list[str]:
        """语义检索历史正文片段。

        exclude_after:排除章号 > 该值 - 直接上下文窗口内的章(避免重复注入);
        实际语义:只检索 chapter_number < exclude_after 的段。
        embedding/库不可用时返回 []。
        """
        k = k or self.settings.embedding_retrieval_k
        try:
            vec = (await EmbeddingClient().embed([query]))[0]
            col = self._collection()
            where = (
                {"chapter_number": {"$lt": exclude_after}}
                if exclude_after is not None
                else None
            )
            res = col.query(
                query_embeddings=[vec], n_results=k, where=where
            )
            docs = res.get("documents") or [[]]
            metas = res.get("metadatas") or [[]]
            out = []
            for doc, meta in zip(docs[0], metas[0]):
                out.append(f"[第{meta.get('chapter_number','?')}章] {doc}")
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("语义检索不可用,降级为空: %s", exc)
            return []
