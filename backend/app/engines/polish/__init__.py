# app/engines/polish/__init__.py
"""D. 润色引擎:锁情节改文笔 + 去 AI 味。"""
from .polisher import polish_text
from .ai_flavor import ai_flavor_report

__all__ = ["polish_text", "ai_flavor_report"]
