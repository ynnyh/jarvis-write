# app/engines/adapt_extract.py
# -*- coding: utf-8 -*-
"""把改编产物摊平成纯文本(供 adapt_audit 核对保真度)。

三条改编线的产物形态各不相同,但「保真度核对」只关心一件事:改编稿里到底
有没有出现原著的关键设定。所以这里做**唯一的格式适配层**——把各线的产物
拼成一段可检索的文字,核对逻辑(adapt_audit)就对格式无感。

为什么单开一个模块而不是塞进 adapt.py:adapt.py 是「喂给模型什么」(输入侧),
本模块是「模型吐出来什么」(输出侧),方向相反;而且它依赖 db 模型的具体列
(剧本正文 / 漫剧台词 JSON),放一起会让 adapt.py 这个叶子长出耦合枝。

边界:纯函数,零 LLM,零 IO。找不到东西就返回空串(核对方据此判「未评估」)。
"""
from __future__ import annotations

from typing import Any

# 剧本正文里的元信息行(Fountain 的标题页字段)。核对保真度时它们是噪声
# ——「Title: 第 1 集」这种字符串既不是剧情也不该算「保住了设定」。
_FOUNTAIN_META_PREFIXES = (
    "title:", "credit:", "author:", "source:", "draft date:", "contact:",
    "title:", "标题:", "作者:", "来源:",
)


def script_episodes_text(episodes: list[Any]) -> str:
    """剧本线各集正文 → 拼接文本。

    只取 ``content``(正文)。标题/梗概/钩子不参与——它们是**规划**而非**稿**,
    拿规划去算保真度等于自己给自己打分。
    """
    parts: list[str] = []
    for ep in episodes or []:
        for line in (getattr(ep, "content", "") or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower().startswith(_FOUNTAIN_META_PREFIXES):
                continue
            parts.append(stripped)
    return "\n".join(parts)


def drama_episodes_text(episodes: list[Any]) -> str:
    """漫剧线各集台词稿 → 拼接文本。

    ``DramaEpisode.script`` 是 JSON(``{lines:[{speaker,text,action}]}``)。
    台词与 action(画面描述)都算——action 里常出现「左臂的血迹」这类细节,
    正是事实落地的地方;speaker 不算(人名单独出现不构成设定落地)。
    """
    parts: list[str] = []
    for ep in episodes or []:
        script = getattr(ep, "script", None)
        if not isinstance(script, dict):
            continue
        lines = script.get("lines")
        if not isinstance(lines, list):
            continue
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            for key in ("text", "action"):
                val = str(ln.get(key) or "").strip()
                if val:
                    parts.append(val)
    return "\n".join(parts)


def clips_text(shots: list[Any]) -> str:
    """情绪短片线的分镜 → 拼接文本(action_desc / dialogue / 提示词)。"""
    parts: list[str] = []
    for shot in shots or []:
        for key in ("action_desc", "dialogue", "prompt_cn"):
            val = str(getattr(shot, key, "") or "").strip()
            if val:
                parts.append(val)
    return "\n".join(parts)
