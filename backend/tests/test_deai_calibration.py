# tests/test_deai_calibration.py
# -*- coding: utf-8 -*-
"""sepia 校准原则落地:去味重写的过度矫正检测 + prompt 校准纪律 + 主审叙事架构清单。

借鉴背景(StoryScope 经由 sepia):人类写作各项指标落在中段,把每条 AI 特征都
反着执行会形成新的反向指纹;叙事架构层(主题明说/结局三脚架/情绪单一化/模板转折)
即使措辞干净也能被高精度识别。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.engines.polish import polisher
from app.engines.polish.ai_flavor import FlavorReport
from app.prompts.editorial import REVIEW_PROMPT
from app.prompts.polish import DEAI_REWRITE_PROMPT, _DEAI_RULES


def _report(score: float, burstiness: float | None = None,
            para_cv: float | None = None) -> FlavorReport:
    """构造指定 score/指标的报告(检测逻辑测试不需要真实文本)。"""
    metrics: dict = {"burstiness": burstiness, "para_cv": para_cv}
    return FlavorReport(total_chars=4000, score=score, metrics=metrics)


# ---------- 过度矫正检测(overcorrected) ----------

def test_overcorrected_extreme_burstiness():
    """节奏打碎到夸张(σ/μ>1.6)= 反向指纹。"""
    assert polisher.overcorrected(_report(3.0, burstiness=1.9)) is True


def test_overcorrected_extreme_para_cv():
    """段落剁成满篇一行段(段长变异极端)= 反向指纹。"""
    assert polisher.overcorrected(_report(3.0, para_cv=1.3)) is True


def test_overcorrected_normal_band_passes():
    """中段区间(人类参考带)不误伤。"""
    assert polisher.overcorrected(_report(3.0, burstiness=0.9, para_cv=0.5)) is False


def test_overcorrected_none_metrics_passes():
    """短文本指标缺失(None)不参与判断。"""
    assert polisher.overcorrected(_report(3.0)) is False


def test_self_heal_rejects_overcorrected_rewrite():
    """自愈闭环:重写降分但节奏极端化 → 判过度矫正,丢弃回退。"""
    before = _report(10.0, burstiness=0.3)   # 超标,触发自愈
    after = _report(3.0, burstiness=1.9)     # 降分但矫枉过正
    with patch.object(polisher, "ai_flavor_report", side_effect=[before, after]), \
         patch.object(polisher, "deai_rewrite", return_value="改写稿"):
        text, _b, a = asyncio.run(polisher.deai_self_heal("脏文本"))
    assert text == "脏文本"  # 回退,不采纳反向指纹版本
    assert a is before


def test_self_heal_still_adopts_normal_rewrite():
    """正常降分、节奏落在中段 → 照常采纳(过度矫正不误伤)。"""
    before = _report(10.0, burstiness=0.3)
    after = _report(3.0, burstiness=0.9, para_cv=0.5)
    with patch.object(polisher, "ai_flavor_report", side_effect=[before, after]), \
         patch.object(polisher, "deai_rewrite", return_value="改写稿"):
        text, _b, a = asyncio.run(polisher.deai_self_heal("脏文本"))
    assert text == "改写稿"
    assert a is after


# ---------- prompt 校准纪律(内容回归钉) ----------

def test_deai_rewrite_prompt_has_calibration():
    """定向去味 prompt 必须带校准纪律:只落实诊断单/情绪混用/留平庸句/未点名维度不动。"""
    assert "矫枉过正" in DEAI_REWRITE_PROMPT
    assert "不要把整套去味规则" in DEAI_REWRITE_PROMPT
    assert "她很害怕" in DEAI_REWRITE_PROMPT       # 直陈情绪是人味句,允许保留
    assert "本轮诊断没点到的维度不要主动去动" in DEAI_REWRITE_PROMPT
    # 原有改写要求仍在(没有被校准段挤掉)
    assert "只改上面点出的问题句" in DEAI_REWRITE_PROMPT


def test_deai_rules_keep_show_dont_tell():
    """通用去味规则仍在(校准只是加约束,不放松既有标准)。"""
    assert "Show, don't tell" in _DEAI_RULES
