# app/engines/media/dialects.py
# -*- coding: utf-8 -*-
"""出片目标(视频模型方言):镜头卡是模型无关的内部工艺,方言层在拼装时把每张卡
重排成目标站吃得动的结构。工艺吸收自 zenstory-ai/drama-skills 的方言文档(MIT):
不同视频站的提示词结构/参考图挂法/对白写法差异很大,一套中文走天下会打折。

设计口径(docs/21 渲染节点的延伸):
- 卡仍是唯一内部表示(画面卡/运动卡/voice/avoid),方言只改「拼装后的每镜块」与文档头;
- 方言格式化是确定性代码,不靠模型自觉——同分镜换目标重拼,内容零漂移;
- 空目标 "" = 通用(现状格式),即梦/可灵等多数站直接可用。
"""
from __future__ import annotations

TARGET_MODELS: dict[str, dict] = {
    "": {
        "key": "", "label": "通用",
        "tip": "一段式中文,即梦/可灵等多数站直接可用",
    },
    "jimeng": {
        "key": "jimeng", "label": "即梦 Seedance",
        "tip": "画面+运动合成一段紧凑动词句,负面词单独贴",
    },
    "kling": {
        "key": "kling", "label": "可灵 Kling",
        "tip": "创意描述与运镜分层,主体动作写连续动词串",
    },
    "minimax-h3": {
        "key": "minimax-h3", "label": "MiniMax H3",
        "tip": "六段结构,支持首帧+定妆多参考图与逐字对白",
    },
}

# 注入镜头卡生成提示词的方言要点(影响 LLM 措辞,不影响卡结构)
DIALECT_CRAFT: dict[str, str] = {
    "jimeng": "出片目标是即梦:画面卡与运动卡措辞都动词开头、一句一个动作,"
              "运镜用「推/拉/摇/移/跟/固定+快慢」的即梦词表,不堆抽象氛围词。",
    "kling": "出片目标是可灵:画面卡里主体动作写成连续动词串(谁-做什么-接着做什么),"
             "镜头运动单独成句,别织进动作描述里。",
    "minimax-h3": "出片目标是 MiniMax H3:运动卡写成时序分解(先…再…最后…),"
                  "一句不超过一个动作;对白语速按中文每秒 4 个可发声字。",
}


def valid_target(key: str) -> bool:
    return key in TARGET_MODELS


def target_meta() -> list[dict]:
    return [
        {"key": v["key"], "label": v["label"], "tip": v["tip"]}
        for v in TARGET_MODELS.values()
    ]


def dialect_usage_lines(target: str) -> list[str]:
    """方言使用说明(进文档头,告诉用户每镜怎么贴/怎么挂图)。"""
    if target == "jimeng":
        return ["出片目标:即梦——每镜把「提示词」整段贴进图生视频(首帧挂参考图),负面词贴到负面框。"]
    if target == "kling":
        return ["出片目标:可灵——「创意描述」贴正文框,「运镜」贴运镜控制项,负面词贴负面框。"]
    if target == "minimax-h3":
        return [
            "出片目标:MiniMax H3——每镜的六段结构一次性整块提交,不可拆开分次贴;",
            "参考图先挂首帧(<Picture 1>),多角色同框可在其后依次加挂定妆照(<Picture 2>…);",
            "台词只出现在 <d>[Chinese]…</d> 内,逐字保留不改编。",
        ]
    return []


def pacing_line(shots: list[dict]) -> str:
    """节奏体检(P2 轻量):近景类占比/平均镜长/固定运镜占比,离谱时给一句提示。

    口径来自 drama-skills 的节奏档案思路:近景类含 近景/中近景/特写/大特写。
    """
    n = len(shots)
    if not n:
        return ""
    close = sum(1 for s in shots if any(k in str(s.get("shot_type") or "") for k in ("近", "特")))
    fixed = sum(1 for s in shots if "固定" in str(s.get("camera") or ""))
    total = sum(int(s.get("duration_s") or 0) for s in shots)
    head = (f"【节奏体检】近景类 {close}/{n} 镜({close * 100 // n}%) · "
            f"平均镜长 {total / n:.1f} 秒 · 固定运镜 {fixed}/{n} 镜({fixed * 100 // n}%)")
    warns: list[str] = []
    if fixed * 100 // n >= 60:
        warns.append("固定运镜过半,回读一遍每镜的切镜职责")
    if close * 100 // n < 30:
        warns.append("近景类偏少,对白落点考虑给到近处")
    return head + ("——" + ";".join(warns) if warns else "")


def format_shot_lines(
    target: str,
    *,
    seq: int, start: int, end: int, shot_type: str, camera: str, duration_s: int,
    ref: str, identity: str, card_cn: str, motion_cn: str,
    dialogue: str, speaker: str, voice: str, sfx: str, negative: str,
) -> list[str]:
    """一张镜头卡 → 目标方言的逐镜块(确定性拼装;"" 目标保持旧格式)。"""
    banner = f"❰第{seq}镜|{start}—{end}秒|{shot_type}|{camera}|首帧:{ref}"
    id_dot = f"{identity}。" if identity else ""
    if target == "jimeng":
        lines = [banner + "|目标:即梦❱", f"提示词:{id_dot}{card_cn}{motion_cn}"]
    elif target == "kling":
        lines = [banner + "|目标:可灵❱", f"创意描述:{id_dot}{card_cn}", f"运镜:{camera};{motion_cn}"]
    elif target == "minimax-h3":
        lines = [
            banner + "|目标:MiniMax H3|挂图:<Picture 1>=首帧❱",
            f"subject_definitions: <Subject 1> 是{identity or '本镜角色'}——"
            "长相与开场站位由 <Picture 1>(首帧)钉死,正文不复述外貌",
            f"summary: 本镜 {duration_s} 秒:{motion_cn}",
            "retention_analysis: <Picture 1>(首帧): fully_preserved——"
            "开场构图、人物长相、站位逐帧保留;台词逐字照读",
            f"detailed_description: [Shot 1] {card_cn} {motion_cn}",
        ]
    else:
        # 通用(现状格式):调用侧不再走这里,保底与旧拼装一致
        lines = [banner + "❱", f"画面卡:{id_dot}{card_cn}", f"运动卡:{motion_cn}"]
    if target == "minimax-h3":
        if dialogue:
            v = f";{voice}" if voice else ""
            lines.append(f'<d>[Chinese]「{dialogue}」({speaker}{v})</d>')
        lines.append(f"overall_soundscape: {sfx or '现场底声与动作同期声'}")
        lines.append("non_diegetic_music: N/A")
    else:
        if dialogue:
            v = f";{voice}" if voice else ""
            lines.append(f"台词:「{dialogue}」({speaker}{v})")
        if sfx:
            lines.append(f"音效:{sfx}")
    lines.append("负面:" + negative)
    return lines
