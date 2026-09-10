# app/prompts/scene.py
# -*- coding: utf-8 -*-
"""场景卡 Prompt:分场(蓝图 → 场景卡)、场景级生成、场景级验收。

为什么独立成文件而不是塞进 chapter.py:
  chapter.py 的草稿模板已有 31 个占位符——形式约束(反 AI 腔 + 文风质感)长期
  占掉近半篇幅,净信号变成「别出格、别用力」,生成结果正是「工整但像喝白水」。
  场景级生成的意义之一就是**把每次调用的注意力预算收窄**:一个场景只收到一个
  情绪指令、一组检索到的事实、相邻场景的上下文。所以场景的 prompt 必须自己
  干净,不能再把 31 个变量搬过来。
"""

# =============== 分场:把一章切成 3-5 张场景卡 ===============
# 独立一次调用,不跟写作混在一起:切分与写作是两种能力,混着做两边都做不好。
SCENE_PLAN_PROMPT = """\
你是资深小说结构编辑。下面是一章的蓝图,请把它切成 {min_scenes}-{max_scenes} 个场景。

【第{chapter_number}章《{chapter_title}》蓝图】
本章定位:{chapter_role}
核心作用:{chapter_purpose}
情绪基调:{emotional_tone}
悬念密度:{suspense_level}
认知颠覆:{plot_twist_level}
本章戏核:{scene_anchor}
本章简述:{chapter_summary}
本章节拍:
{beats_block}

【切分要求】
1. 切 {min_scenes}-{max_scenes} 场,**每场都是一个完整的小场景**(有地点、有人物、
   有推进),不是把简述平均切三段。
2. 每场必须写清三件事:
   - goal:这一场推进了什么(读者读完这一场,知道了什么 / 谁变了)
   - conflict:这一场的张力来源(冲突、悬念、两难、信息落差)。
     **没有张力来源的场景就是白水**——即使是过渡场,也要有一个让人想往下读的钩子。
   - emotion_target:这一场要让读者感受到什么(1-2 个词,如 窒息 / 荒诞 / 温热 / 冷硬)
3. tension_level 给 1-5 的整数,构成**章内张力曲线**:
   1=压抑蓄力,2=暗流收紧,3=常规推进,4=情绪高点,5=全力爆发。
   - 一章里必须有起伏,**不许每场都是 3**(那样全章一个温度,读者会走神)。
   - **最强的场面不要在最后一场**:高潮压在倒数第二或第三场,最后一场用来收束
     与留悬念(最后一场摆最高点,读者读完无处落脚)。
   - 相邻两场不要给同一个 tension_level。
4. target_words 每场 {scene_words} 字左右(本章共 {word_number} 字)。
5. 各场的 characters 只列**这一场真的出场的人**;location 写具体地点。
6. fact_hints:这一场需要核对哪些既有的设定或前情(如「主角的旧伤」「上次约定的期限」),
   只写关键词短语,没有就给空数组。

【输出格式】
严格输出 JSON 数组,不要任何解释文字,不要 markdown 代码块:
[
  {{
    "title": "这一场的短标题(6-12 字)",
    "summary": "这一场发生了什么,2-3 句,具体到人物动作与关键细节",
    "location": "具体地点",
    "characters": ["人物1", "人物2"],
    "goal": "这一场推进了什么",
    "conflict": "这一场的张力来源",
    "emotion_target": "情绪词",
    "tension_level": 3,
    "target_words": {scene_words},
    "fact_hints": ["需要核对的设定关键词"]
  }}
]
"""

# =============== 场景级生成:一次调用只写一场 ===============
# 注意力预算的收窄是核心:单场只带一个情绪指令 + 一组检索到的事实 + 相邻上下文,
# 不再把整章级别的 31 个变量全灌进来。
SCENE_DRAFT_PROMPT = """\
你是长篇小说写手。现在只写**一个场景**,不要写整章,不要写下一场。

【本章整体信息(仅供定位,不要在本场里交代别的场次的内容)】
第{chapter_number}章《{chapter_title}》
本章戏核:{scene_anchor}
本章简述:{chapter_summary}

【本场场景卡 · 第 {scene_seq}/{scene_total} 场】
标题:{scene_title}
地点:{scene_location}
出场人物:{scene_characters}
本场目标:{scene_goal}
张力来源:{scene_conflict}
**情绪指令(本场最重要的一条)**:{emotion_target}
{scene_word_target}

【本场的力度要求(按张力档下发,必须照此强弱下笔)】
{tension_directive}
{twist_prep}
【本场需要核对的事实(只写与本场相关的,不要展开无关设定)】
{hard_constraints}
{resource_ledger}

【前情与衔接】
{rolling_summary}
{recent_tail}{handoff_contract}
{previous_scene_tail}

【文风与禁令】
{style_directives}
{deai_rules}

【写这一场的硬要求】
1. **只写这一场**。从本场的第一句直接进入,不要写章节标题,不要写"第X章",
   不要替下一场开个头,也不要在结尾做全章总结。
2. 开篇就落地:第一句给出具体的画面、动作或对白,不要用环境铺陈或心理独白起手。
3. 情绪一律外化成动作、对白与感官细节。**禁止直陈情绪**(不写"他很紧张""她很难过"),
   让读者从人物做了什么里自己读出来。
4. 对话要么推进信息、要么暴露性格,删掉解释性台词。
5. 本场字数 {scene_words} 字左右(下限 {scene_word_floor},上限 {scene_word_ceil})。
6. 写完就停,不要写"接下来会发生什么"的预告。
"""

