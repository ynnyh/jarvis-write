# app/ratelimit.py
# -*- coding: utf-8 -*-
"""极简内存限流中间件:按客户端 IP 对敏感接口(登录/注册)限速,登录再叠加一个
按用户名的桶(同一账号从多个 IP 打过来照样被挡)。

为什么自研而非 slowapi:部署是单进程 uvicorn(见 Dockerfile),内存计数即够,
不必引 Redis / 新依赖。若日后换多 worker / gunicorn,需改成共享存储(见 docs)。

窗口用固定窗口计数(实现简单、够挡脚本化撞库/刷号);跨窗口边界最坏放行约 2N 次,
对"挡暴力破解/批量注册"这个目的无所谓。时间用 time.monotonic(),不受系统改钟影响。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# 活跃 (规则, 桶键) 超过此数就清一次过期项,防伪造 IP/用户名把内存撑爆
_MAX_BUCKETS = 10_000

# 登录体只有「用户名 + 密码」,超过这个大小就不取用户名了(request.body() 会把
# 整个请求体读进内存,登录接口不该被一个超大 body 拖垮)
_MAX_LOGIN_BODY_BYTES = 8 * 1024

# 用户名桶的键长上限:对齐注册的 max_length=50,超长的直接不建桶(防批量造桶)
_MAX_USERNAME_KEY = 50


@dataclass(frozen=True)
class Rule:
    method: str  # 大写 HTTP 方法
    path: str  # 精确匹配的请求路径
    max_hits: int  # 窗口内最多次数
    window_sec: int  # 窗口秒数
    by_username: bool = False  # 额外再按用户名分一个桶(见 dispatch)


# 敏感接口:登录挡撞库,注册挡批量刷号。阈值宽松,只拦脚本化滥用,不误伤手滑。
DEFAULT_RULES: tuple[Rule, ...] = (
    # 登录同时按 IP 与用户名双限:IP 桶挡住单机脚本,用户名桶挡住「换 IP 挨个
    # 试同一批账号」的分布式撞库——IP 可以轮换,账号不会。
    Rule("POST", "/api/auth/login", max_hits=20, window_sec=300, by_username=True),
    Rule("POST", "/api/auth/register", max_hits=10, window_sec=3600),
)


def client_ip(request: Request) -> str:
    """取真实客户端 IP:默认只认传输层对端(request.client.host)。

    X-Forwarded-For 的**第一段是客户端自己写的**,任何人每次请求换一个值就能
    换一个桶,IP 限流形同虚设。所以默认完全不看它;只有显式开了
    trust_proxy_headers(确实部署在会覆写该头的 Caddy/Nginx 之后)才读,且取
    **最右一跳**——那才是最后一个可信反代记下的对端地址,伪造的前缀排在它左边。
    """
    from app.config import get_settings

    if get_settings().trust_proxy_headers:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            hop = xff.split(",")[-1].strip()
            if hop:
                return hop
    client = request.client
    return client.host if client else "unknown"


async def _login_username(request: Request) -> str:
    """从登录请求体里取用户名(归一化后作为桶键),取不到就返回空串。

    归一化方式与登录接口自己的一致:去空白 + 小写。账号可以换 IP 打,但
    "Admin"/"admin"/" admin " 是同一个人,合成一个桶才拦得住大小写变体轮询。

    读 body 是安全的:Starlette 的 BaseHTTPMiddleware 用 _CachedRequest 包住
    请求,这里 body() 读走的内容会被缓存下来原样喂给下游路由(见 dispatch 注释)。
    这里不碰登录的业务逻辑与错误文案,只是提前数一次。
    """
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:  # 畸形 Content-Length:不猜,交给下游自己报错
        return ""
    if length <= 0 or length > _MAX_LOGIN_BODY_BYTES:
        return ""
    try:
        payload = json.loads(await request.body() or b"{}")
    except Exception:  # 非 JSON / 客户端断开:登录照常走,只是不按用户名计桶
        return ""
    if not isinstance(payload, dict):
        return ""
    name = str(payload.get("username") or "").strip().lower()
    if not name or len(name) > _MAX_USERNAME_KEY:
        return ""
    return name


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按 (规则, 桶键) 固定窗口计数;超限返回 429。仅命中登录/注册,其余零开销。"""

    def __init__(self, app, rules: tuple[Rule, ...] = DEFAULT_RULES) -> None:
        super().__init__(app)
        self._rules = rules
        # (rule_idx, 桶键) -> [window_start_monotonic, hits]
        # 桶键 = 客户端 IP,登录时另加一个 "user:<用户名小写>"
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

    def _count(self, key: tuple[int, str], now: float, rule: Rule) -> int:
        """记一次命中,返回 0=放行 / >0=还差几秒解禁(超限时)。"""
        bucket = self._buckets.get(key)
        if bucket is None or now - bucket[0] >= rule.window_sec:
            # 新窗口(过期即重置,同一桶键不会无限累积)
            self._buckets[key] = [now, 1]
            return 0
        bucket[1] += 1
        if bucket[1] > rule.max_hits:
            return max(1, int(rule.window_sec - (now - bucket[0])))
        return 0

    async def dispatch(self, request: Request, call_next):
        idx = self._match(request)
        if idx is None:
            return await call_next(request)

        rule = self._rules[idx]
        now = time.monotonic()
        if len(self._buckets) > _MAX_BUCKETS:
            self._prune(now)

        # 先 IP 桶:被 IP 桶挡下的请求不再去开用户名桶。否则一个已被限流的 IP
        # 每打一次就能凭空多一个桶,而 _MAX_BUCKETS 的清理只删过期项,桶数会被
        # 单一来源刷爆。IP 桶已是第一道防线,用户名桶只在这一道放行后才参与。
        retry = self._count((idx, client_ip(request)), now, rule)
        if retry:
            return _too_many(retry)

        if rule.by_username:
            # 在这里 await request.body() 是安全的:_CachedRequest 会把整段 body
            # 缓存下来继续喂给下游(它读 body() 时拿到的是缓存值),登录路由不会
            # 因为这里先读了一次就收不到 body。
            name = await _login_username(request)
            if name:
                retry = self._count((idx, f"user:{name}"), now, rule)
                if retry:
                    return _too_many(retry)
        return await call_next(request)


def _too_many(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "请求过于频繁,请稍后再试"},
        headers={"Retry-After": str(retry_after)},
    )
