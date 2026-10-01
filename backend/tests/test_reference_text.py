"""公开文本来源的限量读取、重定向保护和分析证据落点；不访问真实网络。"""
import asyncio
from unittest.mock import patch

import httpx
import pytest

from app.engines import reference_text
from app.engines.creative import analyze_references
from app.schemas.creative import GoalInput


def read_with_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(reference_text.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


async def address(url):
    if url.host == "internal.invalid":
        raise ValueError("private")
    return "8.8.8.8"


def test_html_text_scope_and_request_contains_no_credentials(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text="<script>恶意指令</script><article><h1>饭店</h1><p>省两块，停车费八块。</p></article>")
    monkeypatch.setattr(reference_text, "public_address", address)
    read_with_transport(monkeypatch, handler)
    text, scope = asyncio.run(reference_text.read_reference_text("https://example.invalid/a"))
    assert "停车费八块" in text and "恶意指令" not in text
    assert "前" in scope
    assert requests[0].url.host == "8.8.8.8"
    assert requests[0].headers["host"] == "example.invalid"
    assert requests[0].extensions["sni_hostname"] == "example.invalid"
    assert "authorization" not in requests[0].headers and "cookie" not in requests[0].headers


def test_redirect_to_private_destination_stops_before_connecting(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://internal.invalid/x"})
    monkeypatch.setattr(reference_text, "public_address", address)
    read_with_transport(monkeypatch, handler)
    with pytest.raises(ValueError):
        asyncio.run(reference_text.read_reference_text("https://example.invalid/a"))
    assert len(requests) == 1


def test_private_dns_and_custom_ports_rejected(monkeypatch):
    async def run():
        loop = asyncio.get_running_loop()
        async def resolver(*args, **kwargs):
            return [(None, None, None, None, ("127.0.0.1", 443))]
        monkeypatch.setattr(loop, "getaddrinfo", resolver)
        for url in ("https://localhost/a", "https://example.invalid:9999/a", "file:///tmp/a", "https://user:pass@example.invalid/a"):
            with pytest.raises(ValueError):
                await reference_text.public_address(httpx.URL(url))
    asyncio.run(run())


@pytest.mark.parametrize("kind,size", [("video/mp4", 5), ("text/plain", 1_000_001)], ids=["video", "oversize"])
def test_non_text_and_oversize_references_rejected(monkeypatch, kind, size):
    monkeypatch.setattr(reference_text, "public_address", address)
    read_with_transport(monkeypatch, lambda r: httpx.Response(200, headers={"content-type": kind}, content=b"x" * size))
    with pytest.raises(ValueError):
        asyncio.run(reference_text.read_reference_text("https://example.invalid/a"))


def test_analyzer_reads_only_missing_excerpts_and_records_failures():
    goal = GoalInput(fetch_links=True, references=[{"name": "A", "url": "https://example.invalid/a"}, {"name": "B", "url": "https://example.invalid/b"}, {"name": "C", "url": "https://example.invalid/c", "excerpt": "作者自己挑的片段"}])
    async def reader(link):
        if link.endswith("b"):
            raise ValueError("unavailable")
        assert link.endswith("a")
        return "包袱来自省两块却亏八块。", "读取前12字"
    class Adapter:
        async def ask(self, prompt, **kwargs):
            assert "作者自己挑的片段" in prompt
            return '{"observations":[{"dimension":"engine","instruction":"用反向代价制造笑点","source_index":0,"basis":"excerpt","evidence":"省两块却亏八块"}],"unknowns":[]}'
    with patch("app.engines.reference_text.read_reference_text", side_effect=reader), patch("app.engines.creative.get_adapter_for", return_value=Adapter()):
        result = asyncio.run(analyze_references(goal))
    assert result["references"][0]["read_scope"] == "读取前12字"
    assert result["observations"][0]["basis"] == "excerpt"
    assert any("参考 2" in u and "未读取成功" in u for u in result["unknowns"])
    assert result["references"][2]["excerpt"] == "作者自己挑的片段"
