# app/prompts/consistency.py
# -*- coding: utf-8 -*-
"""长程一致性引擎的 Prompt:章后抽取 + 一致性检查。

抽取输出严格 JSON(由 extractor 解析落库);检查输出问题列表 JSON。
"""

# =============== 章后状态抽取 ===============
EXTRACTION_PROMPT = """\
你是小说连载的"设定管理员"。请从刚完成的章节正文中,抽取需要长期追踪的状态变化。

【已知实体(名字→类型)】
{known_entities}

【本章之前已生效的关键事实(供对照,判断哪些发生了变化)】
{active_facts}

【已登记且未回收的伏笔】
{open_foreshadowings}

【第{chapter_number}章正文】
{chapter_text}

请抽取以下内容,严格按 JSON 输出(不要 markdown 代码块,不要解释):
{{
  "new_entities": [
    {{"name": "实体名", "entity_type": "character|location|item|faction", "aliases": ["别名"], "note": "一句话说明"}}
  ],
  "fact_changes": [
    {{
      "entity": "实体名",
      "fact_type": "state|ability|possession|relationship|location",
      "content": "新事实内容(如:左手截肢;relationship 时填关系描述,如:结为夫妻)",
      "other_entity": "仅 fact_type=relationship 时必填:关系另一方的实体名,其他类型填null",
      "importance": "critical|major|minor",
      "replaces": "被此事实取代的旧事实内容原文,没有则null"
    }}
  ],
  "foreshadow_ops": [
    {{"op": "plant|reinforce|payoff", "description": "伏笔内容(plant 时新写;reinforce/payoff 时必须抄已登记伏笔的原文)", "expected_payoff_chapter": 数字或null, "importance": "critical|major|minor"}}
  ],
  "knowledge_updates": [
    {{"fact": "对应 fact_changes 里的事实内容", "knower": "reader 或 角色名", "state": "known|suspected"}}
  ]
}}

抽取规则:
1. 只抽"会影响后续章节"的持久变化(受伤/痊愈/获得/失去/关系变化/位置迁移/身份揭露),不抽一次性动作
2. fact_changes 的 replaces:如果新事实使旧事实失效(如"痊愈"取代"受伤"),必须填旧事实原文
3. relationship 条目:entity 与 other_entity 必须是两个不同实体(优先用已知实体名),content 只写两人之间的当前关系(如:兄妹/反目成仇/拜为师徒),同一对人物只报一条最新关系
3. 伏笔:本章新埋的用 plant;呼应强化已有的用 reinforce;明确揭晓的用 payoff
4. knowledge_updates:谁在本章"得知"了什么。读者视角用 knower="reader"
5. 宁缺毋滥,每类最多 8 条,按重要性取舍
"""

# =============== 一致性检查 ===============
# 写后一致性门禁(docs/08 §5.4)的对照 prompt:除圣经硬约束与前情摘要外,
# 新增两个对照源——上一章章末交接契约(章末瞬态事实)与上一章结尾原文。
# 圣经为空时走"仅对照上章"降级路径(active_facts 由调用方填提示语,不跳过)。
CONSISTENCY_CHECK_PROMPT = """\
你是小说连载的"一致性审校"。请检查新章节是否与已确立的设定、以及上一章结尾状态矛盾。

【截至上一章的有效事实(硬约束)】
{active_facts}

【上一章章末交接契约(章末那一刻的结构化状态:剧情时间/地点/人物即时状态)】
{prev_contract}

【上一章结尾原文】
{prev_tail}

【前情摘要】
{rolling_summary}

【第{chapter_number}章正文】
{chapter_text}

检查维度:
1. 人物状态(state):是否使用了已失去的能力/肢体/物品?状态是否凭空恢复?与上章章末状态
   (身体/情绪/正在做的事)是否冲突——典型如"睡着又发呆":上章末刚入睡、本章无时间跳跃
   交代却清醒活动。
2. 人物关系与认知(knowledge):角色是否说出了他此刻不该知道的信息?关系是否无端反转?
3. 时间线与空间(timeline):与契约的剧情时间/章末地点是否冲突?位置迁移是否合理?时间是否倒流?
4. 世界观法则(worldrule):是否违反已确立的规则/代价/限制?

严格按 JSON 输出(不要 markdown 代码块,不要解释):
{{
  "issues": [
    {{
      "severity": "blocker|major|minor",
      "type": "state|knowledge|timeline|worldrule",
      "description": "问题点:矛盾描述",
      "evidence": "证据段落:从本章正文逐字引用的原句",
      "conflicting_fact": "被违反的事实或上章章末状态",
      "suggestion": "修正建议"
    }}
  ]
}}

severity 判定:
- blocker:与硬约束或上章章末状态直接冲突的硬矛盾(读者必然出戏),必须修正才能放行
- major:大概率矛盾但尚存解释空间
- minor:轻微不一致或措辞瑕疵

没有矛盾时输出 {{"issues": []}}。只报告确定的矛盾,不要吹毛求疵;
每个问题必须给出 evidence(逐字引用本章正文),引不到原文的不要报。
"""

