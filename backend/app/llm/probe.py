# app/llm/probe.py
# -*- coding: utf-8 -*-
"""渠道体检:判断一个模型渠道能不能真正干「中文长篇写作」的活。

背景(2026-09-08 实锤):某中转渠道(ooioo / glm-5.3-flash,背后是 Claude 中继)
「测试连接」秒过、小请求也正常,但真跑管线时把**英文思维链直接吐进正文**——
`Let me carefully analyze this task. I'm reviewing Chapter 39's blueprint …`
结果:JSON 环节解析失败(重试也救不回)、正文环节会被英文开场白污染。
ping 式的「测试连接」测不出这种问题,因为链路确实通、也有正文返回。

体检三项,每一项都对应一类真实踩过的坑:
1. **中文输出** —— 短中文问答,统计 CJK 占比。占比过低说明模型在吐思维链
   或语种漂移,这类渠道写中文长篇必然出问题。
2. **JSON 结构** —— 要求返回固定结构的小 JSON,用与管线同一套宽容解析校验。
   解析不出来 = 所有 JSON 环节(一致性检查/主审/事实抽取/契约)都会降级。
3. **响应速度** —— 按实际输出 token 数算 tok/s。过慢只是提醒(不判不适配):
   慢渠道能跑小任务,但一章要打十几次调用,几十分钟一章用户扛不住。

用法:设置页「测试连接」在连通后自动跑一次体检;也可单独调用 probe_channel。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger("jarvis-write.probe")

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# 中文占比下限:正常中文回答哪怕只一句("一年有四个季节。")也在 0.6 以上;
# 纯英文思维链是 0。取 0.2 是给「中英混排 + 少量术语」留余量,不是宽容线。
_CN_RATIO_MIN = 0.2
# 慢速提醒阈值(tok/s):低于此值写长篇会非常难熬
_SLOW_TOKENS_PER_SEC = 5.0

PROBE_CN_PROMPT = "请用一句简短的中文回答:一年有几个季节?只回答这一句,不要解释。"

PROBE_JSON_PROMPT = (
    "请严格只输出一个 JSON 对象,不要 Markdown 围栏、不要任何解释文字,格式如下:\n"
    '{"season_count": 4, "example": "春天"}\n'
    "要求:example 字段用中文填写。"
)


@dataclass
class CheckResult:
    """一项体检结果。warning=True 表示只提醒、不影响「是否适配」判定。"""

    name: str
    passed: bool
    detail: str = ""
    sample: str = ""
    warning: bool = False


@dataclass
class ChannelProbe:
    ok: bool  # 至少拿到过一次回复(链路 + 鉴权是通的)
    checks: list[CheckResult] = field(default_factory=list)
    seconds: float = 0.0
    tokens_per_second: float = 0.0

    @property
    def suitable(self) -> bool:
        """是否适配中文长篇写作:所有「硬项」全过(提醒项不参与)。"""
        hard = [c for c in self.checks if not c.warning]
        return bool(hard) and all(c.passed for c in hard)

    def failed_names(self) -> list[str]:
        return [c.name for c in self.checks if not c.passed and not c.warning]

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "suitable": self.suitable,
            "seconds": round(self.seconds, 2),
            "tokens_per_second": round(self.tokens_per_second, 2),
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "detail": c.detail,
                    "sample": c.sample,
                    "warning": c.warning,
                }
                for c in self.checks
            ],
        }


def cjk_ratio(text: str) -> float:
    """中文字符占非空白字符的比例(0~1)。"""
    chars = [c for c in (text or "") if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _CJK.match(c)) / len(chars)


async def probe_channel(adapter) -> ChannelProbe:
    """对适配器做一轮体检。异常不抛,全部折成 failed 的 check。"""
    from app.engines.common import parse_llm_json_checked

    started = time.perf_counter()
    checks: list[CheckResult] = []
    tokens = 0
    ok = False

    # ---- 1) 中文输出 ----
    try:
        resp = await adapter.complete(adapter.to_messages(PROBE_CN_PROMPT))
        ok = True
        tokens += resp.completion_tokens or 0
        text = (resp.content or "").strip()
        ratio = cjk_ratio(text)
        checks.append(
            CheckResult(
                name="中文输出",
                passed=ratio >= _CN_RATIO_MIN,
                detail=(
                    f"中文字符占比 {ratio:.0%}"
                    if ratio >= _CN_RATIO_MIN
                    else f"中文字符占比仅 {ratio:.0%}——回复不像中文"
                    "(多为模型把英文思维链直接吐进正文,会污染 JSON 环节与正文)"
                ),
                sample=text[:80],
            )
        )
    except Exception as exc:  # noqa: BLE001 — 体检不该抛,折成失败的检查项
        checks.append(
            CheckResult(name="中文输出", passed=False, detail=f"调用失败:{_short(exc)}")
        )
        return ChannelProbe(ok=False, checks=checks, seconds=time.perf_counter() - started)

    # ---- 2) JSON 结构 ----
    try:
        resp = await adapter.complete(adapter.to_messages(PROBE_JSON_PROMPT))
        tokens += resp.completion_tokens or 0
        raw = (resp.content or "").strip()
        data, err = parse_llm_json_checked(raw)
        if err:
            checks.append(
                CheckResult(
                    name="JSON 结构",
                    passed=False,
                    detail=f"按管线同款解析失败({err})——一致性/主审/抽取等环节会全线降级",
                    sample=raw[:80],
                )
            )
        elif not {"season_count", "example"} <= set(data):
            checks.append(
                CheckResult(
                    name="JSON 结构",
                    passed=False,
                    detail=f"解析成功但字段缺失(得到 {sorted(data)})",
                    sample=raw[:80],
                )
            )
        else:
            checks.append(
                CheckResult(name="JSON 结构", passed=True, detail="固定结构可稳定解析")
            )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            CheckResult(name="JSON 结构", passed=False, detail=f"调用失败:{_short(exc)}")
        )

    # ---- 3) 响应速度(仅提醒) ----
    seconds = time.perf_counter() - started
    tps = (tokens / seconds) if seconds > 0 and tokens > 0 else 0.0
    if tps and tps < _SLOW_TOKENS_PER_SEC:
        checks.append(
            CheckResult(
                name="响应速度",
                passed=False,
                warning=True,
                detail=f"约 {tps:.1f} tok/s,偏慢——一章需十几次调用,会很煎熬",
            )
        )
    elif tps:
        checks.append(
            CheckResult(name="响应速度", passed=True, warning=True, detail=f"约 {tps:.1f} tok/s")
        )

    return ChannelProbe(ok=ok, checks=checks, seconds=seconds, tokens_per_second=tps)


def _short(exc: BaseException) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:120] or type(exc).__name__
