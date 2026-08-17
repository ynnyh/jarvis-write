# 功能导览

面向使用者的功能介绍。技术细节见各篇[设计文档](/00-overview)。

## 以章节为中心的写作工作台

界面按「你在写书，不是在走流水线」组织成三个区：

- **开书 setup**：概念 → 架构 → 大纲，保留向导感，一步步把书立起来
- **写作 write**：三栏工作台——左侧**章节轨**是唯一选章入口（搜索、状态筛选、每章操作），中间**正文**永远是视觉重心，右侧**参考抽屉**默认折叠成图标条，点开看蓝图 / 人物 / 伏笔 / 世界观规则 / 审核报告 / 历史版本；底部**动作条**一键发起生成本章 / 重写 / 润色 / 校对 / 评分 / 连写，结果卡上直接挂「下一步动作」（评分 → 按建议重写，校对 → 修复所选）
- **全书 book**：看板（概览 / 人物 / 圣经 / 伏笔）、投稿导出、全书体检、翻新批量工具

另有书级设置（字数守卫 / 审校把关 / 连写要求 / 世界观硬规则）和只读阅读页。

<p align="center">
  <img src="./assets/screenshots/01-workbench.png" alt="写作工作台" width="860">
</p>
<p align="center"><i>写作工作台（截图为早期版本，界面已按上述三区结构重构，持续迭代中）</i></p>

设计规格见 [07 · 交互重构：双端设计与实施规格](/07-交互重构-双端设计与实施规格)；新建小说的向导体验见 [09 · 新建小说向导体验升级设计方案](/09-新建小说向导体验升级设计)。

## 命令面板与快捷键

手不离键盘：`Ctrl+K` 唤出命令面板——输入 `37` 跳 37 章，输入章名模糊匹配，输入「润色 / 校对 / 评分」直接执行动作。其余快捷键：`Ctrl+S` 保存正文、`Ctrl+Enter` 生成本章、`Ctrl+[` / `Ctrl+]` 上下章、`Ctrl+B` / `Ctrl+\` 收放左右栏、`F11` 沉浸模式（只留中栏正文）。快捷键在浏览器和桌面客户端里同样生效。

## 长程一致性引擎

写到几十万字不崩的底气：

- **时序故事圣经**：每条事实绑定生效章节区间，可以回答「第 N 章时这个角色是什么状态」
- **伏笔四态调度**：埋设 / 强化 / 回收 / 弃用，到期未回收自动提醒
- **契约与门禁**：章后自动抽取实体与事实写回圣经；定稿后与圣经比对找矛盾，问题列表交你拍板，不擅自改稿

详见 [03 · 三大引擎设计](/03-engines) 与 [08 · 章节生产流水线与前后审核体系设计](/08-章节生产流水线与前后审核体系设计)。

## 大纲级联更新

改任意一章大纲，系统自动做改动分级（小改零成本短路）→ 下游影响分析 → 你勾选后级联重生成；已有正文自动标记失配，大纲全程版本化可回退。同类开源项目里独一份。

<p align="center">
  <img src="./assets/screenshots/demo-cascade.gif" alt="大纲级联更新演示" width="860">
</p>

详见 [03 · 三大引擎设计](/03-engines)。

## 编辑部：评分 / 校对 / 审核

主审按情节 / 文笔 / 节奏 / 人物四维给章节打分，校对抓错字病句，全书体检聚合审核报告。生成时的自动修复清单与手动待修清单留存可查，正文一旦改动自动失效，不留过期误导。审核体系的设计见 [08 · 章节生产流水线与前后审核体系设计](/08-章节生产流水线与前后审核体系设计)。

## 润色与去 AI 味

- **润色锁定情节**：整章或选段风格化润色，润色前抽事实清单、润色后逐条校验，情节事实不变
- **去 AI 味全链路**：生成端——草稿转定稿时，规则检测命中的句子被贴进 prompt 定点改写；润色端——常驻规则 + 倾向标签 + 量化检测前后对比，结果卡 AI 味指数超标可一键去味
- **重写前先聊**：重写前与 AI 多轮对话蒸馏出精准修改意见，再据此重写，避免「AI 猜你想要什么」

详见 [03 · 三大引擎设计](/03-engines) 与 [04 · 标签化倾向系统](/04-tag-system)。

## 已有书翻新

旧书不是废稿：旧章节按新逻辑重构——回填场景节拍、初始化文风备忘、轻度重润（锁情节）或重度重写（整章重跑）；中途失败已完成章进度保留，可续跑剩余章节。详见 [03 · 三大引擎设计](/03-engines)。

## 看板与投稿导出

质感看板带主审四维评分与 AI 味指数，哪章该返工一目了然；人物 / 圣经 / 伏笔各有独立看板。整本可导出 txt / epub 用于投稿，token 用量统一埋点、实时统计。

## 桌面客户端（Windows）

Tauri 壳 + 冻结后端，安装包开箱即用、免登录单机运行、数据落本机。原生菜单栏（文件 / 编辑 / 视图 / 窗口 / 帮助）与前端快捷键共用一套分发；支持开**对照阅读窗**——主窗写当前章，附窗只读看别的章，主窗保存后附窗自动刷新；窗口尺寸位置自动记忆。

## 移动端体验

不做两份代码：同一套组件按断点退化。进来就是当前章正文，底部栏（写 / 读 / 参考 / 设置）+ 右下悬浮按钮（点按执行当前最该做的动作，长按展开动作扇），左右滑动切章，参考与结果以全屏面板弹出。

<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:14px;max-width:780px;margin:16px 0;">
  <img src="./assets/screenshots/mobile-01.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-02.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-03.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-04.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-05.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
</div>
<p><i>移动端截图（界面持续迭代中）</i></p>

## 多用户与自部署

Docker 单容器一键部署；JWT 登录 + 邀请码注册 + 每用户独立配置自己的 LLM key（可存多套命名配置，一键切换强模型档 / 快档）+ 数据隔离。可选应用锁保护本机访问。

| 我的小说首页 | 应用锁 |
|---|---|
| <img src="./assets/screenshots/02-home.png" width="400"> | <img src="./assets/screenshots/03-applock.png" width="400"> |

公网部署的安全要求见 [07 · 公网试用上线安全加固清单](/07-公网试用上线-安全加固清单)，配置项见仓库 [backend/.env.example](https://github.com/ynnyh/jarvis-write/blob/main/backend/.env.example)。
