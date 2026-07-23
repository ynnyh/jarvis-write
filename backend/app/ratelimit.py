# app/ratelimit.py
# -*- coding: utf-8 -*-
"""极简内存限流中间件:按客户端 IP 对敏感接口(登录/注册)限速。

为什么自研而非 slowapi:部署是单进程 uvicorn(见 Dockerfile),内存计数即够,
不必引 Redis / 新依赖。若日后换多 worker / gunicorn,需改成共享存储(见 docs)。

窗口用固定窗口计数(实现简单、够挡脚本化撞库/刷号);跨窗口边界最坏放行约 2N 次,
对"挡暴力破解/批量注册"这个目的无所谓。时间用 time.monotonic(),不受系统改钟影响。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# 活跃 (规则, IP) 桶超过此数就清一次过期项,防伪造 IP 把内存撑爆
_MAX_BUCKETS = 10_000


@dataclass(frozen=True)
class Rule:
    method: str  # 大写 HTTP 方法
    path: str  # 精确匹配的请求路径
    max_hits: int  # 窗口内最多次数
    window_sec: int  # 窗口秒数


# 敏感接口:登录挡撞库,注册挡批量刷号。阈值宽松,只拦脚本化滥用,不误伤手滑。
DEFAULT_RULES: tuple[Rule, ...] = (
    Rule("POST", "/api/auth/login", max_hits=20, window_sec=300),
    Rule("POST", "/api/auth/register", max_hits=10, window_sec=3600),
)


def client_ip(request: Request) -> str:
    """取真实客户端 IP:反代会把原始 IP 放在 X-Forwarded-For 首位。

    注意:XFF 可被伪造,只有在可信反代(Caddy/Nginx)层设置/覆盖它才可靠;
    裸暴露时退化为 request.client.host(反代/直连 IP),仍能挡单机脚本。
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按 (规则, IP) 固定窗口计数;超限返回 429。仅命中登录/注册,其余零开销。"""

    def __init__(self, app, rules: tuple[Rule, ...] = DEFAULT_RULES) -> None:
        super().__init__(app)
        self._rules = rules
        # (rule_idx, ip) -> [window_start_monotonic, hits]
        self._buckets: dict[tuple[int, str], list[float]] = {}

    def _match(self, request: Request) -> int | None:
        for idx, rule in enumerate(self._rules):
            if request.method == rule.method and request.url.path == rule.path:
                return idx
        return None

    def _prune(self, now: float) -> None:
        """清掉所有已过期窗口(仅在桶数超过上限时惰性触发)。"""
        dead = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket[0] >= self._rules[key[0]].window_sec
        ]
        for key in dead:
            del self._buckets[key]

    async def dispatch(self, request: Request, call_next):
        idx = self._match(request)
        if idx is None:
            return await call_next(request)

        rule = self._rules[idx]
        now = time.monotonic()
        if len(self._buckets) > _MAX_BUCKETS:
            self._prune(now)

        key = (idx, client_ip(request))
        bucket = self._buckets.get(key)
        if bucket is None or now - bucket[0] >= rule.window_sec:
            # 新窗口(过期即重置,同一 IP 的桶不会无限累积)
            self._buckets[key] = [now, 1]
        else:
            bucket[1] += 1
            if bucket[1] > rule.max_hits:
                retry = max(1, int(rule.window_sec - (now - bucket[0])))
                return JSONResponse(
                    status_code=429,
                    content={"detail": "请求过于频繁,请稍后再试"},
                    headers={"Retry-After": str(retry)},
                )
        return await call_next(request)
