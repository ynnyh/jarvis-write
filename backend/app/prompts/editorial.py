# app/prompts/editorial.py
# -*- coding: utf-8 -*-
"""编辑部提示词:主编评分 / 校对。

模板已外置到 app/prompts/templates/editorial/ 目录。
"""
from app.prompts.loader import load_prompt

# 主编评分:四维打分 + 短评 + 3 条可执行建议(必须引用原文举证)
REVIEW_PROMPT = load_prompt("editorial/review.txt")

# 校对:错别字/语病/标点/重复用词,输出可精确替换的问题清单
PROOFREAD_PROMPT = load_prompt("editorial/proofread.txt")
