# 02 · 数据模型设计（最关键，重点保存）

> 这份是整个系统的骨架。三大引擎都靠这些表运转。字段设计吸收了调研中
> knowrite（时序真相库）、NovelClaw（分桶记忆/伏笔四态）、KazKozDev
> （读者已知/角色已知分离）的做法。

---

## 一、项目与大纲

### `projects` — 小说项目
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| title | str | 小说名 |
| topic | text | 核心主题 |
| genre | str | 题材（可来自标签，如"赛博朋克"） |
| target_chapters | int | 目标章节数 |
| target_words_per_chapter | int | 每章目标字数 |
| global_tendency | JSON | 全局倾向（见 04 文档，标签组合） |
| status | enum | draft / outlining / writing / done |
| created_at / updated_at | datetime | |

### `architecture` — 顶层架构（雪花写作法产出）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | FK | |
| core_seed | text | 核心种子（一句话故事本质） |
| character_dynamics | text | 角色动力学 |
| world_building | text | 世界观 |
| plot_architecture | text | 情节架构 |
| version | int | 架构也可改，做版本 |

### `outlines` — 章节大纲（每章一行，可独立编辑）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | FK | |
| chapter_number | int | 第几章 |
| title | str | 章节标题 |
| chapter_role | str | 本章定位（借鉴雪花蓝图字段） |
| chapter_purpose | str | 核心作用 |
| suspense_level | str | 悬念密度 |
| foreshadowing | text | 本章伏笔操作（自然语言描述） |
| plot_twist_level | str | 认知颠覆程度 |
| summary | text | 本章简述 |
| characters_involved | JSON | 涉及角色 id 列表 |
| key_items | JSON | 关键道具 |
| scene_location | str | 场景地点 |
| **content_hash** | str | 内容指纹，用于 diff 判断是否变更 |
| **current_version** | int | 当前版本号 |
| updated_at | datetime | |

### `outline_versions` — 大纲版本历史（级联引擎依赖）
> 每次改大纲存一个快照，支撑"改动 diff"和"回溯"。
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| outline_id | FK | |
| version | int | |
| snapshot | JSON | 该版本完整大纲内容 |
| change_type | enum | **minor**（小改）/ **major**（大改） |
| change_summary | text | LLM 生成的改动摘要 |
| created_at | datetime | |

---

## 二、章节正文

### `chapters` — 章节正文
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | FK | |
| outline_id | FK | 对应大纲 |
| chapter_number | int | |
| draft_content | text | 草稿 |
| final_content | text | 定稿 |
| word_count | int | |
| **outline_version_used** | int | **生成时基于的大纲版本号** |
| **is_stale** | bool | **大纲改了但正文没重写 → true（失配标记）** |
| status | enum | empty / drafting / drafted / finalized / stale |
| created_at / updated_at | datetime | |

> `is_stale` 是关键：大纲级联引擎发现某章大纲变了、但正文还是旧版本生成的，
> 就把这一章标 stale，前端红点提醒"正文与新大纲不符，是否重写？"

---

## 三、时序故事圣经（借鉴 knowrite Temporal Truth DB + graphify 图谱）

> 核心思想：**事实不是静态的，而是带"有效章节区间"的**。
> 查询"第 N 章时角色 X 状态如何"，只返回 valid_from ≤ N ≤ valid_until 的事实。

### `entities` — 实体（角色/地点/物品/势力，知识图谱的"节点"）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | FK | |
| entity_type | enum | character / location / item / faction |
| name | str | |
| aliases | JSON | 别名（防止 LLM 换称呼认不出） |
| base_profile | JSON | 基础档案（角色：外貌/性格/初始能力等） |

### `facts` — 时序事实（Temporal Truth，系统心脏）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | FK | |
| entity_id | FK | 该事实属于哪个实体 |
| fact_type | enum | state / ability / possession / relationship / location |
| content | text | 事实内容（如"左手截肢"） |
| **valid_from** | int | 从第几章起成立 |
| **valid_until** | int? | 到第几章失效（null = 一直有效） |
| importance | enum | critical / major / minor |
| source_chapter | int | 由哪章的正文抽取而来 |
| created_at | datetime | |

