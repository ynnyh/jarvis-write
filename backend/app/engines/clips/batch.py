# app/engines/clips/batch.py
# -*- coding: utf-8 -*-
"""情绪短片批产:一次 LLM 出三个不同切入的本子(通用版/小说衍生版同一入口)。

确定性部分:归一化(镜头数/时长收敛)、切段分组(复用 common.group_chunks)、
画风锚兜底、小说衍生版的金句溯源校验(quote_source 必须能在提供的正文节选里找到,
找不到进 cautions——聊天窗口给不了的纪律)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Chapter, DramaCharacterCard, MoodClip, Project
from app.engines.consistency.extractor import parse_llm_json
from app.engines.drama.common import coerce_int, direction_directive
from app.engines.clips.common import group_chunks, shot_hint, theme_label
from app.llm.router import Task, get_adapter_for
from app.prompts.clips import CLIPS_BATCH_PROMPT, CLIPS_NOVEL_PROMPT

_MAX_SHOTS = 7
_MAX_LINES = 10
# 小说衍生:节选最多取几章、每章截多长(字符)
_EXCERPT_CHAPTERS = 3
_EXCERPT_CHARS = 1200


class ClipBatchError(ValueError):
    """批产的业务性错误(信息直接上屏)。"""


# =============== 小说素材拼装 ===============

def _novel_context(db: Session, project: Project) -> tuple[str, str]:
    """返回 (正文节选块, 角色锚块)。节选取最新定稿章;角色锚优先用漫剧角色卡。"""
    rows = (
        db.query(Chapter.chapter_number, Chapter.final_content)
        .filter(Chapter.project_id == project.id, Chapter.status == "approved")
        .order_by(Chapter.chapter_number.desc())
        .limit(_EXCERPT_CHAPTERS)
        .all()
    )
    if not rows:
        raise ClipBatchError("这本书还没有已定稿章节——先写几章再来出投流短视频。")
    parts = []
    for n, content in sorted(rows):
        text = (content or "").strip()
        if text:
            parts.append(f"【第{n}章 节选】\n{text[:_EXCERPT_CHARS]}")
    excerpts = "\n\n".join(parts)

    cards = (
        db.query(DramaCharacterCard)
        .filter(DramaCharacterCard.project_id == project.id)
        .order_by(DramaCharacterCard.id)
        .limit(4)
        .all()
    )
    char_lines = [
        f"【{c.name}】{c.appearance_cn}" + (f"\n  EN: {c.appearance_en}" if c.appearance_en else "")
        for c in cards
        if c.appearance_cn
    ]
    characters = "\n".join(char_lines) if char_lines else "(无角色卡,按节选中人物自行合理设计并保持一致)"
    return excerpts, characters


def _concept_line(project: Project) -> str:
    c = project.concept if isinstance(project.concept, dict) else {}
    logline = str(c.get("logline") or "").strip()
    return f"【一句话故事】{logline}\n" if logline else ""


# =============== 归一化 ===============

def _norm_shots(raw, style: dict, max_seq_cap: int) -> list[dict]:
    out = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action_desc") or "").strip()
        if not action:
            continue
        prompt_cn = str(item.get("prompt_cn") or "").strip()[:800]
        prompt_en = str(item.get("prompt_en") or "").strip()[:600]
        negative = str(item.get("negative") or "").strip()[:400]
        # 画风锚兜底(与漫剧/宣传片同纪律)
        if style.get("style_cn") and style["style_cn"] not in prompt_cn:
            prompt_cn = f"【画风锚】{style['style_cn']}。{prompt_cn}"
        if style.get("style_en") and style["style_en"] not in prompt_en:
            prompt_en = f"{style['style_en']}, {prompt_en}"
        base = style.get("negative") or ""
        if base and base not in negative:
            negative = f"{base},{negative}" if negative else base
        out.append(
            {
                "seq": len(out) + 1,
                "scene_name": str(item.get("scene_name") or "").strip()[:200],
                "characters": [
                    str(c).strip() for c in (item.get("characters") or []) if str(c or "").strip()
                ][:2],
                "action_desc": action[:200],
                "shot_type": str(item.get("shot_type") or "").strip()[:20],
                "camera": str(item.get("camera") or "").strip()[:20],
                "dialogue": str(item.get("dialogue") or "").strip()[:200],
                "duration_s": coerce_int(item.get("duration_s"), 3, lo=1, hi=8),
                "prompt_cn": prompt_cn,
                "prompt_en": prompt_en,
                "negative": negative,
            }
        )
        if len(out) >= max_seq_cap:
            break
    return out


def _quote_grounded(quote: str, excerpts: str) -> bool:
    """金句溯源:原句(去空白)需能在节选(去空白)里找到。"""
    q = "".join(str(quote or "").split())
    if not q:
        return False
    body = "".join(excerpts.split())
    return q in body


def _normalize_clips(data: dict, duration_s: int, excerpts: str = "") -> list[dict]:
    style = {
        "style_cn": str(data.get("style_cn") or "").strip(),
        "style_en": str(data.get("style_en") or "").strip(),
        "negative": str(data.get("negative") or "").strip(),
    }
    max_shots = 5 if duration_s <= 15 else _MAX_SHOTS
    clips = []
    for item in (data.get("clips") or []):
        if not isinstance(item, dict):
            continue
        logline = str(item.get("logline") or "").strip()[:200]
        shots = _norm_shots(item.get("shots"), style, max_shots)
        if not logline or not shots:
            continue
        lines = []
        for l in (item.get("lines") or []):
            if not isinstance(l, dict):
                continue
            text = str(l.get("text") or "").strip()[:120]
            if text:
                lines.append(
                    {
                        "speaker": str(l.get("speaker") or "旁白").strip()[:40],
                        "text": text,
                        "action": str(l.get("action") or "").strip()[:100],
                    }
                )
            if len(lines) >= _MAX_LINES:
                break
        cautions = []
        quote_source = str(item.get("quote_source") or "").strip()[:300]
        if excerpts:
            if not quote_source:
                cautions.append("未给出金句原句,请人工核对是否出自正文")
            elif not _quote_grounded(quote_source, excerpts):
                cautions.append(f"金句原句未在正文节选中找到,请核实:{quote_source[:60]}")
        total = sum(s["duration_s"] for s in shots)
        if abs(total - duration_s) > max(6, duration_s // 3):
            cautions.append(f"分镜总时长 {total}s 与目标 {duration_s}s 偏差较大,拼接时注意")
        clips.append(
            {
                "take": str(item.get("take") or "").strip()[:60] or f"切入{len(clips) + 1}",
                "logline": logline,
                "emotion_curve": str(item.get("emotion_curve") or "").strip()[:120],
                "lines": lines,
                "shots": shots,
                "punchline": str(item.get("punchline") or "").strip()[:60],
                "chunks": group_chunks(shots, 15),
                "hook_text": str(item.get("hook_text") or "").strip()[:60],
                "quote_source": quote_source,
                "cautions": cautions,
            }
        )
        if len(clips) >= 3:
            break
    return clips


# =============== 批产入口 ===============

async def generate_batch(db: Session, clip: MoodClip, progress=lambda s: None) -> dict:
    """一次产三个本子:通用命题或小说衍生,按 source_project_id 分流。"""
    project = None
    excerpts = ""
    characters = ""
    concept_line = ""
    title = clip.custom_theme or ""
    genre = "不限"
    topic = ""

    if clip.source_project_id:
        project = db.get(Project, clip.source_project_id)
        if project is None:
            raise ClipBatchError("源项目不存在(可能已删除)。")
        title, genre = project.title, (project.genre or "不限")
        topic = (project.topic or "").strip()
        concept_line = _concept_line(project)
        excerpts, characters = _novel_context(db, project)

    inspiration_block = (
        f"【用户灵感种子(三个本子都要围着它生长,不可偏离)】{clip.inspiration.strip()}\n"
        if clip.inspiration.strip()
        else ""
    )
    adapter = get_adapter_for(Task.CLIPS_BATCH, timeout=300)
    if project is not None:
        progress(f"AI 正在从《{title}》里挑金句名场面,产 3 个投流本子…")
        prompt = CLIPS_NOVEL_PROMPT.format(
            title=title,
            genre=genre,
            topic=topic or "(未定)",
            concept_block=concept_line,
            excerpts_block=excerpts,
            characters_block=characters,
            duration_s=clip.duration_s,
            direction_directive=direction_directive(clip.direction or "live"),
            inspiration_block=inspiration_block,
            shot_hint=shot_hint(clip.duration_s),
        )
    else:
        if not (clip.theme or clip.custom_theme.strip()):
            raise ClipBatchError("先选一个情绪主题(或填自定义主题)。")
        progress("AI 正在产 3 个不同切入的本子…")
        prompt = CLIPS_BATCH_PROMPT.format(
            theme_label=theme_label(clip),
            duration_s=clip.duration_s,
            inspiration_block=inspiration_block,
            direction_directive=direction_directive(clip.direction or "live"),
            shot_hint=shot_hint(clip.duration_s),
        )

    raw = await adapter.ask(prompt)
    data = parse_llm_json(raw)
    candidates = _normalize_clips(data, clip.duration_s, excerpts=excerpts)
    if len(candidates) < 2:
        raise ClipBatchError("候选本子过少,请重试。")

    clip.style_name = str(data.get("style_name") or "").strip()[:60]
    clip.style_cn = str(data.get("style_cn") or "").strip()[:400]
    clip.style_en = str(data.get("style_en") or "").strip()[:400]
    clip.negative = str(data.get("negative") or "").strip()[:300]
    clip.candidates = candidates
    clip.chosen = -1
    clip.clip = {}
    clip.status = "generated"
    db.commit()
    return {"candidates": candidates, "style_name": clip.style_name}


def pick_clip(db: Session, clip: MoodClip, index: int) -> dict:
    """选定第 index 个候选(0 起)为最终本子。"""
    candidates = clip.candidates or []
    if not (0 <= index < len(candidates)):
        raise ClipBatchError(f"候选序号无效:{index}(共 {len(candidates)} 个)")
    clip.chosen = index
    clip.clip = candidates[index]
    clip.status = "picked"
    db.commit()
    from app.engines.clips.common import clip_dict

    return clip_dict(clip)
