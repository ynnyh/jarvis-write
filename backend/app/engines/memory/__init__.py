# app/engines/memory/__init__.py
"""基础记忆(阶段 2):章节正文入向量库 + 语义检索。

阶段 3 会升级为分桶加权记忆(6 collection),本模块留好接口。
"""
from .store import ChapterMemory

__all__ = ["ChapterMemory"]
