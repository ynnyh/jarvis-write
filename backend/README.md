# jarvis-write · 后端

AI 长篇小说生成系统的后端。重心:长程一致性 / 大纲级联更新 / 可控倾向标签。

设计图纸在仓库根目录 `docs/`,施工顺序见 `docs/05-roadmap.md`。

## 技术栈

- Python 3.12 + FastAPI
- SQLAlchemy 2.x + SQLite(起步,日后可切 Postgres)
- 自封 LLM 适配层(DeepSeek / OpenAI / Gemini,不用 LangChain)
- Chroma 向量库(阶段 2 接入)

## 快速开始

```bash
cd backend

# 1. 建虚拟环境
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# 2. 装依赖
pip install -r requirements.txt

# 3. 配置 API key
cp .env.example .env
# 编辑 .env,至少填一个 provider 的 api_key(默认用 deepseek)

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
├── main.py            FastAPI 入口(启动时建表)
├── config.py          配置管理(.env → Settings)
├── db/
│   ├── base.py        SQLAlchemy Base
│   ├── session.py     引擎 / 会话
│   └── models/        数据模型(见 docs/02-data-model.md)
├── llm/               LLM 适配层
│   ├── base.py        适配器抽象基类
│   ├── openai_compatible.py  OpenAI 兼容基类(DeepSeek/OpenAI 复用)
│   ├── deepseek.py / openai.py / gemini.py
│   ├── factory.py     create_llm_adapter 工厂
│   └── router.py      任务级模型路由(强模型/快模型)
├── api/               路由
└── schemas/           Pydantic 请求/响应模型

scripts/smoke_test.py  阶段自检脚本
```

## 进度

- [x] 阶段 0 · 地基(脚手架 / LLM 适配层 / 数据模型 / 冒烟测试)
- [ ] 阶段 1 · 生成流水线 + 倾向拼装器

详见 `docs/05-roadmap.md`。
