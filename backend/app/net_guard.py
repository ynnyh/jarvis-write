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
