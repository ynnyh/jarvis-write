# app/engines/render/service.py
# -*- coding: utf-8 -*-
"""一次出片的全过程编排:提交 → 轮询 → 当场下载落盘 → 回写各线成片指针。

跑在 jobs.spawn_job 的 worker 里,所以所有 DB 访问都自己开短 session、用完即关
(不跨网络调用持有 session,`database is locked` 的老根因)。结果 URL 有效期很短,
成功后必须立刻下载——这也是整个 service 存在的理由:提交和取货之间不能靠人。
"""
from __future__ import annotations

import asyncio
import logging

from app import storage
from app.crypto import decrypt
from app.db.models import RenderConfig, RenderTask
from app.engines.render.client import (
    RenderError,
    fetch_bytes,
    image_data_uri,
    poll,
    submit,
)

logger = logging.getLogger("jarvis-write.render")

POLL_INTERVAL_S = 2
# 总轮询预算:平台排队 + 生成 15s 视频通常几十秒到几分钟,10 分钟还不出就别吊着
POLL_BUDGET_S = 600


def _db_session():
    from app.db.session import SessionLocal
    return SessionLocal()


async def _resolve_first_frame(src: str, kind: str) -> bytes:
    """首帧图 → 字节。本地资产直接读盘;外链当场拉(拉不到就明说,不悄悄降级)。"""
    if kind == "upload":
        try:
            path = storage.resolve(src)
        except storage.UploadError as exc:
            raise RenderError(f"首帧静帧文件已失效:{exc}") from exc
        if not path.is_file():
            raise RenderError("首帧静帧文件已丢失,请重新上传后再出片。")
        return path.read_bytes()
    from app.engines.render.client import fetch_bytes as _fetch

    try:
        return await _fetch(src, timeout_s=60)
    except RenderError as exc:
        raise RenderError(
            f"首帧用的是外链,但拉取失败({exc})。生图站的外链普遍带时效签名,"
            "建议把图下载后在本格重新上传,再点出片。"
        ) from exc


def apply_pointer(line: str, shot_id: int | None, clip_id: int | None, chunk_index: int, rel: str) -> None:
    """把某版草片回写成「这一格/段的当前成片」(只动指针,不打勾)。

    出片成功后引擎自动调;用户在版本历史里「改用某版」时 api 层也调同一个。
    done_still/done_video 的勾仍由人工判断——草片合不合格,引擎说了不算。
    """
    with _db_session() as db:
        if line == "drama" and shot_id:
            from app.db.models import DramaShot

            shot = db.get(DramaShot, shot_id)
            if shot is not None:
                shot.clip_ref = rel
                db.commit()
        elif line == "clips" and clip_id is not None:
            from sqlalchemy.orm.attributes import flag_modified

            from app.db.models import ClipShoot

            row = db.query(ClipShoot).filter(ClipShoot.clip_id == clip_id).first()
            if row is not None and 0 <= chunk_index < len(row.shoot or []):
                row.shoot[chunk_index]["result_link"] = rel
                flag_modified(row, "shoot")
                db.commit()


async def start_render(progress, spec: dict) -> dict:
    """跑一次出片(异步 worker)。spec 字段见 api/render.py 的提交端点。"""
    task_id = int(spec["task_id"])
    line = spec["line"]
    kind = spec["kind"]
    workflow_id = spec["workflow_id"]

    # 配置现场读:提交到真正执行之间用户可能改了 token,以执行那一刻为准
    with _db_session() as db:
        cfg = (
            db.query(RenderConfig)
            .filter(RenderConfig.user_id == int(spec["user_id"]))
            .first()
        )
        token = decrypt(cfg.token) if cfg else ""
        base_url = cfg.base_url if cfg else ""
        if not token:
            _mark_failed(task_id, "尚未配置出片引擎令牌")
            raise RenderError("尚未配置出片引擎的令牌(token),请先到「设置 → 出片引擎」填写。")

    params = dict(spec.get("params") or {})
    if kind == "i2v":
        progress("读取首帧静帧…")
        frame = spec.get("first_frame") or {}
        src = str(frame.get("src") or "")
        if not src:
            _mark_failed(task_id, "缺首帧静帧")
            raise RenderError("这一格没有可用的首帧静帧,请先挂静帧或改走文生视频。")
        data = await _resolve_first_frame(src, str(frame.get("kind") or "upload"))
        params["first_frame"] = image_data_uri(data, src.rsplit(".", 1)[-1])

    progress("提交出片任务…")
    provider_task_id = await submit(base_url, token, workflow_id, params)
    with _db_session() as db:
        row = db.get(RenderTask, task_id)
        if row is not None:
            row.status = "running"
            row.provider_task_id = provider_task_id
            db.commit()

    waited = 0
    while waited < POLL_BUDGET_S:
        await asyncio.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
        status, urls = await poll(base_url, token, provider_task_id)
        if status == "success":
            break
        if status == "failed":
            _mark_failed(task_id, "平台生成失败")
            raise RenderError("出片平台生成失败,请调整提示词或稍后重试。")
        if waited % 20 == 0:
            progress(f"生成中…已等待 {waited} 秒")
    else:
        _mark_failed(task_id, "轮询超时")
        raise RenderError(
            f"出片超时(等了 {POLL_BUDGET_S} 秒还没出结果)。平台任务 {provider_task_id} "
            "可能仍在排队,可稍后重出一次。"
        )

    video_urls = [u for u in urls if u]
    if not video_urls:
        _mark_failed(task_id, "平台没返回视频文件")
        raise RenderError("出片平台没有返回视频文件,请重试一次。")
    progress("下载成片…")
    data = await fetch_bytes(video_urls[0])
    try:
        rel = storage.save_render_result(task_id, data)
    except storage.UploadError as exc:
        _mark_failed(task_id, str(exc))
        raise RenderError(str(exc)) from exc

    with _db_session() as db:
        row = db.get(RenderTask, task_id)
        if row is not None:
            row.status = "success"
            row.result_path = rel
            db.commit()
    apply_pointer(
        line,
        spec.get("shot_id"),
        spec.get("clip_id"),
        int(spec.get("chunk_index", -1)),
        rel,
    )
    logger.info("出片完成 task=%s line=%s → %s", task_id, line, rel)
    return {"task_id": task_id, "status": "success", "result_path": rel, "waited_s": waited}


def _mark_failed(task_id: int, error: str) -> None:
    """把任务行标成 failed(尽力而为:连不上库也不影响主错误往上抛)。"""
    try:
        with _db_session() as db:
            row = db.get(RenderTask, task_id)
            if row is not None:
                row.status = "failed"
                row.error = error[:500]
                db.commit()
    except Exception:  # noqa: BLE001
        logger.debug("标记出片任务 %s 失败状态时出错", task_id, exc_info=True)
