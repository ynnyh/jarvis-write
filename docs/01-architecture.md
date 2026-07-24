# 01 · 系统架构与技术选型

> 说明:向量库 / 分桶加权记忆已于 2026-07 移除(embedding 来源长期不可用、且长上下文已使其非必需)。长程一致性现由「时序故事圣经 + 滚动摘要」承担,下文架构图与选型表已相应更新。

## 一、总体架构

```
┌──────────────── Web 前端 (React + Vite + TS) ────────────────┐
│  ① 标签化倾向选择器（chips + 我要输入，贯穿大纲/正文/润色）      │
│  ② 大纲编辑器（可随时改，改动触发级联影响分析）                 │
│  ③ 逐章生成（SSE 流式输出）                                    │
│  ④ 一致性看板（角色状态时间线 / 伏笔回收进度 / 大纲-正文同步）    │
│  ⑤ 润色工作台（整章 or 选段，选风格，锁情节）                   │
└───────────────────────────┬─────────────────────────────────┘
                            │ REST + SSE
┌───────────────────────────┴─────────────────────────────────┐
│  FastAPI 后端                                                 │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ A. 生成流水线（借鉴雪花写作法）                        │    │
│  │    种子 → 角色动力学 → 世界观 → 情节架构 → 章节蓝图 → 逐章│  │
│  ├─────────────────────────────────────────────────────┤    │
│  │ B. ★ 长程一致性引擎（双支柱）                          │    │
│  │    · 时序故事圣经（事实绑章节区间）                     │    │
│  │    · 伏笔调度器（四态 + 回收提醒）                      │    │
│  ├─────────────────────────────────────────────────────┤    │
│  │ C. ★ 大纲级联更新引擎（改一处，下游影响分析 + 对齐）     │    │
│  ├─────────────────────────────────────────────────────┤    │
│  │ D. 润色引擎（风格化改写，锁定情节事实）                 │    │
│  ├─────────────────────────────────────────────────────┤    │
│  │ E. 倾向 Prompt 拼装器（标签 → 写作指令注入）            │    │
│  ├─────────────────────────────────────────────────────┤    │
│  │ F. LLM 适配层（DeepSeek / OpenAI / Gemini + 模型路由）  │    │
│  └─────────────────────────────────────────────────────┘    │
└──────────────┬──────────────────────────────┬───────────────┘
               │
     ┌─────────┴──────────┐
     │ 关系库（硬事实）    │
     │ SQLite → Postgres  │
     │ 故事圣经/大纲版本/  │
     │ 伏笔/章节元数据/    │
     │ 滚动摘要            │
     └────────────────────┘
```

## 二、技术选型与理由

| 层 | 选型 | 理由 |
|---|---|---|
| 后端框架 | Python + **FastAPI** | 原生 async + SSE 流式，适合 LLM 长任务；用户指定 Python |
| LLM 编排 | **自封适配层**（不用 LangChain） | 更可控；参考项目用 LangChain 反而变重 |
| 结构化存储 | **SQLite**（起步）→ Postgres | 存故事圣经、大纲版本、伏笔表；SQLite 零配置先跑通 |
| ORM | **SQLAlchemy 2.x** | create_all + 手写幂等迁移（`app/migrate.py`），未引入 Alembic；方便日后切 Postgres |
| 数据校验 | **Pydantic v2** | FastAPI 原生集成，LLM 结构化输出校验 |
| 前端 | **React + Vite + TypeScript** | 生态成熟，标签组件/看板好做 |
| 前端状态 | TanStack Query + Zustand | 服务端状态 + 客户端状态分离 |
| 流式 | **SSE**（Server-Sent Events） | 逐字输出生成过程；比 WebSocket 简单 |
| 部署 | Docker Compose | 单容器,后端 + 前端一键起 |

## 三、后端目录结构（规划）

