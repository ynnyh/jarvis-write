# jarvis-write

**一个可控、改得动、不崩的 AI 长篇小说创作系统。**

[English](README_EN.md) | 简体中文

写长篇小说时，AI 工具的头号问题不是"写不出来"，而是写到几十万字后**人设崩、伏笔丢、大纲改不动**。jarvis-write 不是又一个"一键生成器"——生成文字的活交给 LLM，本项目做的是包在 LLM 外面的**控制层**：故事圣经管事实、伏笔调度管回收、大纲级联管改动、倾向标签管风格，让长篇创作全程可控、可改、可追溯。

<!-- 📸 截图/演示 GIF 待补:建议依次放 1) 写作工作台全景 2) 大纲级联改动的下游影响勾选 3) 故事圣经/伏笔看板。把图片放进 docs/assets/ 后，在此处用 <img src="docs/assets/xxx.png" width="820"> 引入即可。 -->

> 💬 **想直接试用、不想自己部署?** 扫码进 QQ 群领**邀请码**，开箱即用 → [见文末交流群](#community)

## ✨ 三个别人没有的杀手锏

市面上的 AI 写作工具大多止步于"生成"，jarvis-write 的价值在于生成之后还**改得动、不崩、你说了算**：

- **🔗 大纲级联更新**——改任意一章大纲，系统自动做改动分级（小改零成本短路）、分析下游影响、你勾选后级联重生成；已有正文自动标记失配，大纲全程版本化可回退。*这一条，同类开源项目里一个都没做。*
- **🧭 长程一致性引擎**——时序故事圣经（每条事实绑生效章节区间，可查"第 N 章时角色是什么状态"）+ 伏笔四态调度（埋设/强化/回收/弃用，到期自动提醒），章后自动抽取实体与事实写回圣经。几十万字不自相矛盾。
- **🎚️ 标签化倾向系统**——风格/节奏/基调不再写死在 Prompt 里：chips + 自定义输入 + 预设模板，贯穿大纲、正文、润色三个节点，全程你说了算。

## 核心特性

- **六步生成流水线**：种子 → 角色动力学 → 世界观 → 情节架构 → 章节蓝图 → 逐章正文（成熟的雪花写作法 Prompt 体系，鸣谢见文末）
- **长程一致性引擎**：时序故事圣经（每条事实绑定生效章节区间，可查"第 N 章时角色状态"）、伏笔四态调度（埋设/强化/回收/弃用，到期自动提醒）、章后自动抽取实体与事实写回圣经
- **逐章生成 + 一致性检查**：定稿后自动与故事圣经比对找矛盾，问题列表交用户拍板，不擅自改稿；内置重复用词检测
- **大纲级联更新**：随时改任意一章大纲，系统自动做改动分级（minor 零成本短路）→ 下游影响分析 → 用户勾选后级联重生成；已有正文自动标记失配，大纲全程版本化可回溯
- **润色引擎**：整章或选段风格化润色，**锁定情节事实**（润色前抽事实清单、润色后逐条校验）；去 AI 味三层机制（常驻规则 + 倾向标签 + 量化检测前后对比）
- **标签化倾向系统**：chips + 自定义输入 + 预设模板，贯穿大纲、正文、润色三个节点，风格/节奏/基调由用户说了算
- **全书阅读器**：主题（纸张/牛皮纸/夜间）、字体、字号可调
- **多用户**：JWT 登录 + 邀请码注册 + 每用户独立配置 LLM key + 数据隔离；移动端已适配
- **导出与统计**：整本导出 txt / epub；token 用量统一埋点、实时统计
- **Docker 一键部署**：单容器，前端产物由 FastAPI 托管，数据卷持久化

## 快速开始

### 方式一：Docker（推荐）

```bash
git clone https://github.com/ynnyh/jarvis-write.git
cd jarvis-write

# 配置必填环境变量（见下方"配置要点"），然后：
docker compose up --build
```

访问 `http://localhost:8000`（端口可用 `PORT` 环境变量覆盖）。SQLite 数据持久化在 named volume `jarvis_write_data`。

### 方式二：本地开发

```bash
# 后端（首次需建 venv、pip install -r requirements.txt、cp .env.example .env 并配 key）
cd backend && python -m app        # http://127.0.0.1:8000

# 前端（另开终端，/api 代理到 8000）
cd frontend && npm install && npm run dev   # http://localhost:5173
```

详细步骤、冒烟测试与目录结构见 [backend/README.md](backend/README.md)。

## 配置要点

| 配置项 | 说明 |
|---|---|
| `JWT_SECRET` | JWT 签名密钥，**必填**，必须设为随机长串（公网部署否则 token 可被伪造）。`APP_ENV=prod` 下仍用弱默认值将**拒绝启动** |
| `ADMIN_PASSWORD` | 初始管理员密码，**必填**（Docker 下无默认值；代码默认值仅限本地开发） |
| `INVITE_CODE` | 注册邀请码：填对才能注册；**留空则关闭注册** |
| LLM API key | 支持 DeepSeek / OpenAI / Gemini。每个账号登录后在**设置页**配自己的 key（存数据库，推荐）；也可用 `.env` 做兜底 |

完整配置项见 [backend/.env.example](backend/.env.example)。

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/00-overview.md](docs/00-overview.md) | 项目愿景、设计思路，以及与同类项目的差异化对比 |
| [docs/01-architecture.md](docs/01-architecture.md) | 系统架构、代码目录结构、技术选型理由 |
| [docs/02-data-model.md](docs/02-data-model.md) | 数据模型：全部表结构、字段、关系 |
| [docs/03-engines.md](docs/03-engines.md) | 三大核心引擎设计：一致性 / 大纲级联 / 润色 |
| [docs/04-tag-system.md](docs/04-tag-system.md) | 标签化倾向系统：chips + 自定义输入 + 预设模板 |
| [docs/05-roadmap.md](docs/05-roadmap.md) | 分阶段落地路线图、验收标准与落地偏差记录 |
| [backend/README.md](backend/README.md) | 后端运行、测试与目录结构细节 |