# =============== 场景级验收:只判这一场 ===============
# 与章级主审的分工:章级判「整章合不合格」,场景级判「这一场该给的给了没有」。
# 章级判定要读整章、代价高、且只在章末给一次结论——不合格就得整章重写。
# 场景级判定在上限内只读一场,不合格只重写这一场。
SCENE_ACCEPT_PROMPT = """\
你是小说编辑,正在审读一个场景。只判这一场,不要评价整章结构。

【场景卡(作者对这一场的预期)】
标题:{scene_title}
地点:{scene_location}
出场人物:{scene_characters}
本场目标:{scene_goal}
张力来源:{scene_conflict}
情绪指令:{emotion_target}
张力档:{tension_level}/5({tension_directive})

【本场正文】
{scene_text}

【判定要求】
逐条判下面五项,每项给 1-10 分以及一句具体的理由(要能指到正文里的具体句子):
1. emotion_fit:读者读完这一场,能不能感受到「{emotion_target}」这个情绪?
   (不是问作者有没有写"压抑"两个字,是问读者会不会真的被压住)
2. goal_done:本场目标有没有真正落地?读者读完是否清楚「这一场推进了什么」?
3. tension_fit:力度对不对?张力档是 {tension_level}/5——档低却写得火爆、或档高却
   写得温吞,都是失配。
4. concreteness:有没有具体画面?还是通篇概述、心理陈述、空泛抒情?
5. prose:文笔是否有 AI 腔(套话、万能句式、情绪直陈、比喻滥用、对话解释化)?

【输出格式】
严格输出 JSON,不要解释文字,不要 markdown 代码块:
{{
  "scores": {{"emotion_fit": 8, "goal_done": 8, "tension_fit": 8, "concreteness": 8, "prose": 8}},
  "comment": "一两句话总结这一场最需要改的地方",
  "suggestions": [
    {{"evidence": "正文里的原句(必须逐字引用)", "issue": "问题是什么", "fix": "改成什么"}}
  ]
}}
"""

# =============== 场景定点重写:只改不合格的一场 ===============
_SCENE_REWRITE_PROMPT = """\
你是长篇小说写手。下面这个场景验收没通过,请重写**这一场**。

【场景卡】
标题:{scene_title}
地点:{scene_location}
出场人物:{scene_characters}
本场目标:{scene_goal}
张力来源:{scene_conflict}
**情绪指令**:{emotion_target}
{tension_directive}

【编辑意见(必须逐条解决)】
{revision_directive}

【上一版(反面参照,不要照抄,只用来对照问题)】
{previous_text}

【重写要求】
1. 只重写这一场,不要动别的场次。
2. 逐条解决上面的编辑意见;意见里引用的原句必须被改掉或被删除。
3. 其余要求与首次生成一致:开篇落地、情绪外化、禁止直陈情绪、对话有功能。
4. 字数 {scene_words} 字左右。
"""

# =============== 场景缝接:把多场拼成一章的过渡处理 ===============
# 逐场生成必然带来「场与场之间像断开的两个片段」的风险。这里不做 LLM 调用——
# 缝接是排版问题,不是创作问题:直接拼接 + 段间空行,衔接靠场景卡里
# 「本场第一条要接上一场」的指令与上一场尾部的原文注入来保证。
SCENE_JOIN_SEPARATOR = "\n\n"

__all__ = [
    "SCENE_PLAN_PROMPT",
    "SCENE_DRAFT_PROMPT",
    "SCENE_ACCEPT_PROMPT",
    "SCENE_JOIN_SEPARATOR",
]
