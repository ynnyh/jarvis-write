# 00 · 项目愿景与调研对比

> 注:本文为早期调研/设计文档,记录立项时的思路与竞品对比,不逐条对齐当前实现。其中"分桶加权记忆/向量检索"等语义记忆能力已在实现中移除,长程一致性改由「时序故事圣经 + 最近章节 + 滚动摘要」承担。竞品拆解表中对其他项目的描述为客观事实,予以保留。

## 一、为什么做这个

现有 AI 小说工具（包括 5.6k star 的 AI_NovelGenerator）本质是"一键生成器"：输入主题 → 吐出一本能看的小说。但写长篇时有三个头号痛点它们都没解好：

1. **长程一致性崩坏**：写到几十章，人设、伏笔、世界观开始自相矛盾。上下文窗口塞不下前文，靠"最近几章 + 向量检索"只能缓解不能根治。
2. **大纲改不动**：大纲基本"生成即冻结"。改了第 5 章，第 6 章还挂着旧设定——牵一发不能动全身。
3. **生成倾向不可控**：写作风格/节奏/基调被写死在 Prompt 里，用户没法选。

我们要做的是**控制层**——生成文字交给 LLM，我们负责"让它改得动、不崩、可控"。

## 二、开源调研对比（基于实际读源码，非 README 自述）

我扒了 8 个同类项目的真实实现，每个都有值得借鉴的"零件"，但没有一个整体命中三诉求。

| 项目 | Star | 栈 | 值得借鉴的硬货 | 短板 |
|---|---|---|---|---|
| [AI_NovelGenerator](https://github.com/YILING0013/AI_NovelGenerator) | 5.6k | Python/tkinter | **雪花写作法 Prompt** 最成熟（种子→角色弧光→世界观→情节→章蓝图） | 老 GUI、纯 txt、单向量库、倾向写死 |
| [NovelClaw](https://github.com/iLearn-Lab/NovelClaw) | 347 | Python | **分桶加权记忆**（6 类记忆各有权重）+ **伏笔四态追踪器**（埋/强化/回收/弃用，自动提醒该回收） | 多 Agent 偏重、无大纲级联 |
| [KazKozDev/NovelGenerator](https://github.com/KazKozDev/NovelGenerator) | 138 | TS | **读者已知 vs 角色已知分离** + **揭示调度**（伏笔绑目标章/最早章/前置铺垫）+ **重复用词检测** | 纯前端、Gemini 单一、无持久化 |
| [knowrite](https://github.com/knoai/knowrite) | 16 | TS | **时序真相库**：每条事实绑 validFrom/validUntil 章节区间，可查"第 N 章时角色状态" | 早期、star 少 |
| [graphify-novel](https://github.com/Anshler/graphify-novel) | 53 | 提示词 | **知识图谱式 story bible**（节点=角色/地点/物品，边=关系） | 不是代码，是 SKILL 提示词 |
| [AI-Novel-Writing-Assistant](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant) | 2k | TS | **Web 全流程工程化**、API 分层清晰 | TS 栈、重、无级联 |
| novel-bot / pulpgen | 30/16 | Python | 轻量思路，无独特硬货 | — |

## 三、关键结论

> **你的三大诉求，在这些项目里能找到"零件"，但没人把它们拼成一台车。**

| 诉求 | 谁做了零件 | 完整度 |
|---|---|---|
| 长程一致性 | NovelClaw 分桶记忆 + knowrite 时序真相库 + KazKozDev 读者/角色已知分离 | 零件齐，**没人整合** |
| 伏笔不烂尾 | NovelClaw 四态追踪 + KazKozDev 揭示调度 | 零件好，**可直接借鉴** |
| **大纲级联更新** | **没有一个项目做** | **全空白** |
| 标签化倾向 | 都写死 | **基本空白** |

## 四、开发策略：站在巨人肩膀上

**直接借鉴（不重复造）：**
- 雪花写作法 Prompt ← AI_NovelGenerator
- 伏笔四态追踪 ← NovelClaw `TurningPointTracker`
- 时序真相库（事实绑章节区间）← knowrite
- 读者/角色已知分离 + 揭示调度 ← KazKozDev
- 知识图谱式 bible 组织 ← graphify
- Web 前后端分层 ← AI-Novel-Writing-Assistant

**必须自研（真正价值所在）：**
1. 把上述零件**整合成一套连贯的控制层**（没人做过）
2. **大纲级联更新引擎**（全网空白，核心刚需）
3. **标签化倾向系统**（chips + 我要输入 + 存预设）

## 五、用户四大诉求 → 对应模块

| 诉求 | 对应设计 | 文档 |
|---|---|---|
| ① 一致性，上下文连得起来 | 长程一致性引擎（三支柱） | [03](03-engines.md) |
| ② 随时改大纲 + 级联更新 | 大纲级联更新引擎 | [03](03-engines.md) |
| ③ 文笔润色 | 润色引擎（锁情节改文笔） | [03](03-engines.md) |
| ④ 标签化倾向选择 | 倾向系统（chips + 自定义 + 预设） | [04](04-tag-system.md) |
