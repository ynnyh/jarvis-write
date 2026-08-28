# app/engines/render/client.py
# -*- coding: utf-8 -*-
"""autodl.art ComfyUI 工作流 API 客户端:提交 → 轮询 → 下载,三步都是纯网络。

接口契约(平台文档 https://autodl.art/docs/comfyui_api/):
- POST /api/v1/comfyui/comfyui_workflow/{workflow_id}  提交,立即返回 task_id;
- GET  /api/v1/comfyui/comfyui_workflow/result/{task_id}  轮询,QUEUED/RUNNING/SUCCESS;
- 文件参数(首帧图)接受「URL地址/base64文件」,这里走 base64(桌面版后端没有
  公网 URL,上传本地文件是刚需);
- 结果里的资源 URL 有效期很短,拿到必须立刻下载,过期作废。
"""
from __future__ import annotations

import base64
import logging
from urllib.parse import urljoin

import httpx

from app.net_guard import check_public_url

logger = logging.getLogger("jarvis-write.render")

SUBMIT_TIMEOUT_S = 30
POLL_TIMEOUT_S = 30
DOWNLOAD_TIMEOUT_S = 120

# 平台的状态词有点随性:文档写 SUCCESS/FAILED,实测返回过 completed——
# 归一到 success/failed/running 三态,别让上游措辞打进状态机。
_SUCCESS_WORDS = {"success", "completed", "successful", "succeeded"}
_FAIL_WORDS = {"failed", "fail", "error"}


class RenderError(RuntimeError):
    """出片相关的业务性错误(信息直接上屏,不丢堆栈)。"""


class PollTransientError(RuntimeError):
    """轮询时的瞬时故障(网络抖动/5xx/响应截断):可重试,由上层带计数地容忍。

    与 RenderError 的分工:长轮询要打几百次请求,单次抖动是常态,吞掉继续等;
    但连续失败说明链路真断了,计数由 poll_with_retry 管,到顶翻译成明确错误。
    """


class SSRFBlocked(RenderError):
    """下载地址指向内网/本机:由调用方按场景包成人话再上屏(各自话术不同)。

    继承 RenderError 是有意的——漏 catch 时上屏仍是一句人话,不会甩堆栈给用户。
    """


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": token, "Content-Type": "application/json"}


