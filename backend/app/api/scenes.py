# app/api/scenes.py
# -*- coding: utf-8 -*-
"""场景接口:让作者能在生成过程中直接接手(§2.6 / D6)。

背景:场景级生成把「生成单元」从章降到了场景,好处是**着力面变小**——但这只在
作者能落到那个面上时才兑现。此前场景卡只存在于数据库和 prompt 里:作者看到第 3
场写歪了,唯一的手段是重写整章,于是整章里写得好的两场也跟着重抽一次签。

这里把四个动作暴露出来(全是确定性,不额外跑 LLM):

  · 列出本章场景卡 + 每场的正文/状态/验收记录(看板)
  · **改场景卡**(写之前或重写前):目标/冲突/情绪指令/张力档/出场人物——
    这是最有价值的干预点,因为场景卡是 prompt 的输入,改卡比改正文便宜得多
  · **改场景正文**(写之后):逐场手动修订,存版本快照
  · **单独重生成某一场**:定点重抽,不影响其他场

为什么单独放一个模块而不是塞进 chapters 子包:
  chapters 的语义是「整章」(生成/改稿/放行/版本),场景是**章内部**的单元。
  混在一起会让「章级动作」和「场级动作」的权限、互斥与版本语义缠在一起。
  独立模块让「作者接手」这条路径有清晰的边界。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Outline, Project, Scene
from app.db.session import get_db
from app.engines.common import get_outline

logger = logging.getLogger("jarvis-write.api.scenes")

router = APIRouter(
    prefix="/api/projects/{project_id}/scenes",
    tags=["scenes"],
    dependencies=[Depends(get_current_user)],
)

# 场景卡里允许作者改的字段。刻意不含 content/anchor/status——
# 那些是生成的产物或系统维护的元数据,不属于「场景卡」这一层。
CARD_FIELDS = (
    "title", "summary", "location", "characters",
    "goal", "conflict", "emotion_target", "tension_level", "target_words",
    "fact_hints",
)
_INT_FIELDS = ("tension_level", "target_words")
_LIST_FIELDS = ("characters", "fact_hints")


class SceneCardIn(BaseModel):
    """场景卡的局部更新(只传要改的字段;不传即保持原值)。"""

    title: str | None = None
    summary: str | None = None
    location: str | None = None
    characters: list[str] | None = None
    goal: str | None = None
    conflict: str | None = None
    emotion_target: str | None = None
    tension_level: int | None = Field(default=None, ge=1, le=5)
    target_words: int | None = Field(default=None, ge=300, le=8000)
    fact_hints: list[str] | None = None


class SceneTextIn(BaseModel):
    """场景正文的手动修订。"""

    content: str
    note: str = ""


def _scene_or_404(db: Session, project_id: int, scene_id: int) -> Scene:
    scene = (
        db.query(Scene)
        .filter(Scene.id == scene_id, Scene.project_id == project_id)
        .first()
    )
    if scene is None:
        raise HTTPException(status_code=404, detail="场景不存在")
    return scene


def _scene_out(scene: Scene, *, with_text: bool = True) -> dict:
    data = {
        "id": scene.id,
        "chapter_number": scene.chapter_number,
        "seq": scene.seq,
        "title": scene.title,
        "summary": scene.summary,
        "location": scene.location,
        "characters": list(scene.characters or []),
        "goal": scene.goal,
        "conflict": scene.conflict,
        "emotion_target": scene.emotion_target,
        "tension_level": scene.tension_level,
        "target_words": scene.target_words,
        "fact_hints": list(scene.fact_hints or []),
        "status": scene.status,
        "word_count": scene.word_count,
        "rewrite_count": scene.rewrite_count,
        "version": scene.version,
        "accept_note": scene.accept_note,
        # 验收分数里含检索统计等生成期信息,原样透出供「这一场参考了什么」回显
        "accept_scores": scene.accept_scores or {},
        "anchor_start": scene.anchor_start,
        "anchor_end": scene.anchor_end,
        "joins_previous": scene.joins_previous,
    }
    if with_text:
        data["content"] = scene.content or ""
    return data


@router.get("/{chapter_number}")
async def list_scenes(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """本章所有场景卡(按 seq 排序)。未分场时返回空列表 + 提示。

    不带正文(看板只要卡片);正文走 /scenes/{id}/text 或详情,避免一次拖回整章量级。
    """
    get_project_or_404(db, project_id)
    rows = (
        db.query(Scene)
        .filter(
            Scene.project_id == project_id,
            Scene.chapter_number == chapter_number,
        )
        .order_by(Scene.seq)
        .all()
    )
    return {
        "chapter_number": chapter_number,
        "scene_count": len(rows),
        "scenes": [_scene_out(s, with_text=False) for s in rows],
    }


@router.get("/detail/{scene_id}")
async def get_scene(project_id: int, scene_id: int, db: Session = Depends(get_db)):
    """单个场景的完整信息(含正文、验收记录)。

    路径用两段(`/detail/{id}`)而不是 `/{scene_id}`:后者会与上面的
    `GET /{chapter_number}` 同形(都是一段 + int),Starlette 靠注册顺序取胜,
    很容易在后续改动里被悄悄遮蔽。两段路径段数不同,匹配无歧义。
    有 `test_get_scene_detail_includes_text` 钉住。
    """
    get_project_or_404(db, project_id)
    scene = _scene_or_404(db, project_id, scene_id)
    return _scene_out(scene)


@router.patch("/{scene_id}")
async def update_scene_card(
    project_id: int, scene_id: int, req: SceneCardIn, db: Session = Depends(get_db)
):
    """改场景卡(§2.6 核心干预点)。

    为什么改卡比改正文划算:场景卡是生成 prompt 的输入——把「本场目标」改准了,
    重生成出来的正文才会对;而改正文是对症,下一场仍会歪。
    只允许改 CARD_FIELDS,系统维护的字段(content/status/anchor)不在此列。
    """
    get_project_or_404(db, project_id)
    scene = _scene_or_404(db, project_id, scene_id)

    changed: list[str] = []
    payload = req.model_dump(exclude_unset=True)
    for name in CARD_FIELDS:
        if name not in payload:
            continue
        value = payload[name]
        if value is None:
            continue
        if name in _INT_FIELDS:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        if name in _LIST_FIELDS:
            value = [str(v).strip() for v in value if str(v).strip()]
        if getattr(scene, name) != value:
            setattr(scene, name, value)
            changed.append(name)

    if changed:
        # 卡改了 → 该场已有正文即视为过时,标记回 planned 让作者决定是否重生成。
        # 不自动重写:改卡是意图表达,重生成是花钱的动作,该由作者点。
        if (scene.content or "").strip() and scene.status in ("drafted", "accepted"):
            scene.status = "planned"
        db.commit()
        logger.info(
            "第 %d 章第 %d 场场景卡改动:%s", scene.chapter_number, scene.seq, changed
        )
    return {"changed": changed, "scene": _scene_out(scene)}


@router.put("/{scene_id}/text")
async def update_scene_text(
    project_id: int, scene_id: int, req: SceneTextIn, db: Session = Depends(get_db)
):
    """手动改场景正文(存版本快照)。

    章的正文由各场拼接而成(`join_scenes` 用 anchor offset)——所以手改一场之后
    **必须回写整章的 final_content**,否则章正文与场景对不上(看板说改了,正文没变)。
    这里不自己拼:交给调用方在同一事务里走章级落库(见 chapters 的手改端点),
    本端点只负责「场这一层」的持久化与快照,避免两处都拼一遍正则。
    """
    get_project_or_404(db, project_id)
    scene = _scene_or_404(db, project_id, scene_id)

    from app.engines.pipeline.scene_write import snapshot_scene

    new_content = (req.content or "").strip()
    if new_content == (scene.content or "").strip():
        return {"changed": False, "scene": _scene_out(scene)}

    snapshot_scene(db, scene, source="edited", note=req.note or "手动编辑")
    scene.content = new_content
    scene.word_count = len(new_content)
    scene.version += 1
    if new_content:
        scene.status = "accepted"  # 作者手定的内容不再需要验收
    db.commit()
    return {"changed": True, "scene": _scene_out(scene)}


@router.post("/{scene_id}/regenerate")
async def regenerate_scene(
    project_id: int, scene_id: int, db: Session = Depends(get_db)
):
    """单独重生成某一场(定点重抽,不影响其他场)。

    这是场景级生成的兑现点:章级回炉是「整章重新抽签」,实测 prose 维 6→6→6
    烧满预算纹丝不动;这里只重抽一场,着力面小得多,也不会把写得好的场一起换掉。
    重写前留快照;写后写回章正文由前端随后触发的章级同步完成。
    """
    project = get_project_or_404(db, project_id)
    scene = _scene_or_404(db, project_id, scene_id)

    from app.engines.pipeline.scene_write import snapshot_scene, write_scene

    if (scene.content or "").strip():
        snapshot_scene(db, scene, source="rewritten", note="用户定点重生成")

    outline = get_outline(db, project.id, scene.chapter_number)
    if outline is None:
        raise HTTPException(status_code=409, detail="本章没有大纲,无法重生成场景")

    siblings = (
        db.query(Scene)
        .filter(
            Scene.project_id == project_id,
            Scene.chapter_number == scene.chapter_number,
        )
        .order_by(Scene.seq)
        .all()
    )
    total = len(siblings)
    previous = ""
    for s in siblings:
        if s.seq < scene.seq:
            previous = s.content or previous

    try:
        scene.content = await write_scene(
            db, project, scene,
            chapter_number=scene.chapter_number,
            scene_total=total,
            # 定点重生成走「最小上下文」:作者要的是把这一场改好,
            # 不是让模型借机重写相邻场,所以不带章级选装件
            style_block="",
            deai_rules="",
            rolling_summary="",
            recent_tail="",
            handoff_block="",
            scene_anchor=str(getattr(outline, "scene_anchor", "") or ""),
            chapter_summary=outline.summary,
            chapter_title=outline.title,
            previous_text=previous,
            outline=outline,
        )
    except Exception as exc:  # noqa: BLE001 — 失败要让前端看到原因,不是 500 白屏
        logger.warning("第 %d 场重生成失败:%s", scene.seq, exc)
        raise HTTPException(status_code=502, detail=f"重生成失败:{str(exc)[:150]}")

    scene.word_count = len(scene.content or "")
    scene.status = "drafted"
    scene.rewrite_count += 1
    scene.version += 1
    db.commit()
    return {"scene": _scene_out(scene)}
