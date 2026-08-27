# ADR-0001: 用 Alembic 管理数据库迁移

- 状态：✅ 已采纳
- 日期：2026-08-26
- 决策者：项目维护者

## 背景

项目早期使用 `Base.metadata.create_all()` + 手写的 `app/migrate.py` 做数据库 schema 管理。`migrate.py` 里累积了 30+ 个 `_add_xxx_column()` 幂等补列函数，以及多个数据迁移逻辑（admin 账号创建、orphan 数据归属、LLM key 加密、provider_settings→configs 表迁移等），文件达到 822 行（35KB）。

随着表数量增长到 30+ 张，手写迁移的问题越来越明显：
- 每次加列都要写一个新的 `_add_xxx_column()` 函数，重复劳动
- 迁移历史不可追溯，不知道某个列是什么时候、为什么加的
- 不支持 downgrade，出问题只能手动修
- 未来切 Postgres 时，手写的 SQLite 专用 SQL 可能不兼容

## 选项

### 选项 A：继续用手写 migrate.py

- 优点：零迁移成本，现有逻辑不动
- 缺点：上述问题持续存在，文件继续膨胀

### 选项 B：完全替换为 Alembic，把所有历史迁移转成 Alembic 脚本

- 优点：干净利落，统一用 Alembic
- 缺点：风险高，现有用户数据库的 schema 与模型可能有细微差异，自动生成的迁移可能出错；数据迁移逻辑（如 key 加密）难以用 Alembic 表达

### 选项 C（采纳）：渐进式引入 Alembic

- 用 Alembic 管理**未来**的 schema 变更
- 生成一个基线迁移（baseline）代表当前全量表结构
- 现有用户数据库首次启动时自动 stamp 到基线版本（不重复建表）
- `migrate.py` 保留为 legacy 数据迁移（admin 创建、key 加密等），在 Alembic upgrade 之后运行
- 未来的 schema 变更全部走 `alembic revision --autogenerate`

## 决策

采纳选项 C：渐进式引入 Alembic。

理由：
1. 风险最低——现有用户数据不受影响，stamp 逻辑保证不重复建表
2. 未来收益明确——新变更走 Alembic，有版本历史、支持 downgrade、兼容 Postgres
3. 数据迁移逻辑保留在 migrate.py，不强行用 Alembic 表达不适合的东西
4. 桌面版 PyInstaller 打包通过 datas 把 alembic.ini 和迁移脚本打入，运行时从 _MEIPASS 解析

## 后果

### 正面
- 新 schema 变更只需 `alembic revision --autogenerate -m "描述"`，不再手写补列函数
- 迁移历史可追溯，每个变更有版本号和描述
- 支持 `alembic downgrade`，出问题可回退
- 为未来切 Postgres 打下基础（Alembic 支持多数据库方言）

### 负面
- 新增依赖 `alembic`（含 Mako），打包体积略有增加
- 开发者需要学习 Alembic 的基本用法（已在 CONTRIBUTING.md 中说明）
- 迁移脚本需要被 PyInstaller 打包，desktop.spec 已更新

### 需注意
- 现有 `migrate.py` 里的 schema 补列函数不再新增，但保留用于现有用户的幂等迁移
- 首次启动时，如果检测到没有 alembic_version 表但有业务表，会自动 stamp 到基线版本
- Alembic 失败时不阻断启动，回退到 create_all + legacy migrate 作为兜底
