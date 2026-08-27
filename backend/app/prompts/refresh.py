# app/prompts/refresh.py
# -*- coding: utf-8 -*-
"""重构翻新引擎提示词:为已有书回填章节节拍(beats)。

已有书的 outline 多半没有 beats(老数据),重度翻新要靠 beats 才有结构。
这里用「本章简述 + 已成文正文(若有)」反推 3-5 个场景节拍。

模板已外置到 app/prompts/templates/refresh/ 目录。
"""
from app.prompts.loader import load_prompt

# 回填节拍:优先依据已成文正文(最真实),无正文时依据蓝图简述反推
BEATS_BACKFILL_PROMPT = load_prompt("refresh/beats_backfill.txt")
