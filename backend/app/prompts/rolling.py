# app/prompts/rolling.py
# -*- coding: utf-8 -*-
"""滚动规划提示词:卷纲(指南针) + 分段展开时的已成文状态注入。

模板已外置到 app/prompts/templates/rolling/ 目录,通过 loader 加载。
对外变量名(MACRO_PLAN_PROMPT / ROLLING_CONTEXT_BLOCK)保持不变,
调用方无需修改。
"""
from app.prompts.loader import load_prompt

# 卷纲:把全书切成若干卷,每卷一段目标。只定方向不定细节,细节写到该卷再展开。
MACRO_PLAN_PROMPT = load_prompt("rolling/macro_plan.txt")

# 展开下一卷时注入的"已成文状态"块(拼进架构文本尾部)
ROLLING_CONTEXT_BLOCK = load_prompt("rolling/context_block.txt")
