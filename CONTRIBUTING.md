# 贡献指南

感谢你对 jarvis-write 的兴趣！欢迎各种形式的贡献——代码、文档、bug 报告、功能建议、使用经验分享都可以。

## 开始之前

- 请先阅读 [README](README.md) 了解项目定位和功能
- 查看 [docs/](docs/) 下的设计文档，了解架构和实现思路
- 搜索已有的 [Issues](https://github.com/ynnyh/jarvis-write/issues) 和 [PR](https://github.com/ynnyh/jarvis-write/pulls)，避免重复工作

## 开发环境搭建

### 后端

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env  # 然后编辑 .env 填入你的 LLM API key

# 启动后端
python -m app
# 访问 http://127.0.0.1:8000/docs 查看 API 文档
```

### 前端

```bash
cd frontend
npm install
npm run dev
# 访问 http://localhost:5173
```

### 数据库迁移

项目使用 Alembic 管理数据库迁移：

```bash
cd backend

# 应用所有待执行的迁移
alembic upgrade head

# 根据模型变更自动生成新迁移
alembic revision --autogenerate -m "描述本次变更"

# 回退到上一个迁移
alembic downgrade -1

# 查看当前版本
alembic current
```

> 注意：现有用户的数据库会在首次启动时自动 stamp 到基线版本，无需手动操作。

## 代码规范

### 后端（Python）

- Python 3.12+，使用 `from __future__ import annotations`
- 遵循项目现有风格，中文注释，文件头一行说明 + 关键块讲清「为什么这么写」
- LLM 调用一律走 `adapter.ask()` / `ask_messages()`，不要裸调 `complete()`
- 新接口挂 `dependencies=[Depends(get_current_user)]`，取单条记录必须 `assert_project_owner`
- 长任务走 `jobs.spawn_job` + `list_running` 去重，worker 里自己开 `SessionLocal`
- 改已有表的列必须在 Alembic 迁移里写（不再往 `migrate.py` 里加 `_add_xxx_column`）

### 前端（React + TypeScript）

- TypeScript 严格模式，避免 `any`
- 表单一律用 `.form-grid` / `.field` / `.form-actions` 骨架
- 能复用就别手写：复制按钮用 `ui/copy` 的 `CopyBtn`，空态用 `ui/EmptyState`，确认框用 `ui/ConfirmDialog`
- 新样式手写进 `styles.css`，只用现有令牌（`--sp-*` / `--fs-*` / 语义色），不引入 Tailwind/shadcn
- 应用外壳是「左侧全局导航 + 右侧内容」，新页面入口进 `ui/Sidebar.tsx` 的 `ENTRIES`

### 引擎分层约定

- 三条出片线（漫剧 / 宣传片 / 情绪短片 / 生日祝福）的确定性共用件放 `app/engines/media/`
- `media/` 是叶子，不许反向 import 任一条出片线
- 三条线之间也不许互相 import
- 这些约定由 `tests/test_engine_conventions.py` 自动检查

## 提交前检查

每次改动收尾必跑，全绿才算完：

```bash
# 后端
cd backend && python -m pytest -q

# 前端
cd frontend && npx tsc --noEmit && npx eslint . && npx vitest run

# e2e 冒烟(真浏览器,含 390px 窄屏;改了 UI/接口必跑)
cd frontend && npm run e2e
```

其中 `frontend/src/test/uiConventions.test.ts` 是版面公约门禁，`backend/tests/test_engine_conventions.py` 是引擎分层门禁，被拦住时只有两条出路：整改，或者证明判据本身写错了——别加豁免名单。

## UI 功能的「完成定义」三查

单测管逻辑，管不住「渲染出来长什么样、交互顺不顺」——概念页引擎卡排版、生成按钮无忙态这两个 bug 都是全绿之后用户肉眼发现的。所以凡是动了 UI 的改动，合入前三查是完成定义的一部分：

1. **窄屏查**：浏览器切 390px 宽（或 `npm run e2e` 的 mobile 项目）过一遍改动页面——文字不溢出、卡片有边界、按钮点得到；
2. **暗色查**：切暗色主题再看一眼——没有硬编码色号残留（走 CSS 令牌就自动跟随）；
3. **状态查**：加载中/成功/失败三个态都见过——按钮有忙态、报错有人话提示、不会卡死在中间态。

三查各半分钟，省的是用户截图来报 bug 的一晚上。

## 提交信息

遵循 [Conventional Commits](https://www.conventionalcommits.org/) 风格：

```
feat: 新增生日祝福工坊
fix: 修复桌面版启动端口占用竞态
refactor: 拆分 api/drama.py 为多模块
docs: 更新架构文档中的迁移说明
test: 补充 Alembic 迁移测试
chore: 升级依赖版本
```

## Pull Request 流程

1. Fork 仓库，创建特性分支（`git checkout -b feature/my-feature`）
2. 提交改动，确保本地测试通过
3. 推送分支，创建 PR
4. 填写 PR 模板，描述变更内容和测试方式
5. 等待 review，根据反馈修改
6. 合并后删除分支

## 其他

- 有问题可以进 QQ 群（1006352530）交流
- 大型功能建议先开 Issue 讨论，避免方向偏差
- 文档翻译、错别字修正、使用教程都是非常欢迎的贡献
