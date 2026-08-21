# app/prompts/cascade.py
# -*- coding: utf-8 -*-
"""大纲级联更新引擎的 Prompt:改动分级 / 下游影响分析 / 单章大纲重生成。

原则(docs/03-engines.md):级联永远不自动执行,只分析+提示,用户拍板。
"""

# =============== 1. 改动分级(规则粗筛后的 LLM 精判) ===============
CHANGE_CLASSIFY_PROMPT = """\
你是小说编辑。用户修改了第{chapter_number}章的大纲,请判断这次改动的性质。

【修改前】
{old_outline}

【修改后】
{new_outline}

【具体变更字段】
{changed_fields}

判断标准:
- major(大改):改变了情节走向、人物命运、伏笔的埋设/回收、关键信息的揭露时机、
  出场人物增减、结局导向 —— 会影响后续章节的逻辑
- minor(小改):措辞润色、细节微调、场景描写变化、悬念密度微调 —— 不影响后续章节

严格按 JSON 输出(不要 markdown 围栏,不要解释):
{{"change_type": "major|minor", "summary": "一句话概括这次改动的实质"}}
"""

# =============== 2. 下游影响分析 ===============
IMPACT_ANALYSIS_PROMPT = """\
你是小说结构顾问。用户修改了第{chapter_number}章的大纲(大改),请分析哪些下游章节受影响。

【改动概要】
{change_summary}

【修改前的第{chapter_number}章大纲】
{old_outline}

【修改后的第{chapter_number}章大纲】
{new_outline}

【下游章节大纲(候选受影响范围)】
{downstream_outlines}

【未回收的伏笔(改动可能破坏伏笔链)】
{open_foreshadowings}

分析要求:
1. 逐章判断:该章的剧情/人物/伏笔是否依赖被改动的内容
2. 只列出确实受影响的章节,不要为了保险把所有章都列上
3. action 取值:regenerate(依赖被破坏,建议重生成大纲)/ review(轻微关联,建议人工看一眼)

严格按 JSON 输出(不要 markdown 围栏,不要解释):
{{
  "affected": [
    {{"chapter_number": 数字, "reason": "受影响原因(具体指出依赖了什么)", "action": "regenerate|review"}}
  ],
  "overall": "整体影响概述(1-2句)"
}}

无下游影响时输出 {{"affected": [], "overall": "无下游影响"}}
"""

# =============== 3. 单章大纲级联重生成 ===============
OUTLINE_REGENERATE_PROMPT = """\
你是小说结构师。上游第{source_chapter}章大纲已被修改,请重写第{chapter_number}章的大纲使其与新剧情走向一致。

【小说架构(节选)】
{architecture_brief}

【上游改动概要】
{change_summary}

【修改后的第{source_chapter}章大纲(新的剧情基准)】
{new_source_outline}

【第{chapter_number}章的旧大纲(仅供参考,与新走向冲突处必须改)】
{old_outline}

【相邻章节大纲(保持衔接)】
{neighbor_outlines}

【本章受影响原因】
{reason}

{style_directives}
要求:
1. 只调整与上游改动冲突的部分,能保留的剧情尽量保留(最小侵入)
2. 伏笔链保持完整:上游改动若取消了某伏笔,本章不得再回收它
3. 与相邻章节自然衔接

输出格式(严格按此模板,字段名不得改动):
第{chapter_number}章 - [章节标题]
本章定位:[定位]
核心作用:[作用]
悬念密度:[低/中/高]
伏笔操作:[埋设:xxx / 强化:xxx / 回收:xxx / 无]
认知颠覆:[1-5星,如 ★★★☆☆]
涉及人物:[人物1,人物2,...]
关键道具:[道具或 无]
场景地点:[地点]
本章简述:[100字以内]

仅输出以上格式内容,不要解释。
"""

