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
CONSISTENCY_CHECK_PROMPT = """\
你是小说连载的"一致性审校"。请检查新章节是否与已确立的设定矛盾。

【截至上一章的有效事实(硬约束)】
{active_facts}

【前情摘要】
{rolling_summary}

【第{chapter_number}章正文】
{chapter_text}

检查维度:
1. 人物状态:是否使用了已失去的能力/肢体/物品?状态是否凭空恢复?
2. 人物关系与认知:角色是否说出了他此刻不该知道的信息?关系是否无端反转?
3. 时间线与空间:位置迁移是否合理?时间是否倒流?
4. 世界观法则:是否违反已确立的规则/代价/限制?

严格按 JSON 输出(不要 markdown 代码块,不要解释):
{{
  "issues": [
    {{
      "severity": "critical|major|minor",
      "type": "state|knowledge|timeline|worldrule",
      "description": "矛盾描述(引用正文原句)",
      "conflicting_fact": "被违反的事实",
      "suggestion": "修改建议"
    }}
  ]
}}

没有矛盾时输出 {{"issues": []}}。只报告确定的矛盾,不要吹毛求疵。
"""
