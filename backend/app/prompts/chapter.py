# app/prompts/chapter.py
# -*- coding: utf-8 -*-
"""逐章生成 Prompt:滚动摘要 → 草稿 → 定稿(借鉴 AI_NovelGenerator 的多阶段思路)。

上下文组装(见 docs/02-data-model.md 数据流):
  本章蓝图 + 下章蓝图(承上启下)+ 最近章节正文尾部 + 滚动摘要
  + 语义检索的历史片段 + {style_directives} 倾向块
"""

# =============== 滚动摘要:压缩"更早"的剧情 ===============
ROLLING_SUMMARY_PROMPT = """\
你是专业小说编辑。请把以下已完成章节的剧情压缩成"前情摘要",供后续写作参考。

已有前情摘要(可能为空):
{previous_summary}

新完成的章节正文:
第{chapter_number}章《{chapter_title}》:
{chapter_text}

要求:
1. 把新章节的关键剧情融入前情摘要,输出合并后的完整摘要
2. 保留:关键事件、人物状态变化、新揭示的信息、未解决的悬念
3. 删除:场景细节、对话原文、修辞
4. 按时间顺序组织,总长不超过 800 字

仅输出合并后的前情摘要,不要解释。
"""

# =============== 章节草稿 ===============
CHAPTER_DRAFT_PROMPT = """\
你是一位职业小说家,正在创作长篇小说。请写出第{chapter_number}章的完整正文。

【小说架构(节选)】
{architecture_brief}

【前情摘要】
{rolling_summary}

【最近章节结尾(直接上文,衔接必须自然)】
{recent_tail}

【历史相关片段(供呼应伏笔/保持一致,不可照抄)】
{retrieved_context}

【人物当前状态(硬约束,必须遵守,不得违反)】
{hard_constraints}

【伏笔回收提醒(到期伏笔应在本章或近期自然回收)】
{foreshadow_reminders}

{avoid_repetition}
【本章蓝图】
第{chapter_number}章《{chapter_title}》
- 本章定位:{chapter_role}
- 核心作用:{chapter_purpose}
- 悬念密度:{suspense_level}
- 伏笔操作:{foreshadowing}
- 涉及人物:{characters_involved}
- 关键道具:{key_items}
- 场景地点:{scene_location}
- 本章简述:{chapter_summary}

【下一章蓝图(为其留好铺垫,不要写进本章)】
{next_chapter_brief}

{style_directives}
写作要求:
1. 目标字数:约{word_number}字
2. 严格落实本章蓝图的定位、伏笔操作与简述,不得偏离主线
3. 与"最近章节结尾"无缝衔接,开头不要重复前文,不要写"上回说到"
4. 人物言行必须符合已建立的性格与状态,不得与前情矛盾
5. 章末留钩子,呼应下一章蓝图
6. 只写正文,不要章节标题、序号、任何解释或元信息

现在开始写第{chapter_number}章正文:
"""

# =============== 定稿:自查+润修 ===============
CHAPTER_FINALIZE_PROMPT = """\
你是资深文学编辑。以下是长篇小说第{chapter_number}章《{chapter_title}》的草稿,请修订出定稿。

【本章蓝图(修订不得偏离)】
- 核心作用:{chapter_purpose}
- 伏笔操作:{foreshadowing}
- 本章简述:{chapter_summary}

【前情摘要(检查一致性用)】
{rolling_summary}

【草稿正文】
{draft_text}

{style_directives}
修订要求:
1. 修正:与前情矛盾之处、人物言行失当、逻辑漏洞、时间线错误
2. 提升:删除冗余重复,收紧节奏,增强场景感与对话的自然度
3. 保持:剧情走向、伏笔操作、章末钩子不变;字数与草稿相当
4. 只输出修订后的完整正文,不要标注修改点,不要任何解释

修订后的第{chapter_number}章正文:
"""