def _api_base(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def normalize_status(raw: str) -> str:
    v = (raw or "").strip().lower()
    if v in _SUCCESS_WORDS:
        return "success"
    if v in _FAIL_WORDS:
        return "failed"
    return "running"


def file_data_uri(data: bytes, ext: str) -> str:
    """文件字节 → base64 文件参数(图片与音频通用)。

    用标准 data URI(带 data:<mime>;base64 前缀)而不是裸 base64:
    这是 ComfyUI 系上传节点最普遍的接受形态。若平台只认裸 base64,改这一处即可。
    """
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "webp": "image/webp",
        "wav": "audio/wav", "mp3": "audio/mpeg",
    }.get((ext or "").lower(), "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def submit(base_url: str, token: str, workflow_id: str, params: dict) -> str:
    """提交一个工作流任务,返回平台的 task_id。"""
    if not token:
        raise RenderError("尚未配置出片引擎的令牌(token),请先到「设置 → 出片引擎」填写。")
    url = f"{_api_base(base_url)}/api/v1/comfyui/comfyui_workflow/{workflow_id}"
    try:
        async with httpx.AsyncClient(timeout=SUBMIT_TIMEOUT_S) as client:
            resp = await client.post(url, json=params, headers=_headers(token))
    except httpx.TimeoutException as exc:
        raise RenderError("提交出片任务超时,请检查网络后重试。") from exc
    except httpx.HTTPError as exc:
        raise RenderError(f"连接出片平台失败:{exc}") from exc
    if resp.status_code != 200:
        raise RenderError(
            f"提交出片任务失败(HTTP {resp.status_code}):{resp.text[:200]}"
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise RenderError("出片平台返回了无法解析的响应。") from exc
    data = body.get("data") or {}
    task_id = str(data.get("task_id") or "")
    code = str(body.get("code") or "")
    if not task_id or (code and code.lower() not in _SUCCESS_WORDS):
        raise RenderError(f"提交出片任务被平台拒绝:{body.get('msg') or body.get('code') or resp.text[:200]}")
    return task_id


async def poll(base_url: str, token: str, task_id: str) -> tuple[str, list[str]]:
    """查一次任务状态,返回 (归一化状态, 结果文件 URL 列表)。

    故障分三档:401/402/403 是令牌/余额问题,快失败说人话;网络异常、其他非 200、
    响应截断是瞬时故障,抛 PollTransientError 交 poll_with_retry 带计数容忍。
    """
    url = f"{_api_base(base_url)}/api/v1/comfyui/comfyui_workflow/result/{task_id}"
    try:
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT_S) as client:
            resp = await client.get(url, headers=_headers(token))
    except httpx.HTTPError as exc:
        raise PollTransientError(f"网络异常:{exc}") from exc
    if resp.status_code in (401, 402, 403):
        raise RenderError(
            f"出片平台拒绝了查询请求(HTTP {resp.status_code}):令牌无效或余额不足,"
            "请到「设置 → 出片引擎」检查令牌与账户余额。"
        )
    if resp.status_code != 200:
        raise PollTransientError(f"平台返回 HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise PollTransientError("平台返回了无法解析的响应") from exc
    data = body.get("data") or {}
    status = normalize_status(str(data.get("status") or ""))
    urls = [
        str(r.get("url"))
        for r in (data.get("results") or [])
        if isinstance(r, dict) and r.get("url")
    ]
    return status, urls


# 连续瞬时失败的容忍上限:单次抖动常见,连着 5 次基本就是链路断了——
# 及时给用户一个明确说法,别把 10 分钟轮询预算全烧在无效重试上。
MAX_CONSECUTIVE_POLL_FAILURES = 5


async def poll_with_retry(base_url: str, token: str, task_id: str) -> tuple[str, list[str]]:
    """轮询一次任务状态,对瞬时故障带计数容忍;终态(成功/失败)与钱的问题直接返回/抛出。"""
    streak = 0
    while True:
        try:
            status, urls = await poll(base_url, token, task_id)
        except PollTransientError as exc:
            streak += 1
            if streak >= MAX_CONSECUTIVE_POLL_FAILURES:
                raise RenderError(
                    f"多次查询出片任务均失败({exc}),请检查网络后重试。"
                ) from exc
            logger.warning("轮询 %s 第 %d 次瞬时失败,继续等: %s", task_id, streak, exc)
            continue
        return status, urls


# 重定向跟跳上限:正常 CDN 一两次到位,转圈超过 5 次基本是在被遛,别陪跑。
MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)

# 下载体积上限:成片/配音正常几十 MB,给足余量。超限基本是地址被喂了垃圾,
# 流式边收边数,别让几个 GB 的响应体一口吞进内存。
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
_OVERSIZE_MSG = f"下载的文件超过 {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB 上限,已中止。"


async def fetch_bytes(url: str, timeout_s: int = DOWNLOAD_TIMEOUT_S) -> bytes:
    """下载一个远程文件(渲染结果 / 外链首帧),**每一跳**都过 SSRF 校验。

    重定向自己跟、不用 httpx 的 follow_redirects:入口是公网不代表跳完还是公网,
    「给你一个公网地址,302 到 169.254.169.254」是最经典的绕过套路,交给库自动
    跟跳就只剩入口那一跳有校验。逐跳校验在这里兜住,顺带给以后新增的下载点
    兜底——只要走这个函数,就碰不到内网。
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        reason = check_public_url(current)
        if reason:
            raise SSRFBlocked(reason)
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
            # 流式读 + 体积两头堵:Content-Length 声明超限一个字节都不读,
            # 实际字节收满上限即断连接,内存占用封顶
            async with client.stream("GET", current) as resp:
                if resp.status_code in _REDIRECT_CODES and resp.headers.get("location"):
                    current = urljoin(current, resp.headers["location"])  # 相对 Location 要按当前跳解析
                    continue
                if resp.status_code != 200:
                    raise RenderError(f"下载文件失败(HTTP {resp.status_code}):{url[:120]}")
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
                    raise RenderError(_OVERSIZE_MSG)
                chunks: list[bytes] = []
                received = 0
                async for chunk in resp.aiter_bytes():
                    received += len(chunk)
                    if received > MAX_DOWNLOAD_BYTES:
                        raise RenderError(_OVERSIZE_MSG)
                    chunks.append(chunk)
        break
    else:
        raise RenderError(f"下载地址重定向次数过多(超过 {MAX_REDIRECTS} 次),已放弃。")
    return b"".join(chunks)