```
backend/
├── app/
│   ├── main.py                  # FastAPI 入口
│   ├── config.py                # 配置管理（API keys / base_url / 模型路由）
│   ├── db/
│   │   ├── base.py              # SQLAlchemy 基础
│   │   ├── session.py           # DB session
│   │   └── models/              # 数据模型（见 02-data-model.md）
│   │       ├── project.py       # 小说项目
│   │       ├── outline.py       # 大纲 + 版本
│   │       ├── chapter.py       # 章节 + 正文
│   │       ├── story_bible.py   # 时序故事圣经（事实/角色/地点/物品）
│   │       ├── foreshadowing.py # 伏笔调度
│   │       └── preset.py        # 倾向预设
│   ├── llm/
│   │   ├── base.py              # LLM 适配器抽象基类
│   │   ├── deepseek.py          # DeepSeek 适配
│   │   ├── openai.py            # OpenAI 适配
│   │   ├── gemini.py            # Gemini 适配
│   │   ├── factory.py           # create_llm_adapter 工厂
│   │   └── router.py            # 任务级模型路由（强模型/快模型）
│   ├── engines/
│   │   ├── pipeline/            # A. 生成流水线
│   │   │   ├── architecture.py  # 种子/角色/世界观/情节
│   │   │   ├── blueprint.py     # 章节蓝图
│   │   │   └── chapter.py       # 逐章生成
│   │   ├── consistency/         # B. 长程一致性引擎
│   │   │   ├── bible.py         # 时序故事圣经（查询/更新）
│   │   │   ├── foreshadow.py    # 伏笔调度器
│   │   │   ├── extractor.py     # 章节后状态/事实抽取
│   │   │   └── checker.py       # 一致性校验
│   │   ├── cascade/             # C. 大纲级联更新引擎
│   │   │   ├── differ.py        # 大纲改动 diff
│   │   │   ├── impact.py        # 下游影响分析
│   │   │   └── regenerate.py    # 级联重生成
│   │   ├── polish/              # D. 润色引擎
│   │   │   └── polisher.py
│   │   └── tendency/            # E. 倾向拼装器
│   │       ├── catalog.py       # 内置标签目录
│   │       └── assembler.py     # 标签 → Prompt 片段
│   ├── prompts/                 # 所有 Prompt 模板（借鉴雪花写作法）
│   ├── api/                     # 路由
│   │   ├── projects.py
│   │   ├── outline.py
│   │   ├── chapters.py
│   │   ├── consistency.py
│   │   ├── polish.py
│   │   └── tendency.py
│   └── schemas/                 # Pydantic 请求/响应模型
├── scripts/                     # 阶段自检脚本（smoke_test / stage1~5_test / ...）
├── requirements.txt
└── Dockerfile

> 实际未引入 Alembic：启动时 `create_all` + `app/migrate.py` 幂等迁移（补列/建 admin）。

frontend/
├── src/
│   ├── api/                     # 对应后端路由的客户端
│   ├── components/
│   │   ├── TendencySelector/    # ① chips + 我要输入
│   │   ├── OutlineEditor/       # ② 大纲编辑 + 级联提示
│   │   ├── ChapterStream/       # ③ 流式生成
│   │   ├── ConsistencyBoard/    # ④ 一致性看板
│   │   └── PolishWorkbench/     # ⑤ 润色台
│   ├── pages/
│   ├── store/
│   └── main.tsx
├── package.json
└── Dockerfile
```

## 四、模型路由策略（借鉴 AI_NovelGenerator）

不同任务用不同模型平衡成本与质量：

| 任务 | 模型档位 | 理由 |
|---|---|---|
| 架构生成（种子/世界观/情节） | 强模型 | 定基调，质量优先 |
| 章节蓝图 | 强模型 | 结构关键 |
| 正文草稿 | 快模型 | 量大，成本优先 |
| 章节摘要/事实抽取 | 快模型 | 结构化任务，快模型够用 |
| 定稿/润色 | 强模型 | 直接影响成品质量 |
| 一致性校验 | 快/中模型 | 找矛盾，够用即可 |

路由表存在配置里，用户可覆盖。
