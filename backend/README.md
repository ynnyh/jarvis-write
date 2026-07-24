# jarvis-write · 后端

AI 长篇小说生成系统的后端。重心:长程一致性 / 大纲级联更新 / 可控倾向标签。

设计图纸在仓库根目录 `docs/`,施工顺序见 `docs/05-roadmap.md`。

## 技术栈

- Python 3.12 + FastAPI
- SQLAlchemy 2.x + SQLite(起步,日后可切 Postgres)
- 自封 LLM 适配层(DeepSeek / OpenAI / Gemini,不用 LangChain)

## 快速开始

```bash
cd backend

# 1. 建虚拟环境
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# 2. 装依赖
pip install -r requirements.txt

# 3. 配置
cp .env.example .env
# 编辑 .env:本地开发至少填一个 provider 的 api_key 作兜底;
# 也可起服务后在 /settings 页面给每个账号单独配 key(存数据库,优先级更高)。
# 多用户相关(INVITE_CODE / JWT_SECRET / ADMIN_*)见 .env.example 注释。

# 4. 起服务
python -m app
# 或  uvicorn app.main:app --reload --port 8000
```

服务起来后:

- 接口文档:http://127.0.0.1:8000/docs
- 健康检查:`GET /api/health` → 返回各 provider 是否已配置
- 冒烟测试大模型:`POST /api/ping-llm` → `{"prompt": "你好"}`

## 冒烟测试(不需要真实 API key)

```bash
python scripts/smoke_test.py
```

校验:模块导入、建表、路由注册、LLM 工厂、provider 配置状态。

## 目录结构

```
app/
├── main.py            FastAPI 入口(启动时建表 + 跑 migrate.py 幂等迁移)
├── config.py          配置管理(.env → Settings)
├── auth.py            鉴权:bcrypt 密码 / JWT / 当前用户 contextvar(阶段 8)
├── migrate.py         启动时幂等迁移:补 user_id 列/建初始 admin/存量归 admin(无 Alembic)
├── jobs.py            进程内后台任务(异步生成 + 五段进度轮询)
├── db/
│   ├── base.py        SQLAlchemy Base
│   ├── session.py     引擎 / 会话
│   └── models/        数据模型 15 张表(见 docs/02-data-model.md)
├── engines/
│   ├── pipeline/      生成流水线(雪花架构/章节蓝图/逐章生成)
│   ├── consistency/   长程一致性(时序圣经/伏笔调度/章后抽取/检查/重复用词)
│   ├── cascade/       大纲级联(diff/影响分析/级联重生成)
│   ├── polish/        润色(锁情节/AI 味量化检测)
│   ├── tendency/      倾向拼装(chips → Prompt 指令)
│   └── memory/        向量记忆(Chroma,单桶 store.py;6 桶待 embedding 来源)
├── llm/               LLM 适配层
│   ├── base.py        适配器抽象基类(ask 统一埋点 token 用量)
│   ├── openai_compatible.py  OpenAI 兼容基类(DeepSeek/OpenAI 复用)
│   ├── deepseek.py / openai.py / gemini.py
│   ├── factory.py     create_llm_adapter 工厂(per-user key 经 contextvar 注入)
│   └── router.py      任务级模型路由(强模型/快模型)
├── prompts/           Prompt 模板
├── api/               路由(projects/outline/chapters/consistency/polish/
│                      tendency/auth/settings/inspire/misc/system)
├── schemas/           Pydantic 请求/响应模型
└── static/            设置页(每用户 LLM key 配置)

scripts/               阶段自检脚本(见下"进度")
tests/                 pytest 测试(接口级 + mock LLM 全链路,独立临时库)
```

## 进度

- [x] 阶段 0 · 地基(脚手架 / LLM 适配层 / 数据模型 / 冒烟测试)
- [x] 阶段 1 · 生成流水线 + 倾向拼装器
- [x] 阶段 2 · 逐章生成 + 基础记忆(最近章节 + 滚动摘要)
- [x] 阶段 3 · 长程一致性引擎(时序圣经 / 伏笔调度 / 章后抽取 / 一致性检查)
- [x] 阶段 4 · 大纲级联更新引擎(版本化 / 改动分级 / 影响分析 / 级联重生成)
- [x] 阶段 5 · 润色引擎(锁情节 + 去 AI 味)
- [x] 阶段 6 · Web 前端工作台
- [x] 阶段 7 · 打磨(token 统计 / 异步生成五段进度 / txt·epub 导出 / Docker)
- [x] 阶段 8 · 多用户(JWT + 邀请码 + per-user LLM key + 数据隔离)+ 移动端适配

自动化测试(pytest,接口级 + mock LLM 全链路,临时库不碰开发数据;CI 在 push/PR 时自动跑):

```bash
python -m pytest
```

阶段自检脚本(手动跑;除压测外都 mock LLM,不需要真实 key):

```bash
python scripts/smoke_test.py     # 冒烟:模块导入/建表/路由/LLM 工厂
python scripts/stage1_test.py    # 阶段 1~5 全链路自检(stage2~5 同理)
python -m scripts.stage_p0_test  # P0 回归:重写章节后圣经/伏笔/摘要回滚重建
python -m scripts.stress20       # 20 章真实压测(需真实 key,报告写 scripts/stress_report.jsonl)
```

详见 `docs/05-roadmap.md`。
