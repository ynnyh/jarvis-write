# app/engines/consistency/__init__.py
"""B. 长程一致性引擎:时序故事圣经 / 伏笔调度 / 章后抽取 / 一致性检查。"""
from .bible import BibleService
from .foreshadow import ForeshadowScheduler

__all__ = ["BibleService", "ForeshadowScheduler"]