> 例：角色A第5章受伤 → fact(valid_from=5, valid_until=11)；第12章痊愈 →
> 新 fact(valid_from=12, valid_until=null)。查第8章 → 命中"受伤"。

### `relationships` — 关系边（知识图谱的"边"）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | FK | |
| from_entity_id | FK | |
| to_entity_id | FK | |
| relation | str | 如"师徒""仇敌""恋人" |
| valid_from | int | 关系带时序（关系会变） |
| valid_until | int? | |

### `knowledge_states` — 谁知道什么（借鉴 KazKozDev 读者/角色已知分离）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | FK | |
| fact_id | FK | 针对哪条事实 |
| knower | str | "reader" 或 角色 entity_id |
| known_from_chapter | int | 从第几章起知道 |
| knower_state | enum | known / suspected / blind |

> 写悬疑必备：同一真相，读者第3章就知道、但角色B到第10章才知道。
> 生成时据此控制"这个角色现在不该说出他还不知道的事"。

---

## 四、伏笔调度（借鉴 NovelClaw 四态 + KazKozDev 揭示调度）

### `foreshadowings` — 伏笔
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | FK | |
| description | text | 伏笔内容 |
| chapter_planted | int | 埋设章节 |
| **expected_payoff_chapter** | int? | 预期回收章 |
| **earliest_payoff_chapter** | int? | 最早不能早于（KazKozDev minimumChapter） |
| status | enum | **planted / reinforced / paid_off / abandoned** |
| payoff_chapter | int? | 实际回收章 |
| reinforcement_chapters | JSON | 强化出现的章节列表 |
| importance | enum | critical / major / minor |
| required_hints | JSON | 回收前需要的前置铺垫 |
| notes | text | |

> 调度规则（借鉴 NovelClaw）：`status in (planted, reinforced)` 且
> `expected_payoff_chapter <= 当前章+2` → 进入"该回收"提醒列表，
> 生成该章时注入 prompt："以下伏笔应在近期回收：…"。

---

## 五、倾向预设（你的标签系统，详见 04 文档）

### `tendency_presets` — 用户存的倾向模板
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| name | str | 如"我的爽文模板" |
| scope | enum | outline / chapter / polish |
| tags | JSON | 标签组合（含自定义输入） |
| is_builtin | bool | 内置 or 用户自建 |

---

## 六、向量库（Chroma，与关系库并行）

不进 SQL，独立存于 Chroma，**6 个 collection 对应 6 桶**（借鉴 NovelClaw）：

| collection | 存什么 | 基础权重 |
|---|---|---|
| texts | 章节正文分段 | 0.58 |
| outlines | 大纲/滚动摘要 | 0.88 |
| characters | 角色相关描写 | 0.82 |
| world_settings | 世界观设定 | 0.80 |
| plot_points | 情节点 | 0.76 |
| fact_cards | 事实卡（timeline/foreshadowing/relationship） | 0.78 |

每条记录 metadata 带 `chapter_number`，检索时可按章节窗口过滤 + 权重加权排序。

---

## 七、数据流转关系图

```
生成一章正文的数据流：
  outline(本章) + architecture
      + 故事圣经查询（相关 entity 在"当前章"的 facts）
      + 分桶记忆检索（characters/world/plot 桶，按角色/场景召回）
      + 伏笔调度（该回收的 foreshadowings）
      + 倾向拼装（global_tendency + 本次临时标签）
   → 拼装 Prompt → LLM 草稿 → 定稿
   → 存 chapters.final_content
   → 抽取器 extractor 从正文抽新 facts / 更新伏笔状态 / 写回向量库

改一章大纲的数据流（级联引擎）：
  编辑 outline → 存 outline_version（diff 出 change_type）
   → 若 major：impact 分析下游章节依赖
   → 提示用户"第 X-Y 章受影响，是否重生成大纲？"
   → 已有正文的受影响章 → chapters.is_stale = true
```
