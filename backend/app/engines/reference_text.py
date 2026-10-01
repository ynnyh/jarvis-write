"""公开文本参考读取：限定文本、限量读取、逐跳检查并固定DNS结果。"""
from __future__ import annotations

import asyncio
from html.parser import HTMLParser
import ipaddress
import socket

import httpx


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1
        if not self.hidden and tag in ("p", "br", "div", "article", "section", "h1", "h2", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


async def public_address(url: httpx.URL) -> str:
    if url.scheme not in ("http", "https") or not url.host or url.username or url.password:
        raise ValueError("只支持无账号密码的公开 HTTP/HTTPS 文本链接")
    port = url.port or (443 if url.scheme == "https" else 80)
    if port not in (80, 443):
        raise ValueError("只支持公开网页的标准端口")
    loop = asyncio.get_running_loop()
    results = await asyncio.wait_for(loop.getaddrinfo(url.host, port, type=socket.SOCK_STREAM), timeout=5)
    addresses = list(dict.fromkeys(r[4][0] for r in results))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("该链接不是公开网页地址")
    return addresses[0]


async def read_reference_text(link: str) -> tuple[str, str]:
    url = httpx.URL(link)
    # 不继承本机代理，不向参考站点发送模型密钥、登录态或用户凭证。
    async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as client:
        for _ in range(4):
            address = await public_address(url)
            pinned = url.copy_with(host=address)
            async with client.stream("GET", pinned, headers={"Host": url.host, "Accept": "text/plain,text/html", "User-Agent": "JarvisWrite-Reference/1.0"}, extensions={"sni_hostname": url.host}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("网页跳转缺少目标地址")
                    url = url.join(location)
                    continue
                response.raise_for_status()
                kind = response.headers.get("content-type", "").split(";")[0].lower()
                if kind not in ("text/plain", "text/html", "application/xhtml+xml", "text/markdown"):
                    raise ValueError("该链接不是文本网页，请粘贴正文/字幕；音视频和PDF暂不读取")
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 1_000_000:
                        raise ValueError("网页过大，请提供需要借鉴的文本片段")
                text = bytes(payload).decode(response.encoding or "utf-8", errors="replace")
                if kind in ("text/html", "application/xhtml+xml"):
                    parser = TextParser()
                    parser.feed(text)
                    text = "".join(parser.parts)
                text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
                if not text:
                    raise ValueError("没有读取到正文，可能需要登录或脚本加载，请粘贴片段")
                scope = f"公开文本页面，读取前{min(len(text), 12000)}字" + ("（后文未读取）" if len(text) > 12000 else "")
                return text[:12000], scope
    raise ValueError("网页跳转次数过多，请提供最终文本链接")
