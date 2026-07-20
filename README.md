# jarvis-write

一个**可控、改得动、不崩**的 AI 长篇小说创作系统。

不是"又一个一键生成器"——生成文字的活交给 LLM 和成熟 Prompt，我们做的是包在外面的**控制层**：让小说写到几十万字时人设不崩、伏笔不丢、大纲改一处下游自动对齐、生成倾向由用户说了算。

## 核心定位

- **技术栈**：Python + FastAPI 后端，React + Vite 前端，前后端分离
- **接入模型**：DeepSeek、OpenAI (GPT)、Gemini
- **工程重心**：长程一致性 + 大纲级联更新 + 标签化倾向选择（这三块是现有开源项目普遍的空白）

## 设计文档索引（开工前必读）

> 这套文档就是"施工图纸"。开工前先读，开工中对照，改设计先改文档。

| 文档 | 内容 |
|---|---|
| [docs/00-overview.md](docs/00-overview.md) | 项目愿景、开源调研对比、我们借鉴谁做了什么、自研价值在哪 |
| [docs/01-architecture.md](docs/01-architecture.md) | 系统架构、代码目录骨架、技术选型理由 |
| [docs/02-data-model.md](docs/02-data-model.md) | **数据模型（最关键的图纸）**：所有表结构、字段、关系 |
| [docs/03-engines.md](docs/03-engines.md) | 三大核心引擎详细设计：一致性引擎 / 大纲级联 / 润色引擎 |
| [docs/04-tag-system.md](docs/04-tag-system.md) | 标签化倾向系统：chips + 自定义输入 + 预设模板 |
| [docs/05-roadmap.md](docs/05-roadmap.md) | 分阶段落地路线图 + 每阶段验收标准（含落地偏差记录） |

## 一句话愿景

> 这批开源项目每个都造了一两个好零件，但没人把"一致性、伏笔、级联、可控倾向"拼成一台完整的车。我们不重造零件（直接借鉴），只做那台车的**底盘和控制系统**。

## 快速开始

本地开发（两个终端）：

```bash
# 后端（首次需建 venv、pip install -r requirements.txt、cp .env.example .env 并配 key）
cd backend && python -m app        # http://127.0.0.1:8000

# 前端（/api 代理到 8000）
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Docker（单容器，前端产物由 FastAPI 托管在 /app）：

```bash
docker compose up --build
# 必须设置环境变量 JWT_SECRET（随机长串）和 ADMIN_PASSWORD（初始管理员密码），
# INVITE_CODE 留空则关闭注册；SQLite/Chroma 数据持久化在 named volume jarvis_data。
```

细节见 [backend/README.md](backend/README.md)。

## 测试与 CI

- 后端:`cd backend && python -m pytest`(接口级 + mock LLM 全链路,用临时库)
- 前端:`cd frontend && npm run lint && npm run build`
- GitHub Actions(`.github/workflows/ci.yml`)在 push/PR 时自动跑以上检查。

## 当前状态

✅ **阶段 0–8 已完成**：核心生成流水线 + 倾向拼装器、长程一致性引擎、大纲级联更新引擎、润色引擎（锁情节 + 去 AI 味）、Web 前端工作台、token 成本统计与 txt/epub 导出、多用户（JWT + 邀请码注册 + per-user LLM key + 数据隔离）、移动端适配。分阶段验收与偏差记录见 [docs/05-roadmap.md](docs/05-roadmap.md)。
