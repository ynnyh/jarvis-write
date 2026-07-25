---
layout: home

hero:
  name: jarvis-write
  text: 可控、改得动、不崩的 AI 长篇小说创作系统
  tagline: 生成文字交给 LLM，本项目做包在它外面的控制层——故事圣经管事实、伏笔调度管回收、大纲级联管改动、倾向标签管风格，让长篇创作全程可控、可改、可追溯。
  actions:
    - theme: brand
      text: 下载桌面版
      link: https://github.com/ynnyh/jarvis-write/releases/latest
    - theme: alt
      text: 阅读设计文档
      link: /00-overview
    - theme: alt
      text: GitHub
      link: https://github.com/ynnyh/jarvis-write

features:
  - icon: 🔗
    title: 大纲级联更新
    details: 改任意一章大纲，系统自动做改动分级（小改零成本短路）、分析下游影响、你勾选后级联重生成；已有正文自动标记失配，大纲全程版本化可回退。同类开源项目里独一份。
  - icon: 🧭
    title: 长程一致性引擎
    details: 时序故事圣经（每条事实绑定生效章节区间，可查“第 N 章时角色是什么状态”）+ 伏笔四态调度（埋设/强化/回收/弃用，到期自动提醒），章后自动抽取实体与事实写回圣经。几十万字不自相矛盾。
  - icon: 🎚️
    title: 标签化倾向系统
    details: 风格/节奏/基调不再写死在 Prompt 里：chips + 自定义输入 + 预设模板，贯穿大纲、正文、润色三个节点，全程你说了算。
  - icon: 🖥️
    title: 桌面版开箱即用
    details: Windows 安装包免登录单机运行，数据落本机；也支持 Docker 一键自部署多用户服务，JWT 登录 + 邀请码 + 数据隔离。
  - icon: ✨
    title: 润色锁定情节
    details: 整章或选段风格化润色，润色前抽事实清单、润色后逐条校验，锁定情节事实；去 AI 味三层机制（常驻规则 + 倾向标签 + 量化检测前后对比）。
  - icon: 📖
    title: 全书阅读器
    details: 主题（纸张/牛皮纸/夜间）、字体、字号可调；段落级 AI 问答与润色，整本导出 txt / epub，token 用量实时统计。
---
