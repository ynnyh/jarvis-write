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

import httpx

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


def image_data_uri(data: bytes, ext: str) -> str:
    """图片字节 → base64 文件参数。

    用标准 data URI(带 data:image/...;base64 前缀)而不是裸 base64:
    这是 ComfyUI 系上传节点最普遍的接受形态。若平台只认裸 base64,改这一处即可。
    """
    mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(
        (ext or "").lower(), "image/png"
    )
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
    """查一次任务状态,返回 (归一化状态, 结果文件 URL 列表)。"""
    url = f"{_api_base(base_url)}/api/v1/comfyui/comfyui_workflow/result/{task_id}"
    try:
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT_S) as client:
            resp = await client.get(url, headers=_headers(token))
    except httpx.HTTPError as exc:
        # 单次轮询网络抖动不当失败:交由上层循环继续等,这里按 running 处理
        logger.warning("轮询出片任务 %s 网络异常(继续等): %s", task_id, exc)
        return "running", []
    if resp.status_code != 200:
        raise RenderError(f"查询出片任务失败(HTTP {resp.status_code})。")
    try:
        body = resp.json()
    except ValueError as exc:
        raise RenderError("出片平台返回了无法解析的轮询响应。") from exc
    data = body.get("data") or {}
    status = normalize_status(str(data.get("status") or ""))
    urls = [
        str(r.get("url"))
        for r in (data.get("results") or [])
        if isinstance(r, dict) and r.get("url")
    ]
    return status, urls


async def fetch_bytes(url: str, timeout_s: int = DOWNLOAD_TIMEOUT_S) -> bytes:
    """下载一个远程文件(渲染结果 / 外链首帧)。"""
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise RenderError(f"下载文件失败(HTTP {resp.status_code}):{url[:120]}")
    return resp.content
