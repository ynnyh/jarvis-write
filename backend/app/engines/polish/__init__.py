# app/engines/polish/__init__.py
"""D. 润色引擎:锁情节改文笔 + 去 AI 味。"""
from .polisher import discuss_fragment, polish_fragment, polish_text
from .ai_flavor import ai_flavor_report

__all__ = ["polish_text", "polish_fragment", "discuss_fragment", "ai_flavor_report"]
