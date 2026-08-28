# app/engines/render/emotion.py
# -*- coding: utf-8 -*-
"""对白链的配音情绪:8 档情绪 → indextts2 情感权重向量。

情绪是「演奏指示」:手选、默认平静,不进拆分镜 prompt 让模型猜(人拍板
比模型猜准,改一格不用重拆整集)。权重走单主情绪制——主情绪 0.8、其余 0,
比把一句话拆成情绪鸡尾酒更可控,出的不像就换一档重 roll,几分钱的事。

emo_control_method 的枚举值以平台「在线调用」页实测为准;如果平台改了文案,
改 EMO_CONTROL_WEIGHTS 这一个常量即可。
"""
from __future__ import annotations

# 情绪 key(存 drama_shots.emotion)→ 中文标签(前端下拉共用这份,别另写一份)
EMOTIONS: dict[str, str] = {
    "calm": "平静",
    "happy": "开心",
    "angry": "愤怒",
    "sad": "悲伤",
    "afraid": "惊恐",
    "disgusted": "厌恶",
    "surprised": "惊讶",
    "melancholic": "忧郁",
}
DEFAULT_EMOTION = "calm"

# indextts2 的 emo_control_method 取值(权重模式;平台文案若变只改这里)
EMO_CONTROL_WEIGHTS = "使用情感权重"

# 单主情绪的权重档
_EMOTION_WEIGHT = 0.8
_CALM_WEIGHT = 0.6


def normalize_emotion(label: str) -> str:
    """脏值收敛:白名单外的(含空串)一律回落默认「平静」。"""
    v = (label or "").strip().lower()
    return v if v in EMOTIONS else DEFAULT_EMOTION


def emotion_weights(label: str) -> dict:
    """情绪标签 → indextts2 提交参数里的情感字段集(直接并入工作流参数)。"""
    emo = normalize_emotion(label)
    weights: dict[str, float] = {f"emo_{k}": 0.0 for k in EMOTIONS if k != "calm"}
    weights["emo_calm"] = _CALM_WEIGHT
    weights["emo_random"] = False
    if emo != "calm":
        weights[f"emo_{emo}"] = _EMOTION_WEIGHT
        weights["emo_calm"] = round(1.0 - _EMOTION_WEIGHT, 2)  # 情绪浓一点,平静垫底
    weights["emo_control_method"] = EMO_CONTROL_WEIGHTS
    return weights
