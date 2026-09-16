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

# 故事骨架(docs/20 两段式点火):分段的走向墙(段名/段目标/冲突/起止状态),
# 作者逐段确认后才铺章。与 MACRO_PLAN_PROMPT 的区别:多四个人话字段、默认逐墙确认;
# 卷纲是 >150 章书的自动指南针,骨架是所有书的显式订单——两者共存于 macro_plan。
SKELETON_PROMPT = load_prompt("rolling/skeleton.txt")
