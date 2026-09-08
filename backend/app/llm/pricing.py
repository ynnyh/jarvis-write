# app/llm/pricing.py
"""模型价格表与成本估算(¥/百万 token)。

口径说明:
- 只内置有把握的官方牌价(2026-08,DeepSeek 官网),其余模型一律返回 None
  ——宁可不算钱,不编价格;
- DeepSeek 官方按「缓存命中/未命中/输出」三档计价,且峰时(北京时间
  9:00-12:00、14:00-18:00)全项 ×2。llm_usage 只记了 prompt/completion
  两个总数,没有缓存命中明细,所以累计金额按「未命中底价」估算——这是
  上界,实际账单(有缓存命中)只会更低,界面上如实标注;
- 峰时判断给到 API 层做「现在花得多还是省」的提示,不回溯历史金额。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# ¥/百万 token。model 按前缀匹配(取最长前缀命中)。
# 价格若有更新,改 _PRICE_BY_PREFIX 即可;新模型加一行就能在用量面板折算金额。
# 只内置有把握的官方牌价(2026-08),没有把握的不编价、不算钱。


@dataclass(frozen=True)
class ModelPrice:
    """单模型三档牌价(¥/百万 token)。cache_hit 可为 0 表示无缓存档。"""

    cache_hit: float  # 输入(缓存命中)
    input: float  # 输入(未命中)
    output: float  # 输出
    peak_multiplier: float = 2.0  # 峰时全项倍率;1.0 = 无峰时定价
    currency: str = "¥"


# 官方牌价(2026-08):v4-flash 三档 0.02 / 1 / 2,峰时 ×2
_PRICE_BY_PREFIX: tuple[tuple[str, ModelPrice], ...] = (
    ("deepseek-v4-flash", ModelPrice(cache_hit=0.02, input=1.0, output=2.0)),
)


def match_price(model: str) -> ModelPrice | None:
    """按模型名前缀匹配牌价;没有把握的价格一律 None(不算钱只算 token)。"""
    name = (model or "").strip().lower()
    for prefix, price in _PRICE_BY_PREFIX:
        if name == prefix or name.startswith(prefix):
            return price
    return None


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    peak: bool = False,
) -> float | None:
    """按牌价估算一笔用量的金额(¥)。无牌价模型返回 None。

    prompt_tokens 全按「未命中」档算——llm_usage 没记缓存命中明细,
    这是上界估计,真实账单只会更低。
    """
    price = match_price(model)
    if price is None:
        return None
    mult = price.peak_multiplier if peak else 1.0
    cost = (
        prompt_tokens * price.input + completion_tokens * price.output
    ) / 1_000_000
    return round(cost * mult, 4)


# 峰时窗口(北京时间,含头不含尾):9:00-12:00、14:00-18:00
_PEAK_WINDOWS: tuple[tuple[int, int], ...] = ((9, 12), (14, 18))
_TZ_CN = timezone(timedelta(hours=8))


def beijing_hour(dt: datetime | None = None) -> int:
    """取北京时间小时(0-23)。不依赖服务器本地时区。"""
    now = dt.astimezone(_TZ_CN) if dt is not None else datetime.now(_TZ_CN)
    return now.hour


def is_peak_now(dt: datetime | None = None) -> bool:
    """当前(北京时间)是否处于 DeepSeek 官方峰时定价窗口。"""
    h = beijing_hour(dt)
    return any(lo <= h < hi for lo, hi in _PEAK_WINDOWS)


PEAK_WINDOWS_TEXT = "北京时间 9:00-12:00、14:00-18:00"


def peak_note() -> str:
    """给用量面板的峰时提示语。"""
    if is_peak_now():
        return (
            f"当前处于官方峰时定价({PEAK_WINDOWS_TEXT}),牌价 ×2;"
            "错峰(12:00-14:00 或 18:00 后)跑长任务可省一半。"
        )
    return (
        f"当前为官方低峰时段(峰时:{PEAK_WINDOWS_TEXT}),牌价按标准价;"
        "长篇生成等大任务安排在低峰更省。"
    )
