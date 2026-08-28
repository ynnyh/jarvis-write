# app/api/promo.py
# -*- coding: utf-8 -*-
"""宣传片工坊接口:企划 CRUD + 多轮研讨(SSE 流式) + 简报/风格/地标/解说词/分镜/提示词/成片包 + 导出。

研讨是主流程:先聊透方向 →「收敛简报」成契约 → 简报驱动后续生成,各步独立可重跑。
归属隔离与项目同款(assert_project_owner 按 user_id 判断);SSE 帧式同润色研讨
(token/done/error),对话记录持久化在 plan.chat_log。

GET    /api/promos                       我的企划列表
POST   /api/promos                       新建(subject/angles/duration_s/direction)
GET    /api/promos/{id}                  企划详情(含分镜)
PATCH  /api/promos/{id}                  改表单字段/简报/锁定/素材点/地标卡
DELETE /api/promos/{id}                  删企划(连分镜)
POST   /api/promos/{id}/chat             研讨对话(SSE 流式)
POST   /api/promos/{id}/brief            研讨收敛成简报(async)
POST   /api/promos/{id}/style            生成视觉风格(async)
POST   /api/promos/{id}/landmarks        生成地标卡(async)
POST   /api/promos/{id}/script           写解说词(async)
POST   /api/promos/{id}/storyboard       拆分镜(async,覆盖式)
POST   /api/promos/{id}/prompts          出三轨提示词(async)
POST   /api/promos/{id}/pack             出成片包(async)
PATCH  /api/promos/{id}/shots/{sid}      手动改分镜/提示词
GET    /api/promos/{id}/export           导出 ?format=md|csv|srt|json
GET    /api/promos/meta                  角度/方向目录(前端选择器用)
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import assert_project_owner, get_current_user
from app.db.models import PromoPlan, PromoShot, Project  # noqa: F401 — Project 仅文档示意
from app.db.session import get_db
from app.engines.promo import (
    PromoAssetError,
    PromoChunkError,
    PromoBriefError,
    PromoChatError,
    PromoPackError,
    PromoPromptError,
    PromoScriptError,
    PromoStoryboardError,
    build_chunks,
    build_pack,
    build_promo_film_prompt,
    build_storyboard,
    distill_brief,
    export_csv,
    export_json,
    export_markdown,
    export_srt,
    generate_landmarks,
    generate_style,
    render_shot_prompts,
    write_script,
)
from app.engines.promo.common import (
    PROMO_ANGLES,
    VALID_ANGLES,
    plan_dict,
    shot_dict,
)
from app.engines.media.directions import VALID_DIRECTIONS
from app.api.sse import STREAM_HEADERS, sse_event
from app.jobs import list_running, spawn_job

logger = logging.getLogger("jarvis-write.promo")

router = APIRouter(prefix="/api/promos", tags=["promo"], dependencies=[Depends(get_current_user)])

_PROMO_ERRORS = (
    PromoAssetError,
    PromoChunkError,
    PromoBriefError,
    PromoChatError,
    PromoPackError,
    PromoPromptError,
    PromoScriptError,
    PromoStoryboardError,
)


# =============== 请求模型 ===============

class PromoCreateIn(BaseModel):
    subject: str = Field(min_length=1, max_length=120)
    title: str = ""
    angles: list[str] = []
    duration_s: int = Field(default=90, ge=30, le=180)
    direction: str = "live"


class PromoPatchIn(BaseModel):
    subject: str | None = None
    title: str | None = None
    angles: list[str] | None = None
    duration_s: int | None = Field(default=None, ge=30, le=180)
    direction: str | None = None
    material_notes: str | None = None
    brief: dict | None = None
    brief_locked: bool | None = None
    landmarks: list[dict] | None = None


class ChatIn(BaseModel):
    messages: list[dict] = []


class ChunksIn(BaseModel):
    chunk_s: int = Field(default=15)


class FilmPromptIn(BaseModel):
    """整片提示词手动保存:整段替换(粘贴自己写的版本也走这里)。"""
    film_prompt: str = ""


class ShotIn(BaseModel):
    scene_name: str | None = None
    action_desc: str | None = None
    shot_type: str | None = None
    camera: str | None = None
    dialogue: str | None = None
    duration_s: int | None = Field(default=None, ge=1, le=10)
    prompt_cn: str | None = None
    prompt_en: str | None = None
    negative: str | None = None


# =============== 工具 ===============

def _get_plan(db: Session, plan_id: int) -> PromoPlan:
    plan = db.get(PromoPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="企划不存在")
    assert_project_owner(plan)  # 通用:user_id 比对(与项目同款隔离语义)
    return plan


def _existing_job(kind: str) -> dict | None:
    for jid, job in list_running("promo-"):
        if job["kind"] == kind:
            return {"job_id": jid}
    return None


def _plan_job(plan_id: int, action: str, engine_fn):
    """企划级 async job 公共件:去重 + 独立会话重载执行。"""
    kind = f"promo-{action}-{plan_id}"
    if (existing := _existing_job(kind)):
        return existing

    async def work(progress):
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            plan = session.get(PromoPlan, plan_id)
            if plan is None:
                raise ValueError("企划已被删除,任务取消。")
            return await engine_fn(session, plan, progress)

    return {"job_id": spawn_job(kind, work)}


# =============== 目录 / CRUD ===============

@router.get("/meta")
async def promo_meta():
    from app.engines.media.directions import DIRECTIONS

    return {
        "angles": PROMO_ANGLES,
        "directions": [
            {"key": d["key"], "label": d["label"], "tip": d["tip"]}
            for d in DIRECTIONS
        ],
    }


@router.get("")
async def list_promos(db: Session = Depends(get_db)):
    from app.auth import current_user_id

    uid = current_user_id.get()
    rows = (
        db.query(PromoPlan)
        .filter(PromoPlan.user_id == uid)
        .order_by(PromoPlan.updated_at.desc())
        .all()
        if uid is not None
        else []
    )
    # 列表瘦身:不带 chat_log/script/pack 大字段
    slim = []
    for r in rows:
        d = plan_dict(r)
        slim.append(
            {k: d[k] for k in (
                "id", "subject", "title", "angles", "duration_s", "direction",
                "direction_label", "status", "brief_locked",
            )}
        )
    return {"plans": slim}


@router.post("")
async def create_promo(body: PromoCreateIn, db: Session = Depends(get_db)):
    from app.auth import current_user_id

    if body.direction not in VALID_DIRECTIONS:
        raise HTTPException(status_code=400, detail=f"未知画风方向:{body.direction}")
    bad = [a for a in body.angles if a not in VALID_ANGLES]
    if bad:
        raise HTTPException(status_code=400, detail=f"未知角度:{','.join(bad)}")
    plan = PromoPlan(
        user_id=current_user_id.get(),
        subject=body.subject.strip()[:120],
        title=body.title.strip()[:200],
        angles=body.angles,
        duration_s=body.duration_s,
        direction=body.direction,
    )
    db.add(plan)
    db.commit()
    return {"plan": plan_dict(plan)}


@router.get("/{plan_id}")
async def get_promo(plan_id: int, db: Session = Depends(get_db)):
    plan = _get_plan(db, plan_id)
    shots = (
        db.query(PromoShot)
        .filter(PromoShot.promo_id == plan.id)
        .order_by(PromoShot.seq)
        .all()
    )
    return {"plan": plan_dict(plan), "shots": [shot_dict(s) for s in shots]}


@router.patch("/{plan_id}")
async def patch_promo(plan_id: int, body: PromoPatchIn, db: Session = Depends(get_db)):
    plan = _get_plan(db, plan_id)
    if body.subject is not None:
        if not body.subject.strip():
            raise HTTPException(status_code=400, detail="主题不能为空。")
        plan.subject = body.subject.strip()[:120]
    if body.title is not None:
        plan.title = body.title.strip()[:200]
    if body.angles is not None:
        bad = [a for a in body.angles if a not in VALID_ANGLES]
        if bad:
            raise HTTPException(status_code=400, detail=f"未知角度:{','.join(bad)}")
        plan.angles = body.angles
    if body.duration_s is not None:
        plan.duration_s = body.duration_s
    if body.direction is not None:
        if body.direction not in VALID_DIRECTIONS:
            raise HTTPException(status_code=400, detail=f"未知画风方向:{body.direction}")
        plan.direction = body.direction
    if body.material_notes is not None:
        plan.material_notes = body.material_notes[:8000]
    if body.brief is not None:
        if not isinstance(body.brief, dict):
            raise HTTPException(status_code=400, detail="brief 需为对象。")
        plan.brief = body.brief
    if body.brief_locked is not None:
        plan.brief_locked = body.brief_locked
        if body.brief_locked and plan.status == "draft" and (plan.brief or {}).get("positioning"):
            plan.status = "briefed"
    if body.landmarks is not None:
        clean = []
        for l in body.landmarks[:8]:
            if isinstance(l, dict) and str(l.get("name") or "").strip():
                clean.append(
                    {
                        "name": str(l["name"]).strip()[:200],
                        "appearance_cn": str(l.get("appearance_cn") or "")[:400],
                        "appearance_en": str(l.get("appearance_en") or "")[:300],
                    }
                )
        plan.landmarks = clean
    db.commit()
    return {"plan": plan_dict(plan)}


@router.delete("/{plan_id}")
async def delete_promo(plan_id: int, db: Session = Depends(get_db)):
    plan = _get_plan(db, plan_id)
    db.query(PromoShot).filter(PromoShot.promo_id == plan.id).delete(
        synchronize_session=False
    )
    db.delete(plan)
    db.commit()
    return {"ok": True}


# =============== 研讨对话(SSE 流式) ===============

@router.post("/{plan_id}/chat")
async def promo_chat(plan_id: int, body: ChatIn, db: Session = Depends(get_db)):
    from app.engines.promo.chat import build_chat_messages, chat_stream

    plan = _get_plan(db, plan_id)
    # 先持久化用户侧消息(校验在 build_chat_messages 里做,这里先同步校验一次拿错误)
    try:
        build_chat_messages(plan, body.messages)
    except _PROMO_ERRORS as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    turns = [
        {"role": m["role"], "text": str(m.get("content") or m.get("text") or "").strip()}
        for m in body.messages
        if isinstance(m, dict)
        and m.get("role") in ("user", "assistant")
        and str(m.get("content") or m.get("text") or "").strip()
    ][-20:]
    plan.chat_log = turns
    db.commit()

    async def gen():
        try:
            reply = ""
            async for kind, payload in chat_stream(plan, turns):
                if kind == "token":
                    reply += payload
                    yield sse_event("token", {"text": payload})
                else:  # done
                    reply = payload.get("reply", reply)
                    yield sse_event("done", payload)
        except _PROMO_ERRORS as exc:
            yield sse_event("error", {"detail": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            yield sse_event("error", {"detail": f"对话失败: {exc}"})
            return
        # 对话完成:回复落库。请求级 session 在流式响应尾部的提交时机不可靠
        # (实测会被响应生命周期吞掉),改用独立会话按 id 重载落库——与生成类 job 同一纪律。
        try:
            from app.db.session import SessionLocal

            with SessionLocal() as session:
                fresh = session.get(PromoPlan, plan_id)
                if fresh is not None:
                    fresh.chat_log = turns + [{"role": "assistant", "text": reply}]
                    session.commit()
        except Exception:  # noqa: BLE001 — 落库失败不吞掉已推给用户的内容
            logger.warning("企划 %s 研讨回复落库失败", plan_id, exc_info=True)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=STREAM_HEADERS)


# =============== 生成类(async job) ===============

@router.post("/{plan_id}/brief")
async def promo_brief(plan_id: int, db: Session = Depends(get_db)):
    _get_plan(db, plan_id)
    return _plan_job(plan_id, "brief", distill_brief)


@router.post("/{plan_id}/style")
async def promo_style(plan_id: int, db: Session = Depends(get_db)):
    _get_plan(db, plan_id)
    return _plan_job(plan_id, "style", generate_style)


@router.post("/{plan_id}/landmarks")
async def promo_landmarks(plan_id: int, db: Session = Depends(get_db)):
    _get_plan(db, plan_id)
    return _plan_job(plan_id, "landmarks", generate_landmarks)


@router.post("/{plan_id}/script")
async def promo_script(plan_id: int, db: Session = Depends(get_db)):
    _get_plan(db, plan_id)
    return _plan_job(plan_id, "script", write_script)


@router.post("/{plan_id}/storyboard")
async def promo_storyboard(plan_id: int, db: Session = Depends(get_db)):
    _get_plan(db, plan_id)
    return _plan_job(plan_id, "board", build_storyboard)


@router.post("/{plan_id}/prompts")
async def promo_prompts(plan_id: int, db: Session = Depends(get_db)):
    _get_plan(db, plan_id)
    return _plan_job(plan_id, "prompts", render_shot_prompts)


@router.post("/{plan_id}/pack")
async def promo_pack(plan_id: int, db: Session = Depends(get_db)):
    _get_plan(db, plan_id)
    return _plan_job(plan_id, "pack", build_pack)


class _ChunksAction:
    """把 chunk_s 参数绑进 engine_fn(plan, progress) 签名的适配器。"""

    def __init__(self, chunk_s: int):
        self.chunk_s = chunk_s

    async def __call__(self, session, plan, progress):
        return await build_chunks(session, plan, self.chunk_s, progress)


@router.post("/{plan_id}/chunks")
async def promo_chunks(plan_id: int, body: ChunksIn | None = None, db: Session = Depends(get_db)):
    """生成切段:镜头边界贪心聚段(≤chunk_s 秒)+ 每段视频提示词/首帧指引/拼接提示。"""
    _get_plan(db, plan_id)
    chunk_s = (body.chunk_s if body else 15) or 15
    if chunk_s not in (5, 10, 15):
        raise HTTPException(status_code=400, detail="切段时长只支持 5/10/15 秒。")
    return _plan_job(plan_id, "chunks", _ChunksAction(chunk_s))


# ---- 整片提示词(端到端音频原生视频模型) ----


@router.post("/{plan_id}/film-prompt")
async def promo_film_prompt(plan_id: int, db: Session = Depends(get_db)):
    """把分镜+解说词+地标卡组装成一条「一次出一整片」的成片提示词(覆盖旧稿)。"""
    _get_plan(db, plan_id)
    return _plan_job(plan_id, "film-prompt", build_promo_film_prompt)


@router.get("/{plan_id}/film-prompt")
async def get_promo_film_prompt(plan_id: int, db: Session = Depends(get_db)):
    plan = _get_plan(db, plan_id)
    return {"film_prompt": plan.film_prompt or ""}


@router.put("/{plan_id}/film-prompt")
async def save_promo_film_prompt(
    plan_id: int, body: FilmPromptIn, db: Session = Depends(get_db)
):
    """整段替换保存:手改后的稿子、或用户自己写的版本都存这一列。"""
    plan = _get_plan(db, plan_id)
    plan.film_prompt = (body.film_prompt or "").strip()
    db.commit()
    return {"film_prompt": plan.film_prompt}


# =============== 分镜手动编辑 ===============

@router.patch("/{plan_id}/shots/{shot_id}")
async def patch_shot(plan_id: int, shot_id: int, body: ShotIn, db: Session = Depends(get_db)):
    _get_plan(db, plan_id)
    shot = (
        db.query(PromoShot)
        .filter(PromoShot.id == shot_id, PromoShot.promo_id == plan_id)
        .first()
    )
    if shot is None:
        raise HTTPException(status_code=404, detail="分镜不存在。")
    for field, limit in (
        ("scene_name", 200), ("shot_type", 20), ("camera", 20),
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(shot, field, value.strip()[:limit])
    for field in ("action_desc", "dialogue", "prompt_cn", "prompt_en", "negative"):
        value = getattr(body, field)
        if value is not None:
            setattr(shot, field, value)
    if body.duration_s is not None:
        shot.duration_s = body.duration_s
    db.commit()
    return {"shot": shot_dict(shot)}


# =============== 导出 ===============

@router.get("/{plan_id}/export")
async def export_promo(plan_id: int, format: str = "md", db: Session = Depends(get_db)):
    plan = _get_plan(db, plan_id)
    shots = (
        db.query(PromoShot)
        .filter(PromoShot.promo_id == plan.id)
        .order_by(PromoShot.seq)
        .all()
    )
    base = plan.title or plan.subject or f"promo-{plan.id}"
    if format == "csv":
        content, media, name = export_csv(shots), "text/csv; charset=utf-8", f"{base}-分镜.csv"
    elif format == "srt":
        content, media, name = export_srt(shots), "application/x-subrip; charset=utf-8", f"{base}-字幕.srt"
    elif format == "json":
        content, media, name = export_json(plan, shots), "application/json; charset=utf-8", f"{base}.json"
    else:
        content, media, name = export_markdown(plan, shots), "text/markdown; charset=utf-8", f"{base}-拍摄手册.md"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )
