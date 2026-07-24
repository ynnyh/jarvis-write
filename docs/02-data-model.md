# 02 · 数据模型设计（最关键，重点保存）

> 这份是整个系统的骨架。三大引擎都靠这些表运转。字段设计吸收了调研中
> knowrite（时序真相库）、NovelClaw（分桶记忆/伏笔四态）、KazKozDev
> （读者已知/角色已知分离）的做法。
>
> 当前共 15 张表：主体 11 张 + 阶段 2 的 `chapter_summaries`、阶段 7 的
> `llm_usage`、阶段 8 的 `users` / `provider_settings`（见下）。

---

## 一、项目与大纲

### `projects` — 小说项目
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| user_id | FK → users? | 归属用户（阶段 8 数据隔离）；存量数据迁移时归 admin |
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

### `chapter_summaries` — 滚动前情摘要（阶段 2 新增）
> 第 N 章的行存的是「截至第 N 章的完整前情摘要」，每章定稿后把剧情合并压缩进来；
> 生成第 N+1 章时取 chapter_number=N 的行注入上下文。
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| project_id | FK → projects | 级联删除 |
| chapter_number | int | 摘要覆盖到第几章 |
| rolling_summary | text | 截至该章的合并压缩摘要 |
| created_at / updated_at | datetime | |

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

## 六、用量与多用户（阶段 7/8 新增）

### `llm_usage` — LLM 用量记录（阶段 7）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| user_id | int? | 记账归属：哪个用户烧的 token（NULL = 多用户迁移前的历史记录） |
| model | str | 实际调用的模型 |
| prompt_tokens / completion_tokens | int | 本次调用的输入/输出 token |
| created_at / updated_at | datetime | |

> `llm/base.ask` 统一埋点，所有生成链路自动记账；`GET /api/usage` 汇总，
> 前端顶栏实时显示累计用量。

### `users` — 用户账号（阶段 8）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| username | str | 唯一 |
| password_hash | str | bcrypt 哈希，不存明文 |
| is_admin | bool | 初始 admin 由启动迁移（migrate.py）自动创建 |
| created_at / updated_at | datetime | |

> 数据隔离：`projects.user_id` / `provider_settings.user_id` / `llm_usage.user_id`
> 均按用户过滤，跨账号访问返回 404。

### `provider_settings` — 每用户 LLM provider 配置（阶段 8 起 per-user）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | PK | |
| user_id | FK → users | 每用户 × provider 一行（唯一约束 `uq_provider_per_user`） |
| provider | str | deepseek / openai / gemini |
| api_key / base_url / model | str | 该用户自己的 key 与接入点，在站点设置页配置 |
| is_default | bool | 该用户的默认 provider |
| created_at / updated_at | datetime | |

> 优先级：数据库里当前用户的配置 > .env / 环境变量（.env 仅作开发兜底）。

---

## 七、长程记忆（无向量库）

> 早期设计规划过 Chroma 向量库 + 6 桶加权记忆，但因 embedding 来源始终不稳（中转站
> `/embeddings` 返 403），且现代模型上下文窗口已足够长，该方案已整体移除。长程一致性
> 现由**时序故事圣经**（结构化事实，见第三节）+**滚动摘要**（`chapter_summaries`）+
> **最近章节正文尾部**三者承担，全部落在 SQL 里，不再有独立向量存储。

---

## 八、数据流转关系图

```
生成一章正文的数据流：
  outline(本章) + architecture
      + 故事圣经查询（相关 entity 在"当前章"的 facts）
      + 最近章节结尾 + 滚动前情摘要（直接上下文）
      + 伏笔调度（该回收的 foreshadowings）
      + 倾向拼装（global_tendency + 本次临时标签）
   → 拼装 Prompt → LLM 草稿 → 定稿
   → 存 chapters.final_content
   → 抽取器 extractor 从正文抽新 facts / 更新伏笔状态（写回故事圣经）

改一章大纲的数据流（级联引擎）：
  编辑 outline → 存 outline_version（diff 出 change_type）
   → 若 major：impact 分析下游章节依赖
   → 提示用户"第 X-Y 章受影响，是否重生成大纲？"
   → 已有正文的受影响章 → chapters.is_stale = true
```
