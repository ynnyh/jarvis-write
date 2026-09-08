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

## Prompt 改动守则（评测门禁）

`app/prompts/` 下的模板与判据直接决定生成质量，改前改后必须过一遍评测底座，不能只凭手感：

```bash
cd backend

# 1. 动手前先留底：跑一次 baseline 并记下 JSON 路径
python -m app.evals run --fixture po_feng_ji --label before

# 2. 改 prompt 模板（prompt_registry 会自动算内容指纹，不用手写版本号）

# 3. 改完再跑一次
python -m app.evals run --fixture po_feng_ji --label after

# 4. 出对比表
python -m app.evals compare <before.json> <after.json>

# 5. 过回归门槛(不达标非 0 退出,与 CI 同款判定)
python -m app.evals gate <after.json>
```

判定标准分两层：

**硬门槛**（`python -m app.evals gate`，越界即失败，可直接挂 CI）：

- blocker 数 = 0、隔离章 ≤ 1 —— 这是「不崩」的底线，带硬矛盾的章节一律不许流出；
- 达标率 ≥ 0.8；
- AI 味指数 ≤ 6.0、章内复读 ≤ 5；
- **事实抽取数 ≥ 30** —— 掉了说明章后抽取在静默降级，故事圣经停止生长，
  后续章的一致性对照会悄悄失去事实源（最危险的一类退化，界面上却看不出来）；
- 篇幅比 0.7–1.4（字数守卫失效会在这露出来）。

**参考项**（不设硬门槛，交人判断）：

- 主审四维 plot / prose / pacing / character 与连贯分。这四项是 **LLM 自评**，
  且默认配置下审校档与生成档指向同一个模型（自审自写），方差大、乐观偏差明显。
  只能看趋势，不能当门禁。要真正的审校分离，在设置页给审校档配一套独立模型
  （`review_chapter` 会返回 `self_review` 标记提示当前是否处在自审状态）。

门槛定义在 `app/evals/thresholds.py`：**放宽门槛等于承认能力退化**，改它必须在
PR 里写明理由。判别力由 `tests/test_evals_gate.py` 守着——它把几种典型退化
（AI 味飙升 / blocker 泄漏 / 抽取静默降级 / 复读暴增 / 字数守卫失效）逐个注入
真实基线，断言门槛确实拦得住。指标对退化不敏感的门槛，等于没有门槛。

注意事项：

- 评测会在独立 SQLite 库里新建 `[评测]` 前缀项目，**不会碰你自己的书库**；但如果传了 `--db` 指向真库，非前缀项目存在时会拒绝执行。
- 评测跑真模型会耗 token：先用 `--chapters 2` 冒烟确认链路通，再跑全 10 章；单次对比只看趋势，下结论前同一配置至少跑两次看方差。
- 夹具与报告范例见 `app/evals/fixtures/po_feng_ji.json` 与 `app/evals/examples/`。

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
