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
      text: 功能导览
      link: /features
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
    details: 整章或选段风格化润色，润色前抽事实清单、润色后逐条校验，锁定情节事实；去 AI 味贯穿生成与润色全链路——草稿转定稿时检测命中句定点改写，结果卡 AI 味指数超标可一键去味。
  - icon: ♻️
    title: 已有书翻新
    details: 旧章节按新逻辑重构：回填场景节拍、生成文风备忘、轻度重润或重度重写；章节带节拍铺场景、文风备忘随书累积；中途失败进度保留，可续跑剩余章节。
  - icon: 📖
    title: 全书阅读器
    details: 主题（纸张/牛皮纸/夜间）、字体、字号可调；段落级 AI 问答与润色，整本导出 txt / epub，token 用量实时统计。
---

## 直击现场

写作以章节为中心：左侧章节轨管选章与状态，中间正文永远是视觉重心，右侧参考抽屉放蓝图/人物/伏笔/审核报告，底部动作条一键发起生成、重写、润色、校对、评分。

<p align="center">
  <img src="./assets/screenshots/01-workbench.png" alt="写作工作台" width="860">
</p>
<p align="center"><i>写作工作台（截图为早期版本，界面已按「以章节为中心」重构，持续迭代中）</i></p>

改大纲不再是大工程。改任意一章，系统自动分级改动、分析下游影响、勾选后级联重生成：

<p align="center">
  <img src="./assets/screenshots/demo-cascade.gif" alt="大纲级联更新演示" width="860">
</p>
<p align="center"><i>大纲级联更新全流程演示（30 秒）</i></p>

## 移动端

手机浏览器打开即用：进来就是当前章正文，底部栏 + 悬浮动作按钮，左右滑动切章，参考与结果以全屏面板弹出。

<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;max-width:600px;margin:16px 0;">
  <img src="./assets/screenshots/mobile-01.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-02.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-03.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-04.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-05.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-06.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-07.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-08.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
  <img src="./assets/screenshots/mobile-09.jpg" style="width:100%;margin:0;border-radius:12px;border:1px solid var(--vp-c-divider);box-shadow:0 2px 10px rgba(0,0,0,.08);">
</div>
<p><i>移动端截图（界面持续迭代中）</i></p>

## 三种使用方式

**1. 桌面安装包（Windows，最简单）**

从 [GitHub Releases](https://github.com/ynnyh/jarvis-write/releases/latest) 下载 `jarvis-write_<版本>_x64-setup.exe`，双击安装即可。免登录单机运行，数据落本机，首次打开填上自己的 LLM API key 就能写。

**2. Docker 自部署（多用户服务）**

```bash
git clone https://github.com/ynnyh/jarvis-write.git
cd jarvis-write
docker compose up --build
```

访问 `http://localhost:8000`，JWT 登录 + 邀请码注册 + 每用户独立 LLM key + 数据隔离。配置项见 [backend/.env.example](https://github.com/ynnyh/jarvis-write/blob/main/backend/.env.example)。

**3. 不想部署，直接试用**

扫码进 QQ 群领**邀请码**，开箱即用：

<p>
  <img src="./assets/qq-group-qr.jpg" alt="jarvis-write QQ 交流群 1006352530" width="200">
</p>
<p><b>QQ 群：1006352530</b> · 扫码进群，领邀请码免费试用</p>

## 继续了解

- [功能导览](/features)——按真实功能逐条介绍：写作工作台、命令面板、一致性引擎、编辑部、去 AI 味、桌面端与移动端体验
- [00 · 项目愿景与调研对比](/00-overview)——为什么做、和同类项目差在哪
- [03 · 三大引擎设计](/03-engines)——一致性 / 大纲级联 / 润色 / 翻新的技术细节
- [07 · 交互重构：双端设计与实施规格](/07-交互重构-双端设计与实施规格)——三区信息架构、快捷键、桌面客户端与移动端设计
