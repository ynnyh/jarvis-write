# app/engines/pipeline/scene_chapter.py
# -*- coding: utf-8 -*-
"""场景级章节编排:按场景卡逐场生成 → 逐场验收 → 不合格只重写该场 → 拼成整章。

这是 generate_chapter 的「生成段」替代实现(阶段一拆解 + 阶段二场景化的落点)。
原实现是一次调用写整章,一次回炉重写整章;这里换成:

    切场 → [逐场:生成 → 验收 → (不合格: 重写,封顶 2 次)] → 拼接 → 交给原审校链路

保留原链路的其余部分不动(门禁、精修、字数守卫、去味、落库、章后链路)——
那些是「校验厚」的部分,它们是有效的;薄的是生成那一层,所以只换生成段。

为什么重写封顶 2 次而不是继续烧(D4):
  场景级重写的收益递减极快——第 3 次基本是同一份 prompt 再抽一次签。连续 2 次
  不过说明问题不在「模型这次没写好」,而在这张场景卡本身(目标/情绪指令与蓝图
  不匹配),那该由用户或重规划来解,不该继续烧钱。

失败隔离:
  单场生成抛异常 → 该场记为 failed,跳过验收,整章继续(最后按章级门禁兜底)。
  一场写废不该让整章白跑,更不该把已生成的其他场丢掉。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Project, Scene
from app.engines.pipeline.scene_plan import plan_scenes, scenes_of_chapter
from app.engines.pipeline.scene_write import (
    MAX_SCENE_REWRITES,
    SceneWriteResult,
    _build_scene_directive,
    accept_scene,
    join_scenes,
    snapshot_scene,
    write_scene,
)

logger = logging.getLogger("jarvis-write.scene_chapter")


async def compose_by_scenes(
    db: Session,
    project: Project,
    chapter_number: int,
    *,
    style_block: str,
    deai_rules: str,
    rolling_summary: str,
    recent_tail: str,
    handoff_block: str,
    outline_summary: str,
    outline_title: str,
    scene_anchor: str,
    threshold: int,
    outline=None,
    report=None,
    revision_directive: str = "",
    replan: bool = False,
) -> SceneWriteResult:
    """逐场生成并拼成整章正文。

    revision_directive:章级重写意见(来自门禁/主审回炉)。有值时不做逐场验收——
    这一轮的目标是「按意见改对」而不是「重新判定合格」,把预算花在改上。
    replan:强制重切场景(蓝图改过时用)。
    outline:本章大纲(用于判断是否转折章、是否注入反转预备;缺省则自行查)。

    返回 SceneWriteResult(text + scenes + anchors + verdicts + stats)。
    """
    if outline is None:
        try:
            outline = _outline_of(db, project, chapter_number)
        except ValueError:
            outline = None  # 无大纲极罕见(调用方已校验),反转预备缺失不等于生成失败

    def _report(stage: str) -> None:
        if report:
            try:
                report(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
                pass

    # ---- 切场(D2:蓝图阶段就切好;已有卡则复用,不重切) ----
    scenes = scenes_of_chapter(db, project.id, chapter_number)
    if not scenes or replan:
        _report("1/6 场景切分")
        scenes = await plan_scenes(db, project, _outline_of(db, project, chapter_number),
                                   rewrite=bool(scenes))
    if not scenes:
        # 极端情况:切分为空(理论上 plan_scenes 有兜底,这里再兜一层)
        logger.warning("第 %d 章场景为空,回落到空结果", chapter_number)
        return SceneWriteResult(text="", scenes=[], stats={"scene_count": 0})
    db.commit()

    total = len(scenes)

    # ---- 逐场生成 → 验收 → 定点重写 ----
    verdicts: list[dict] = []
    failed_scenes: list[int] = []
    for i, scene in enumerate(scenes, 1):
        label = f"2/6 场景生成({i}/{total})"
        _report(label)
        previous_text = (scenes[i - 2].content or "") if i > 1 else ""
        try:
            scene.content = await write_scene(
                db, project, scene,
                chapter_number=chapter_number,
                scene_total=total,
                style_block=style_block,
                deai_rules=deai_rules,
                rolling_summary=rolling_summary,
                recent_tail=recent_tail,
                handoff_block=handoff_block,
                scene_anchor=scene_anchor,
                chapter_summary=outline_summary,
                chapter_title=outline_title,
                previous_text=previous_text,
                outline=outline,
                # 章级重写意见只在第一场带一次,后续场靠「上一场尾部」自然接住
                revision_directive=revision_directive if i == 1 else "",
            )
        except Exception as exc:  # noqa: BLE001 — 单场失败不拖垮整章
            logger.warning("第 %d 章第 %d 场生成失败:%s", chapter_number, scene.seq, exc)
            scene.status = "rejected"
            scene.accept_note = f"生成失败:{str(exc)[:150]}"
            failed_scenes.append(scene.seq)
            db.commit()
            continue

        scene.word_count = len(scene.content or "")
        db.flush()

        # ---- 验收(章级重写轮不做验收:那一轮的目标是改对,不是重判) ----
        if revision_directive:
            scene.status = "drafted"
            db.commit()
            continue

        rewrites = 0
        while True:
            verdict = await accept_scene(db, scene, threshold)
            if verdict.get("degraded"):
                # 验收降级:不重写(重写解决不了解析问题),标待人工
                scene.status = "drafted"
                scene.accept_note = verdict.get("comment", "")
                break
            if verdict["passed"]:
                scene.status = "accepted"
                scene.accept_note = verdict.get("comment", "")
                break
            if rewrites >= MAX_SCENE_REWRITES:
                scene.status = "rejected"
                scene.accept_note = (
                    f"连续 {MAX_SCENE_REWRITES + 1} 次未通过("
                    f"{'/'.join(verdict.get('failing') or [])}),已接受当前版本"
                )
                failed_scenes.append(scene.seq)
                break
            rewrites += 1
            snapshot_scene(
                db, scene, source="rewritten",
                note=f"第 {rewrites} 次重写:{'/'.join(verdict.get('failing') or [])}",
            )
            _report(f"2/6 场景重写({i}/{total}·第 {rewrites} 次)")
            try:
                scene.content = await write_scene(
                    db, project, scene,
                    chapter_number=chapter_number,
                    scene_total=total,
                    style_block=style_block,
                    deai_rules=deai_rules,
                    rolling_summary=rolling_summary,
                    recent_tail=recent_tail,
                    handoff_block=handoff_block,
                    scene_anchor=scene_anchor,
                    chapter_summary=outline_summary,
                    chapter_title=outline_title,
                    outline=outline,
                    revision_directive=_build_scene_directive(verdict),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("第 %d 章第 %d 场重写失败:%s", chapter_number, scene.seq, exc)
                scene.status = "rejected"
                scene.accept_note = f"重写失败:{str(exc)[:150]}"
                failed_scenes.append(scene.seq)
                break
            scene.word_count = len(scene.content or "")
            db.flush()
        scene.rewrite_count = rewrites
        scene.version += rewrites
        scene.accept_scores = {**(scene.accept_scores or {}), "last_verdict": {
            k: v for k, v in verdict.items() if k != "suggestions"
        }}
        db.commit()
        verdicts.append({"seq": scene.seq, **verdict})

    # ---- 拼接 ----
    _report("2/6 场景拼接")
    text, anchors = join_scenes(scenes)
    for scene, (start, end) in zip(scenes, anchors):
        scene.anchor_start = start
        scene.anchor_end = end
    db.flush()
    db.commit()

    stats = {
        "scene_count": total,
        "accepted": sum(1 for s in scenes if s.status == "accepted"),
        "rejected": len(failed_scenes),
        "failed_seqs": failed_scenes,
        "rewrites": sum(s.rewrite_count for s in scenes),
        "words": len(text),
    }
    logger.info(
        "第 %d 章场景级生成完成:%d 场(通过 %d,未过 %d),重写 %d 次,共 %d 字",
        chapter_number, total, stats["accepted"], stats["rejected"],
        stats["rewrites"], stats["words"],
    )
    return SceneWriteResult(
        text=text, scenes=scenes, anchors=anchors, verdicts=verdicts, stats=stats
    )


def _outline_of(db: Session, project: Project, chapter_number: int):
    from app.engines.common import get_outline

    outline = get_outline(db, project.id, chapter_number)
    if outline is None:
        raise ValueError(f"第 {chapter_number} 章没有大纲,请先生成蓝图")
    return outline