# =============== 4. 修改指令解析(自然语言 → 受影响章大纲改写预览) ===============
EDIT_DIRECTIVE_PROMPT = """\
你是小说结构编辑。作者对整部小说提出了一条结构性修改指令,请判断哪些章节的大纲需要改写,并给出改写结果。

【修改指令】
{directive}

【小说架构简报】
{architecture_brief}

【全部章节蓝图(章号/标题/简述)】
{blueprint_digest}

要求:
1. 只改写确实受指令影响的章节,不受影响的不要列出
2. new_summary 是改写后的完整"本章简述"(100字以内),不是改动说明
3. new_title 仅在章节标题需要随之变化时给出,否则省略该字段
4. change_reason 一句话说明该章为什么受指令影响
5. 指令明显是"删除/不要某角色"时,suggest_retire 列出建议退场的人物名;否则为空数组
6. 没有任何章节受影响时 items 为空数组,并在 analysis 里说明原因

严格按 JSON 输出(不要 markdown 围栏,不要解释):
{{
  "analysis": "一两句总体判断",
  "items": [
    {{"chapter_number": 数字, "new_title": "新标题(可选)", "new_summary": "改写后的本章简述", "change_reason": "受影响原因"}}
  ],
  "suggest_retire": ["建议退场的人物名"]
}}
"""
# =============== 5. 单章大纲研讨(多轮对话 → 改写提案) ===============
# 与正文的「重写研讨」(REVISE_CHAT_SYSTEM_PROMPT)同构,但研讨对象是大纲:
# 聊清"这章大纲哪里不对" → 蒸馏成结构化改写提案(新标题+新简述+改动说明),
# 前端确认后走修改指令的 apply 链路(版本化落库 + 正文标失配)。
OUTLINE_DISCUSS_SYSTEM_PROMPT = """\
你是小说结构编辑,正和作者研讨第{chapter_number}章的大纲该怎么改。

【小说架构简报】
{architecture_brief}

【本章当前大纲】
{outline_block}

【相邻章节大纲(改写要保持衔接)】
{neighbor_block}

【成文状态】
{written_note}

研讨规则:
- 先听懂作者对哪里不满意,必要时追问细节,再给具体建议
- 建议要落到"本章简述怎么改写"上,别空谈理论
- 保持与相邻章节的衔接,不破坏伏笔链
- 本章已有正文时,提醒改动意味着正文需要重写(会标记失配)
- 每次回复控制在 150 字以内,像编辑同事对话,不要写小作文
"""

OUTLINE_DISCUSS_DISTILL_PROMPT = """\
把下面这段"作者与结构编辑关于第{chapter_number}章大纲的研讨"蒸馏成一份大纲改写提案。

【本章当前简述】
{current_summary}

【研讨记录】
{transcript}

要求:
- new_summary 是改写后的完整"本章简述"(100字以内),不是改动说明;要体现研讨达成的共识
- new_title 仅在标题需要随之变化时给出,否则为 null
- change_reason 一句话说明改了什么、为什么
- 研讨还没有形成明确改写方向时,只输出一个短横线:-

严格按 JSON 输出(不要 markdown 围栏,不要解释):
{{"new_title": "新标题或 null", "new_summary": "改写后的本章简述", "change_reason": "改动说明"}}
"""

# =============== 6. 章节标题润色(生成候选,供作者挑选;不落库)===============
# 作者觉得现有章节标题不合适(如太夸张 / 标题党),让 AI 基于本章大纲给几个候选。
# 纯展示性改动:选定后走 editOutline 只改 title,不影响正文(见 differ._COSMETIC_FIELDS)。
CHAPTER_RETITLE_PROMPT = """\
你是小说编辑,为第{chapter_number}章重拟章节标题,给作者几个候选挑选。

【小说架构简报】
{architecture_brief}

【本章大纲】
{outline_block}

【当前标题】
{current_title}

【作者的要求】
{directive}

要求:
- 给出 {count} 个风格各异的候选标题,都要贴合本章大纲的实际内容,不能剧透关键反转或结局
- 克制、准确、有文学质感;不做标题党,不堆砌形容词,不浮夸煽情,不用感叹号
- 每个标题简洁(建议 4-12 字,最多 15 字),像正经出版长篇小说的目录
- 优先满足作者的要求;不要与"当前标题"雷同

严格按 JSON 输出(不要 markdown 围栏,不要解释):
{{"titles": ["候选标题1", "候选标题2", "..."]}}
"""

# =============== 7. 批量重拟标题(一键换一批,不动剧情)===============
# 作者对整本书的标题风格不满意时,一次性为多章各拟一个新标题。只改标题,不碰简述/剧情
# (选定后逐章走 apply_outline_edit 只改 title,cosmetic 改动,不标正文失配)。
BATCH_RETITLE_PROMPT = """\
你是小说编辑,要为下面这些章节统一重拟章节标题。只改标题,绝不改动剧情、简述或任何设定。

【小说架构简报】
{architecture_brief}

【作者对标题风格的要求】
{directive}

【各章的现标题与内容(按此为每章拟新标题)】
{chapters_block}

要求:
- 为上面列出的每一章各拟 1 个新标题,必须贴合该章"内容",不能剧透关键反转或结局
- 首要基准是"作者对标题风格的要求",整本书的标题要风格统一、气质一致
- 克制、准确、有文学质感;不做标题党,不堆砌形容词,不浮夸煽情,不用感叹号
- 每个标题简洁(建议 4-12 字,最多 15 字),像正经出版长篇小说的目录
- 各章标题不要雷同、也不要都用同一句式;若现标题已经很好,可保留原样

严格按 JSON 输出(不要 markdown 围栏,不要解释),chapter 用章号数字:
{{"titles": [{{"chapter": 1, "title": "新标题"}}, {{"chapter": 2, "title": "新标题"}}]}}
"""
