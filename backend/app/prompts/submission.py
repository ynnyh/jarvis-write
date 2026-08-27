# app/prompts/submission.py
# -*- coding: utf-8 -*-
"""投稿包生成提示词:把项目素材压缩成知乎等平台的投稿表单字段。

输出严格 JSON,字段对齐知乎「故事类」投稿表单:
  作品名称(≤15字) / 频道 / 时空 / 标签(≤7) / 金句(≤25字) / 简介(短中长) / 封面提示词

模板已外置到 app/prompts/templates/submission/ 目录。
"""
from __future__ import annotations

from app.prompts.loader import load_prompt

SUBMISSION_PROMPT = load_prompt("submission/submission.txt")