# =============== 写前审核(Pre-flight Check)===============
# 写前审核(docs/08 §5.3):草稿调用前,本章蓝图 vs 上一章契约,找"动笔前就看得
# 出的矛盾"。只警告不阻断(蓝图可以故意安排时间跳跃),警告落 chapter_issues
# (source=preflight)并随生成响应透出。
PREFLIGHT_CHECK_PROMPT = """\
你是小说连载的"写前审稿"。第{chapter_number}章还没动笔,请对照上一章的章末交接契约,
审查本章蓝图是否存在"动笔前就能确定的矛盾"。

【上一章章末交接契约(上一章结尾那一刻的结构化状态)】
{prev_contract}

【第{chapter_number}章蓝图(写作计划,尚未成文)】
{blueprint}

审查维度(只报这两类):
1. 人物状态(state):蓝图安排的出场角色/行动与上章章末状态直接冲突——如某角色
   上章末重伤昏迷,蓝图却安排他本章正常出场行动;或出场角色上章末已离场/死亡。
2. 时间线与空间(timeline):蓝图的时间/地点安排与上章章末状态冲突且无任何时间
   跳跃交代——如蓝图写"清晨渡口出发",上章契约却是"深夜刚入睡、time_jump 为 none"。

注意:时间跳跃是常见叙事手法。蓝图或契约已暗示时间跳跃(time_jump_hint 非 none、
蓝图明确写了"次日/数日后"等)时,不算矛盾。只报确定的硬冲突,不要吹毛求疵。

严格按 JSON 输出(不要 markdown 代码块,不要解释):
{{
  "warnings": [
    {{
      "type": "state|timeline",
      "description": "问题点:蓝图与上章章末状态的矛盾描述",
      "evidence": "证据:蓝图中引发冲突的原文片段",
      "conflicting_fact": "被违反的上章章末状态",
      "suggestion": "修正建议(调整蓝图或补时间跳跃交代)"
    }}
  ]
}}

没有矛盾时输出 {{"warnings": []}}。
"""

# =============== 章末交接契约提取 ===============
# 与 EXTRACTION_PROMPT 互补:EXTRACTION 抽"跨章持久事实"写圣经;本 prompt 抽
# "章末那一刻的瞬态"(剧情时间/地点/人物即时状态/未决线索),落 chapter_states 表,
# 供下一章开头衔接注入与连续性门禁比对(docs/08 §5.2)。
HANDOFF_CONTRACT_PROMPT = """\
你是小说连载的"场记"。请从刚完成的章节正文中,提取本章结尾那一刻的结构化状态
("章末交接契约"),供下一章开头无缝衔接。

【第{chapter_number}章正文】
{chapter_text}

请严格按 JSON 输出(不要 markdown 代码块,不要解释):
{{
  "in_story_time": "章末剧情时间(如:第三日 深夜;无法判断填 null)",
  "location": "章末场景地点",
  "scene_continues": true或false,
  "characters": [
    {{
      "name": "人物名",
      "location": "此人章末所在位置",
      "physical": "身体状态(伤势/体力等,无异常填 null)",
      "emotional": "情绪状态",
      "doing": "章末正在做什么(要具体,如:刚入睡/启程赶路/与人对峙)",
      "knows": ["此人截至章末新得知的关键信息"],
      "unresolved_intent": "未完成的意图或下一步打算,无则填 null"
    }}
  ],
  "open_threads": ["章末悬而未决的线索/悬念"],
  "time_jump_hint": "none|next_morning|hours_later|days_later(章末是否暗示时间跳跃)"
}}

提取规则:
1. 只记录章末那一刻的状态,不要复述整章剧情
2. characters 只收章末在场或状态刚发生变化的人物,最多 6 人
3. doing/physical 必须具体("刚入睡"而非"休息")——下章要靠它校验"睡着又发呆"类矛盾
4. scene_continues:下一章是否应紧接同一场景继续(章末停在场景中段为 true)
5. 无法确定的字段填 null 或空数组,不要编造
"""
