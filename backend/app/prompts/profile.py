# app/prompts/profile.py
# -*- coding: utf-8 -*-
"""创作偏好档案相关 Prompt。

档案是贯穿全书的创作宪法(文风/禁忌/读者定位/其他主张),注入所有生成环节。
两个 prompt:
- PROFILE_ABSORB_PROMPT:把研讨对话里聊出的新主张合并进已有档案。
- PROFILE_EXTRACT_PROMPT:对已生成的书,从概念/架构/简介/抽样正文反向提炼出档案,
  让老书不用作者手填就有一份与正文相符的档案。

模板已外置到 app/prompts/templates/profile/ 目录。
"""
from app.prompts.loader import load_prompt

PROFILE_ABSORB_PROMPT = load_prompt("profile/absorb.txt")
PROFILE_EXTRACT_PROMPT = load_prompt("profile/extract.txt")