## 技术栈

- **后端**：Python 3.12 + FastAPI（REST + SSE），SQLAlchemy 2.x + SQLite（可切 Postgres），Pydantic v2
- **LLM 层**：自封适配层（DeepSeek / OpenAI / Gemini，不用 LangChain），任务级模型路由（强模型/快模型分档）
- **前端**：React + TypeScript + Vite
- **部署**：单容器 Docker（多阶段构建，前端产物由 FastAPI 托管在 `/app`）

## 项目状态与路线图

阶段 0–8 已全部完成：生成流水线与倾向拼装器、逐章生成、长程一致性引擎、大纲级联更新引擎、润色引擎、Web 前端工作台、token 统计与 txt/epub 导出、Docker 部署、多用户与移动端适配。每阶段验收结果与实现偏差见 [docs/05-roadmap.md](docs/05-roadmap.md)。

已知遗留项：

- **SSE 逐 token 真流式**：已用"异步任务 + 五段进度轮询"替代，体验达标
- **多模型路由细化**（quality/fast 分 provider）：待接入第二家模型时做成设置页配置

## 测试

```bash
# 后端：接口级 + mock LLM 全链路（独立临时库，不碰开发数据）
cd backend && python -m pytest

# 前端：lint + 构建
cd frontend && npm run lint && npm run build
```

另有按阶段的自检脚本（`backend/scripts/stage*_test.py`），详见 [backend/README.md](backend/README.md)。

<a id="community"></a>

## 🫂 交流群

遇到问题、想要**邀请码试用**、提需求或一起折腾，欢迎进 QQ 群：

<p align="center">
  <img src="docs/assets/qq-group-qr.jpg" alt="jarvis-write QQ 交流群 1006352530" width="240">
</p>

<p align="center"><b>QQ 群：1006352530</b> · 扫码进群，<b>领邀请码免费试用</b></p>

## 🙏 鸣谢

本项目站在许多优秀开源项目的肩上——下面这些能力借鉴了它们的思路，特此致谢（逐个读源码的完整对比见 [docs/00-overview.md](docs/00-overview.md)）：

- **雪花写作法 Prompt 体系** ← [AI_NovelGenerator](https://github.com/YILING0013/AI_NovelGenerator)
- **伏笔四态追踪** ← [NovelClaw](https://github.com/iLearn-Lab/NovelClaw)
- **事实绑章节区间的时序真相库** ← [knowrite](https://github.com/knoai/knowrite)
- **读者/角色已知分离 · 揭示调度 · 重复用词检测** ← [KazKozDev/NovelGenerator](https://github.com/KazKozDev/NovelGenerator)
- **知识图谱式 story bible 组织** ← [graphify-novel](https://github.com/Anshler/graphify-novel)
- **Web 全流程工程化分层** ← [AI-Novel-Writing-Assistant](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)

而**大纲级联更新引擎**、**标签化倾向系统**，以及把这些"零件"整合成一套连贯控制层的工作，是本项目自研的部分。

## License

本项目以 [Apache License 2.0](LICENSE) 开源。Copyright 2026 ynnyh。
