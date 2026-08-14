1	# jarvis-write
2	
3	**一个可控、改得动、不崩的 AI 长篇小说创作系统。**
4	
5	[English](README_EN.md) | 简体中文
6	
7	写长篇小说时，AI 工具的头号问题不是"写不出来"，而是写到几十万字后**人设崩、伏笔丢、大纲改不动**。jarvis-write 不是又一个"一键生成器"——生成文字的活交给 LLM，本项目做的是包在 LLM 外面的**控制层**：故事圣经管事实、伏笔调度管回收、大纲级联管改动、倾向标签管风格，让长篇创作全程可控、可改、可追溯。
8	
9	<p align="center">
10	  <img src="docs/assets/screenshots/01-workbench.png" alt="写作工作台" width="820">
11	</p>
12	<p align="center"><i>写作工作台：左侧六步流水线导航 + 章节地图（审核状态、字数一览），右侧逐章生成 / 阅读 / 重写</i></p>
13	
14	**🎬 30 秒看懂「大纲级联更新」全流程：**

<p align="center">
  <img src="docs/assets/screenshots/demo-cascade.gif" alt="大纲级联更新演示" width="820">
</p>
15	
16	<details>
17	<summary>📸 更多截图（桌面版 / 移动端）</summary>
18	
19	| 首页 · 我的小说 | 应用锁 |
20	|---|---|
21	| <img src="docs/assets/screenshots/02-home.png" width="400"> | <img src="docs/assets/screenshots/03-applock.png" width="400"> |
22	
23	移动端已适配，手机浏览器打开即用：
24	
25	<p>
26	  <img src="docs/assets/screenshots/mobile-01.jpg" width="180">
27	  <img src="docs/assets/screenshots/mobile-02.jpg" width="180">
28	  <img src="docs/assets/screenshots/mobile-03.jpg" width="180">
29	  <img src="docs/assets/screenshots/mobile-04.jpg" width="180">
30	</p>
31	
32	</details>
33	
34	> 🖥️ **下载桌面版（Windows 安装包，开箱即用）** → [GitHub Releases](https://github.com/ynnyh/jarvis-write/releases/latest)
35	>
36	> 🌐 **官网与在线文档** → [ynnyh.github.io/jarvis-write](https://ynnyh.github.io/jarvis-write/)
37	>
38	> 💬 **想直接试用、不想自己部署?** 扫码进 QQ 群领**邀请码**，开箱即用 → [见文末交流群](#community)
39	
40	## ✨ 三个别人没有的杀手锏
41	
42	市面上的 AI 写作工具大多止步于"生成"，jarvis-write 的价值在于生成之后还**改得动、不崩、你说了算**：
43	
44	- **🔗 大纲级联更新**——改任意一章大纲，系统自动做改动分级（小改零成本短路）、分析下游影响、你勾选后级联重生成；已有正文自动标记失配，大纲全程版本化可回退。*这一条，同类开源项目里一个都没做。*
45	- **🧭 长程一致性引擎**——时序故事圣经（每条事实绑生效章节区间，可查"第 N 章时角色是什么状态"）+ 伏笔四态调度（埋设/强化/回收/弃用，到期自动提醒），章后自动抽取实体与事实写回圣经。几十万字不自相矛盾。
46	- **🎚️ 标签化倾向系统**——风格/节奏/基调不再写死在 Prompt 里：chips + 自定义输入 + 预设模板，贯穿大纲、正文、润色三个节点，全程你说了算。
47	
48	## 核心特性
49	
50	- **六步生成流水线**：种子 → 角色动力学 → 世界观 → 情节架构 → 章节蓝图 → 逐章正文（成熟的雪花写作法 Prompt 体系，鸣谢见文末）
51	- **长程一致性引擎**：时序故事圣经（每条事实绑定生效章节区间，可查"第 N 章时角色状态"）、伏笔四态调度（埋设/强化/回收/弃用，到期自动提醒）、章后自动抽取实体与事实写回圣经
52	- **逐章生成 + 一致性检查**：定稿后自动与故事圣经比对找矛盾，问题列表交用户拍板，不擅自改稿；内置重复用词检测
53	- **大纲级联更新**：随时改任意一章大纲，系统自动做改动分级（minor 零成本短路）→ 下游影响分析 → 用户勾选后级联重生成；已有正文自动标记失配，大纲全程版本化可回溯
54	- **润色引擎**：整章或选段风格化润色，**锁定情节事实**（润色前抽事实清单、润色后逐条校验）；去 AI 味三层机制（常驻规则 + 倾向标签 + 量化检测前后对比）
55	- **场景节拍 + 文风备忘**：章节蓝图带 3-5 个场景节拍，正文按节拍铺场景不再松散；文风备忘随书自动累积（调性/人物声音/复现意象），注入后续章节，长篇后段不变味；不同任务自动分档采样温度
56	- **已有书翻新引擎**：旧章节按新逻辑重构——回填节拍、初始化文风备忘、轻度重润（锁情节）/ 重度重写（整章重跑）；中途失败已完成章进度保留，可续跑剩余章节
57	- **质感看板**：章节地图带主审四维评分与 AI 味指数，哪章该返工一目了然
58	- **标签化倾向系统**：chips + 自定义输入 + 预设模板，贯穿大纲、正文、润色三个节点，风格/节奏/基调由用户说了算
59	- **创作偏好档案**：文风 / 禁忌 / 读者定位 / 其他主张结构化沉淀为项目级档案，作为最高优先级约束贯穿所有生成环节；研讨中的主张可一键吸收沉淀，已生成的老书打开即自动从正文反向提炼启用
60	- **字数守卫与自动拆章**：定稿超标自动压缩重写，严重超标自动拆章（LLM 选断点 + 全表编号顺移 + 圣经/摘要重建），结构改动与正文同事务原子落地，中途崩溃不损坏正文
61	- **编辑部回显**：主审 / 校对结果留存，生成时的自动修复清单与手动待修清单打开即见，正文一旦改动自动失效，不留过期误导
62	- **重写前先聊**：重写前与 AI 多轮对话蒸馏出精准修改意见，再据此重写，避免"AI 猜你想要什么"
63	- **全书阅读器**：主题（纸张/牛皮纸/夜间）、字体、字号可调；段落级 AI 问答与润色，选中即问、采纳即替换
64	- **多用户**：JWT 登录 + 邀请码注册 + 每用户独立配置 LLM key + 数据隔离；移动端已适配
65	- **导出与统计**：整本导出 txt / epub；token 用量统一埋点、实时统计
66	- **Docker 一键部署**：单容器，前端产物由 FastAPI 托管，数据卷持久化
67	- **桌面版（Windows）**：安装包开箱即用，免登录单机运行、数据落本机；由 GitHub Actions 自动构建并发布到 Releases
68	
69	## 快速开始
70	
71	### 方式一：桌面版安装包（最简单 · Windows）
72	
73	从 [GitHub Releases](https://github.com/ynnyh/jarvis-write/releases/latest) 下载最新的 `jarvis-write_<版本>_x64-setup.exe`，双击安装即可。免登录单机运行，作品数据存在本机（`%APPDATA%\jarvis-write`），无需部署、无需配置数据库。首次打开后在「模型设置」里填上你自己的 LLM API key 即可开始创作。
74	
75	### 方式二：Docker（自部署多用户服务）
76	
77	```bash
78	git clone https://github.com/ynnyh/jarvis-write.git
79	cd jarvis-write
80	
81	# 配置必填环境变量（见下方"配置要点"），然后：
82	docker compose up --build
83	```
84	
85	访问 `http://localhost:8000`（端口可用 `PORT` 环境变量覆盖）。SQLite 数据持久化在 named volume `jarvis_write_data`。
86	
87	### 方式三：本地开发
88	
89	```bash
90	# 后端（首次需建 venv、pip install -r requirements.txt、cp .env.example .env 并配 key）
91	cd backend && python -m app        # http://127.0.0.1:8000
92	
93	# 前端（另开终端，/api 代理到 8000）
94	cd frontend && npm install && npm run dev   # http://localhost:5173
95	```
96	
97	详细步骤、冒烟测试与目录结构见 [backend/README.md](backend/README.md)。
98	
99	## 配置要点
100	
101	| 配置项 | 说明 |
102	|---|---|
103	| `JWT_SECRET` | JWT 签名密钥，**必填**，必须设为随机长串（公网部署否则 token 可被伪造）。`APP_ENV=prod` 下仍用弱默认值将**拒绝启动** |
104	| `ADMIN_PASSWORD` | 初始管理员密码，**必填**（Docker 下无默认值；代码默认值仅限本地开发） |
105	| `INVITE_CODE` | 注册邀请码：填对才能注册；**留空则关闭注册** |
106	| LLM API key | 支持 DeepSeek / OpenAI / Gemini 及任意 OpenAI 兼容中转站。每个账号登录后在**设置页**配自己的 key——可存多套命名配置，一键切换默认（强模型档）/快档（存数据库，推荐）；也可用 `.env` 做兜底 |
107	
108	完整配置项见 [backend/.env.example](backend/.env.example)。
109	
110	## 文档索引
111	
112	> 🌐 在线文档站（含全部设计文档 + 本地搜索）：[ynnyh.github.io/jarvis-write](https://ynnyh.github.io/jarvis-write/)
113	
114	| 文档 | 内容 |
115	|---|---|
116	| [docs/00-overview.md](docs/00-overview.md) | 项目愿景、设计思路，以及与同类项目的差异化对比 |
117	| [docs/01-architecture.md](docs/01-architecture.md) | 系统架构、代码目录结构、技术选型理由 |
118	| [docs/02-data-model.md](docs/02-data-model.md) | 数据模型：全部表结构、字段、关系 |
119	| [docs/03-engines.md](docs/03-engines.md) | 核心引擎设计：一致性 / 大纲级联 / 润色 / 翻新 + 生成质量增强 |
120	| [docs/04-tag-system.md](docs/04-tag-system.md) | 标签化倾向系统：chips + 自定义输入 + 预设模板 |
121	| [docs/05-roadmap.md](docs/05-roadmap.md) | 分阶段落地路线图、验收标准与落地偏差记录 |
122	| [backend/README.md](backend/README.md) | 后端运行、测试与目录结构细节 |
123	
124	## 技术栈
125	
126	- **后端**：Python 3.12 + FastAPI（REST + SSE），SQLAlchemy 2.x + SQLite（可切 Postgres），Pydantic v2
127	- **LLM 层**：自封适配层（DeepSeek / OpenAI / Gemini，不用 LangChain），任务级模型路由（强模型/快模型分档，各选一套配置），cc-switch 风格多配置管理，瞬时错误自动重试 + 流式聚合兜底（防中转站 CDN 掐断长请求）
128	- **前端**：React + TypeScript + Vite
129	- **部署**：单容器 Docker（多阶段构建，前端产物由 FastAPI 托管在 `/app`）
130	- **桌面版**：Tauri 2 壳 + PyInstaller 冻结后端，GitHub Actions 自动构建 NSIS 安装包并发布到 Releases
131	
132	## 项目状态与路线图
133	
134	阶段 0–8 已全部完成：生成流水线与倾向拼装器、逐章生成、长程一致性引擎、大纲级联更新引擎、润色引擎、Web 前端工作台、token 统计与 txt/epub 导出、Docker 部署、多用户与移动端适配。每阶段验收结果与实现偏差见 [docs/05-roadmap.md](docs/05-roadmap.md)。
135	
136	已知遗留项：
137	
138	- **SSE 逐 token 真流式**：已用"异步任务 + 五段进度轮询"替代，体验达标
139	
140	## 测试
141	
142	```bash
143	# 后端：接口级 + mock LLM 全链路（独立临时库，不碰开发数据）
144	cd backend && python -m pytest
145	
146	# 前端：lint + 构建
147	cd frontend && npm run lint && npm run build
148	```
149	
150	另有按阶段的自检脚本（`backend/scripts/stage*_test.py`），详见 [backend/README.md](backend/README.md)。
151	
152	<a id="community"></a>
153	
154	## 🫂 交流群
155	
156	遇到问题、想要**邀请码试用**、提需求或一起折腾，欢迎进 QQ 群：
157	
158	<p align="center">
159	  <img src="docs/assets/qq-group-qr.jpg" alt="jarvis-write QQ 交流群 1006352530" width="240">
160	</p>
161	
162	<p align="center"><b>QQ 群：1006352530</b> · 扫码进群，<b>领邀请码免费试用</b></p>
163	
164	## 🙏 鸣谢
165	
166	本项目站在许多优秀开源项目的肩上——下面这些能力借鉴了它们的思路，特此致谢（逐个读源码的完整对比见 [docs/00-overview.md](docs/00-overview.md)）：
167	
168	- **雪花写作法 Prompt 体系** ← [AI_NovelGenerator](https://github.com/YILING0013/AI_NovelGenerator)
169	- **伏笔四态追踪** ← [NovelClaw](https://github.com/iLearn-Lab/NovelClaw)
170	- **事实绑章节区间的时序真相库** ← [knowrite](https://github.com/knoai/knowrite)
171	- **读者/角色已知分离 · 揭示调度 · 重复用词检测** ← [KazKozDev/NovelGenerator](https://github.com/KazKozDev/NovelGenerator)
172	- **知识图谱式 story bible 组织** ← [graphify-novel](https://github.com/Anshler/graphify-novel)
173	- **Web 全流程工程化分层** ← [AI-Novel-Writing-Assistant](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)
174	
175	而**大纲级联更新引擎**、**标签化倾向系统**，以及把这些"零件"整合成一套连贯控制层的工作，是本项目自研的部分。
176	
177	## License
178	
179	本项目以 [Apache License 2.0](LICENSE) 开源。Copyright 2026 ynnyh。
180	