# app/audit.py
# -*- coding: utf-8 -*-
"""留痕:把「这次生成吃了哪些用户输入」记下来(docs/29 取证 / docs/32 §2.4)。

与 app/usage.py 的分工:
- usage 记**动没动过**(动作计数,内存缓冲 + 30s 批量落库,不记内容);
- 这里记**输入了什么**(生成前的原始输入,随生成落库)——复盘偏失时,输入才是证据。

两条硬规矩:
1. 写入失败**静默**(只告警):留痕的价值远小于生成本身,绝不能拖垮主链路;
2. 别跨 LLM 调用持有请求级 session:长任务里不传 db,让 helper 自己开短会话
   (CLAUDE.md「database is locked」的老根因)。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("jarvis-write.audit")

# 单条值的字符上限:留痕要能复盘,但别变成第二个正文库(正文另有 chapter 表)
_MAX_VALUE_CHARS = 4000
# 列表/字典最多留这么多项,防止把整本书的候选灌进来
_MAX_ITEMS = 50


def _clip(value: Any) -> Any:
    """裁剪长文本;list/dict 只递归一层(生成输入都是浅结构)。"""
    if isinstance(value, str):
        return value if len(value) <= _MAX_VALUE_CHARS else value[:_MAX_VALUE_CHARS] + "…(截断)"
    if isinstance(value, list):
        return [_clip(v) for v in value[:_MAX_ITEMS]]
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in list(value.items())[:_MAX_ITEMS]}
    return value


def record_gen_inputs(
    *,
    kind: str,
    payload: dict[str, Any],
    project_id: int | None = None,
    job_id: str | None = None,
    db: Any = None,
) -> None:
    """记一次生成的输入快照。失败静默(只告警),绝不抛给调用方。

    job_id 不传时自动取当前任务上下文(app.live.current_job_id),这样在
    job worker 里调用无需手工穿参。db 传入则用调用方会话(随本次事务提交);
    不传则自己开短会话——请求路径上有 db 就传,worker 里不传。
    """
    try:
        from app.db.models import GenInput

        if job_id is None:
            from app.live import current_job_id

            job_id = current_job_id.get()
        row = GenInput(
            project_id=project_id,
            job_id=job_id,
            kind=kind,
            payload={k: _clip(v) for k, v in payload.items()},
        )
        if db is not None:
            db.add(row)
            return
        from app.db.session import session_scope

        with session_scope() as own:
            own.add(row)
    except Exception as exc:  # noqa: BLE001 — 留痕绝不影响生成
        logger.warning("生成输入留痕失败(不影响本次生成):%s", exc, exc_info=True)
