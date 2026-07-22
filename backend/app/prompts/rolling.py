# app/prompts/rolling.py
# -*- coding: utf-8 -*-
"""滚动规划提示词:卷纲(指南针) + 分段展开时的已成文状态注入。"""

# 卷纲:把全书切成若干卷,每卷一段目标。只定方向不定细节,细节写到该卷再展开。
MACRO_PLAN_PROMPT = """\
你是网文总编,为一部 {number_of_chapters} 章的长篇做分卷规划。

【全书架构】
{novel_architecture}
{style_directives}
把全书切成 {segment_count} 卷,每卷约 {segment_size} 章。每卷给出一段 80-150 字的卷目标,必须写清:
1. 本卷开始时主角的处境 → 本卷结束时的处境(状态要有实质变化)
2. 本卷的主线冲突与对手
3. 本卷结束时留下的悬念钩子(勾着读者进下一卷)
全书要有整体爬升感:力量/格局/赌注逐卷抬高,最后一卷完成架构中的终局。

只输出 JSON:
{{
  "segments": [
    {{"start": 1, "end": {segment_size}, "goal": "卷目标…"}},
    ...
  ]
}}
"""

# 展开下一卷时注入的"已成文状态"块(拼进架构文本尾部)
ROLLING_CONTEXT_BLOCK = """\

【本卷目标(第 {start}-{end} 章,规划蓝图必须服务于它)】
{segment_goal}

【下一卷预告(本卷结尾要为它埋好势能)】
{next_goal}

【前情进展(截至第 {written_upto} 章的实际成文,蓝图必须从这个状态出发,不得与之矛盾)】
{rolling_summary}

【未回收的伏笔(优先安排在本卷回收或强化,已错过预期章的优先)】
{open_foreshadows}
"""
