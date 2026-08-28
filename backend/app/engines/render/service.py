# app/engines/render/service.py
# -*- coding: utf-8 -*-
"""一次出片的全过程编排:提交 → 轮询 → 当场下载落盘 → 回写各线成片指针。

跑在 jobs.spawn_job 的 worker 里,所以所有 DB 访问都自己开短 session、用完即关
(不跨网络调用持有 session,`database is locked` 的老根因)。结果 URL 有效期很短,
成功后必须立刻下载——这也是整个 service 存在的理由:提交和取货之间不能靠人。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import struct

from app import storage
from app.crypto import decrypt
from app.db.models import RenderConfig, RenderTask
from app.engines.render.client import (
    RenderError,
    fetch_bytes,
    file_data_uri,
    poll,
    submit,
)
from app.engines.render.emotion import emotion_weights, normalize_emotion

logger = logging.getLogger("jarvis-write.render")

POLL_INTERVAL_S = 2
# 总轮询预算:平台排队 + 生成 15s 视频通常几十秒到几分钟,10 分钟还不出就别吊着
POLL_BUDGET_S = 600
# 音频档上限:对口型工作流的 audio_duration 只到 15 秒(平台按此截取)
AUDIO_MAX_S = 15
# TTS 失败时的语速兜底(字/秒,与 production.py 同口径):wav 头解析不出来时估时长
_CHARS_PER_SEC = 4.5


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
    if kind in ("i2v", "talk"):
        progress("读取首帧静帧…")
        frame = spec.get("first_frame") or {}
        src = str(frame.get("src") or "")
        if not src:
            _mark_failed(task_id, "缺首帧静帧")
            raise RenderError("这一格没有可用的首帧静帧,请先挂静帧或改走文生视频。")
        data = await _resolve_first_frame(src, str(frame.get("kind") or "upload"))
        # 首尾帧工作流吃 first_frame,对口型工作流吃 ref_image_0(文档字段名不同)
        params["first_frame" if kind == "i2v" else "ref_image_0"] = file_data_uri(
            data, src.rsplit(".", 1)[-1]
        )

    # 对白链:先 indextts2 配音(带缓存),真实音频时长决定画面的 audio_duration
    talk = spec.get("talk")
    if kind == "talk" and talk:
        progress("配音中(indextts2)…")
        wav_bytes, wav_s, cached = await _synthesize_voice(progress, base_url, token, talk)
        params["ref_audio_0"] = file_data_uri(wav_bytes, "wav")
        params["audio_duration"] = max(1, min(AUDIO_MAX_S, round(wav_s)))
        with _db_session() as db:
            row = db.get(RenderTask, task_id)
            if row is not None:
                row.params = {
                    **(row.params or {}),
                    "duration_s": params["audio_duration"],
                    "tts_cached": cached,
                    **({"note": f"配音 {wav_s:.1f}s 超过工作流 15s 上限,成片已截断,建议把台词拆成两格"}
                       if wav_s > AUDIO_MAX_S else {}),
                }
                db.commit()

    progress("提交出片任务…")
    provider_task_id = await submit(base_url, token, workflow_id, params)
    with _db_session() as db:
        row = db.get(RenderTask, task_id)
        if row is not None:
            row.status = "running"
            row.provider_task_id = provider_task_id
            db.commit()

    try:
        video_urls = await _wait_result(progress, base_url, token, provider_task_id, "生成")
    except RenderError as exc:
        _mark_failed(task_id, str(exc))
        raise
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
    return {"task_id": task_id, "status": "success", "result_path": rel}


def _cache_key(talk: dict) -> str:
    """配音缓存键:工作流|音色源|情绪|台词。任一变了就是新配音。"""
    raw = "|".join([
        str(talk.get("workflow_tts") or ""),
        str(talk.get("voice_src") or ""),
        normalize_emotion(str(talk.get("emotion") or "")),
        str(talk.get("text") or "").strip(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def wav_duration_s(data: bytes) -> float:
    """RIFF/wav 头解析真实时长(秒);解析不出返回 0(调用方按字数兜底估算)。

    纯 python 读块,不引 ffmpeg:fmt 块取 byte_rate(采样率×声道×位宽),
    data 块大小除以它即时长。chunk 按 2 字节对齐跳过非 fmt/data 的杂块。
    """
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return 0.0
    pos, byte_rate = 12, 0
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        try:
            size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        except struct.error:
            return 0.0
        if cid == b"fmt " and pos + 20 <= len(data):
            byte_rate = struct.unpack("<I", data[pos + 16:pos + 20])[0]
        elif cid == b"data":
            if byte_rate <= 0:
                return 0.0
            return size / byte_rate
        pos += 8 + size + (size & 1)
    return 0.0


async def _wait_result(progress, base_url: str, token: str, provider_task_id: str,
                       what: str) -> list[str]:
    """轮询一个平台任务直到出结果,返回文件 URL 列表;失败/超时抛 RenderError。"""
    waited = 0
    while waited < POLL_BUDGET_S:
        await asyncio.sleep(POLL_INTERVAL_S)
        waited += POLL_INTERVAL_S
        status, urls = await poll(base_url, token, provider_task_id)
        if status == "success":
            return [u for u in urls if u]
        if status == "failed":
            raise RenderError(f"{what}失败:平台生成出错,请稍后重试。")
        if waited % 20 == 0:
            progress(f"{what}中…已等待 {waited} 秒")
    raise RenderError(
        f"{what}超时(等了 {POLL_BUDGET_S} 秒还没出结果)。平台任务 {provider_task_id} "
        "可能仍在排队,可稍后重出一次。"
    )


async def _synthesize_voice(progress, base_url: str, token: str, talk: dict) -> tuple[bytes, float, bool]:
    """台词 → 配音 wav 字节 + 真实时长(秒)。缓存命中直接读文件,零调用零等待。

    台词/音色/情绪/工作流任一变了 key 就变,自然重新合成;缓存文件丢了
    (用户清盘)也当未命中,照常重合。
    """
    from app.db.models import TtsTrack

    key = _cache_key(talk)
    with _db_session() as db:
        row = db.query(TtsTrack).filter(TtsTrack.cache_key == key).first()
        if row is not None and row.path:
            try:
                path = storage.resolve(row.path)
                if path.is_file():
                    return path.read_bytes(), float(row.duration_s or 0), True
            except storage.UploadError:
                logger.warning("配音缓存 %s 读取失败,重新合成", key)

    text = str(talk.get("text") or "").strip()
    voice_src = str(talk.get("voice_src") or "")
    if not text or not voice_src:
        raise RenderError("对白出片缺台词或音色参考,请检查角色卡的音色上传。")
    try:
        path = storage.resolve(voice_src)
        if not path.is_file():
            raise storage.UploadError("音频文件不存在")
        voice_bytes = path.read_bytes()
    except storage.UploadError as exc:
        raise RenderError(
            f"该角色的音色参考已失效({exc}),请到角色卡重新上传后再出片。"
        ) from exc

    params = {
        "prompt_text": text,
        "prompt_simple": file_data_uri(voice_bytes, voice_src.rsplit(".", 1)[-1]),
        **emotion_weights(str(talk.get("emotion") or "")),
    }
    provider_task_id = await submit(
        base_url, token, str(talk.get("workflow_tts") or ""), params
    )
    urls = await _wait_result(progress, base_url, token, provider_task_id, "配音")
    if not urls:
        raise RenderError("配音平台没有返回音频文件,请重试一次。")
    data = await fetch_bytes(urls[0])
    try:
        rel = storage.save_tts_cache(key, data)
    except storage.UploadError as exc:
        raise RenderError(str(exc)) from exc
    duration = wav_duration_s(data) or round(len(text) / _CHARS_PER_SEC, 2)
    with _db_session() as db:
        db.add(TtsTrack(
            user_id=int(talk.get("user_id") or 0),
            cache_key=key, voice_src=voice_src, text=text[:2000],
            emotion=normalize_emotion(str(talk.get("emotion") or "")),
            workflow_id=str(talk.get("workflow_tts") or ""),
            duration_s=duration, path=rel,
        ))
        db.commit()
    return data, duration, False


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
