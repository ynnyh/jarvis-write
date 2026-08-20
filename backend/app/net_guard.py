# app/net_guard.py
# -*- coding: utf-8 -*-
"""出站地址校验:拦截把 provider base_url 指向私网/环回/元数据地址的配置。

SSRF 防线:用户能在设置页自定义 base_url,服务器会拿它发出站请求。若指向内网
(127.0.0.1 / 10.x / 169.254.169.254 云元数据等),可被用来探测内网服务。
在"保存配置"这个低频入口做一次 DNS 解析 + IP 段判断,挡掉绝大多数尝试。

局限:DNS rebinding(解析时公网、请求时内网)绕得过——那要在发请求时再校验,
成本高;对试用场景先接受,记 TODO。空 base_url 放行(回落 .env 默认,可信)。
"""
from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urlparse

from fastapi import HTTPException


def _is_blocked(ip_text: str) -> bool:
    ip = ipaddress.ip_address(ip_text)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 含 169.254.169.254 云元数据端点
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_public_base_url(base_url: str) -> None:
    """base_url 指向内网/本机则抛 400;空串或无法解析的域名放行。"""
    url = (base_url or "").strip()
    if not url:
        return  # 空 = 回落默认,可信
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="base_url 格式不正确")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # 解析不了的域名连不上,放行(真正外呼会自然失败),不误伤内网 DNS/临时故障
        return
    for info in infos:
        if _is_blocked(info[4][0]):
            raise HTTPException(
                status_code=400, detail="base_url 不能指向内网/本机地址"
            )


# ---------------------------------------------------------------------------
# Cloudflare CDN 检测(风险提示用,不拦截)
# ---------------------------------------------------------------------------

# Cloudflare 官方公布的 IP 段(https://www.cloudflare.com/ips/,变更极低频)。
# 中转站套 CF CDN 后,国内家宽直连其边缘节点常见分钟级间歇性 RST——生成一章要打
# 十几次模型调用、战线几十分钟,几乎必撞故障窗口;而「测试连接」单次几秒,恰好在
# 窗口外就通过。故在保存/列表/测试处标记提醒,但不拦截(挂代理或部分运营商下
# CF 渠道完全正常)。注意:中转站用"优选 IP"接入时解析不落官方段,检测不到,
# 属可接受的漏检(漏检好过误伤)。
_CLOUDFLARE_NETS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
        "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
        "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
    )
)

# host -> (到期时间戳, 是否CF)。设置页列表每次加载都要给每行打标,不能行行打 DNS;
# DNS 视图短期内也不该跳变,TTL 缓存足够。读取竞态最坏多解析一次,无害。
_CF_CACHE: dict[str, tuple[float, bool]] = {}
_CF_CACHE_TTL = 300.0


def is_cloudflare_ip(ip_text: str) -> bool:
    """IP 是否落在 Cloudflare 官方段(字面量判断,不发 DNS,可单测)。"""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return any(ip in net for net in _CLOUDFLARE_NETS)


def is_cloudflare_hosted(base_url: str) -> bool:
    """base_url 的域名解析到 Cloudflare IP 段 → 视为套了 CF CDN。

    同步函数(内部 getaddrinfo 会阻塞),async 端点里请用 asyncio.to_thread 包。
    空/无 host/解析失败一律 False——检测是尽力而为的提示,绝不阻塞业务。
    """
    url = (base_url or "").strip()
    host = urlparse(url).hostname if url else None
    if not host:
        return False
    now = time.monotonic()
    cached = _CF_CACHE.get(host)
    if cached and cached[0] > now:
        return cached[1]
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    result = any(is_cloudflare_ip(info[4][0]) for info in infos)
    _CF_CACHE[host] = (now + _CF_CACHE_TTL, result)
    return result
