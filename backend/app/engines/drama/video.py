# app/engines/drama/video.py
# -*- coding: utf-8 -*-
"""分镜 → 视频:图生视频 / 文生视频提示词 + 「一次最多 N 秒」的视频段计划。

为什么单独一层(生图提示词直接拿去生视频是错的):
- 生图要「把一帧画满」——外貌锚 + 场景 + 光影 + 画风,越具体越好;
- **图生视频**只该写「怎么动」:首帧图已经把长相钉死了,提示词里再描述一遍
  五官服饰,模型会照着文字把脸重画一遍(实测最常见的翻车)。所以 i2v 这一版
  **刻意不带外貌锚**,只留运动 + 镜头 + 幅度;
- **文生视频**没有首帧,外貌与画风只能全靠文字,于是反过来必须自带锚段
  (= 出图提示词 + 运动句),否则同一个角色每段一张脸。

还有个绕不开的现实:所有视频站都有**单次生成时长上限**(常见 5 / 10 / 15 秒),
而我们的分镜格是 2-8 秒。用户的实际做法是「一次生成十几秒,再在画布/时间线上
拼」,所以这里同时负责把相邻格并成不超上限的**视频段**,并如实标出哪一段超了
上限、该怎么接——宁可写明「这段得分两次生成」,也不假装一次能出来。

本模块全部确定性(不碰 LLM/DB):运动段由出提示词那一步顺带产出(见
prompts/drama.py 的 SHOT_PROMPT_PROMPT),模型漏给时这里按运镜栏兜底拼。
"""
from __future__ import annotations

from app.engines.drama.common import clip
from app.engines.media.audio import VIDEO_AUDIO_RULE_CN, VIDEO_AUDIO_RULE_EN
from app.engines.media.segments import group_by_limit

# 单次生成时长上限:各站常见档位(即梦/可灵多为 5-10 秒,Sora/Veo/Runway 到 15 秒)
CLIP_LIMITS: tuple[int, ...] = (5, 10, 15)
CLIP_LIMIT_DEFAULT = 10

# 视频特有负面词:生图那套(多手指/水印)之外,是「动起来」才会有的坏毛病
VIDEO_NEGATIVE_CN = (
    "人脸变形、五官漂移、中途换脸,肢体穿模、手指增减,画面闪烁抖动,"
    "物体突然出现或消失,镜头剧烈晃动,速度忽快忽慢,画风中途改变,"
    "出现文字、字幕、水印、logo"
)
VIDEO_NEGATIVE_EN = (
    "face morphing, identity change, distorted face, extra limbs, "
    "flickering, jitter, sudden cut, warping objects, violent camera shake, "
    "speed ramping, style change, text, subtitles, watermark, logo"
)

# 运镜 → 视频站的镜头词(生图那套构图词在视频站不吃)
_CAMERA_EN: dict[str, str] = {
    "固定": "static camera, locked off",
    "推": "slow push in, dolly in",
    "拉": "slow pull back, dolly out",
    "摇": "slow pan",
    "跟随": "tracking shot following the subject",
    "环绕": "slow orbit around the subject",
}

VIDEO_PLATFORMS: tuple[tuple[str, str], ...] = (
    ("i2v", "图生视频·中文站(即梦 / 可灵 / 海螺:传首帧图 + 粘这段)"),
    ("i2v_en", "图生视频·英文站(Runway / Luma / Pika)"),
    ("t2v", "文生视频(Sora / Veo / 可灵文生:没有首帧,慎用)"),
)
DEFAULT_VIDEO_PLATFORM = "i2v"


def camera_en(camera: str) -> str:
    """运镜中文 → 英文镜头词(白名单外的值给个中性兜底,不硬翻)。"""
    return _CAMERA_EN.get((camera or "").strip(), "steady camera")


def motion_fallback(shot) -> tuple[str, str]:
    """模型没给运动段时确定性拼一条(镜头照运镜栏,主体动作照本格画面)。

    宁可拼得朴素也不能空:这一句是图生视频**唯一**的输入,空着等于把活推回给用户。
    """
    cam = (shot.camera or "").strip() or "固定"
    act = clip(getattr(shot, "action_desc", ""), 60) or "保持姿态,只有呼吸与衣料的细微浮动"
    cn = f"镜头{cam};{act};幅度小、速度平稳,人物长相与服饰保持不变"
    en = (
        f"{camera_en(cam)}, subtle natural motion, slow steady pace, "
        "keep the character identity and outfit unchanged"
    )
    return cn, en


def motion_tracks(shot) -> tuple[str, str]:
    """取这一格的运动段(中/英),缺哪条补哪条。"""
    cn = (getattr(shot, "motion_cn", "") or "").strip()
    en = (getattr(shot, "motion_en", "") or "").strip()
    if cn and en:
        return cn, en
    fb_cn, fb_en = motion_fallback(shot)
    return cn or fb_cn, en or fb_en


def _duration_line(duration_s: int, limit_s: int) -> str:
    """时长指令:站点档位往往是 5/10 秒固定档,说清「选不到就选更短的」。"""
    d = max(1, int(duration_s or 0))
    if d > limit_s:
        return (
            f"时长 {d} 秒——**超过单次上限 {limit_s} 秒**,这一段要分两次生成再拼:"
            f"先生成 {limit_s} 秒,取尾帧当第二次的首帧,接着生成剩下 {d - limit_s} 秒。"
        )
    return f"时长 {d} 秒(站点只有固定档位时,选**不超过** {d} 秒的最接近档,宁短不长)"


def _join(*blocks: str) -> str:
    return "\n\n".join(b.strip() for b in blocks if b and b.strip())


def video_negative(style_negative: str = "") -> str:
    """视频负面词 = 视频特有项 + 画风卡的负面词基座(去重靠包含判断)。"""
    base = (style_negative or "").strip()
    if not base:
        return VIDEO_NEGATIVE_CN
    return f"{VIDEO_NEGATIVE_CN},{base}"


def video_paste(
    *,
    motion_cn: str,
    motion_en: str,
    prompt_cn: str = "",
    camera: str = "",
    duration_s: int = 4,
    seq_label: str = "",
    style_negative: str = "",
    ratio: str = "9:16",
    limit_s: int = CLIP_LIMIT_DEFAULT,
    has_character: bool = True,
) -> dict[str, dict[str, str]]:
    """一格(或一段)的视频粘贴版,结构与生图粘贴版一致 {label,main,negative,hint}。

    三份对应三种拿法:i2v 中文站 / i2v 英文站 / t2v 文生视频。
    """
    neg = video_negative(style_negative)
    dur = _duration_line(duration_s, limit_s)
    frame = f"用{seq_label}出好的静帧当首帧图" if seq_label else "用这一格出好的静帧当首帧图"

    i2v_main = _join(
        f"【首帧】{frame}:人物长相、发型、服饰、画风**全部照首帧**,不许重画、不许换脸。",
        f"【怎么动】{motion_cn}",
        f"【镜头】{(camera or '固定').strip()},竖屏 {ratio}",
        f"【时长】{dur}",
        VIDEO_AUDIO_RULE_CN,
        f"【不要出现】{neg}",
    )
    i2v_en_main = _join(
        f"{motion_en}, {camera_en(camera)}, vertical {ratio}, "
        f"{max(1, int(duration_s or 0))}s, keep the first frame's character and style, "
        f"{VIDEO_AUDIO_RULE_EN}"
    )
    t2v_main = _join(
        (prompt_cn or "").strip(),
        f"【怎么动】{motion_cn}",
        f"【时长】{dur}",
        VIDEO_AUDIO_RULE_CN,
        f"【不要出现】{neg}",
    )
    t2v_hint = (
        "文生视频没有首帧,同一个角色**每段都可能换脸**——建议只用在空镜/氛围格,"
        "或者你还没出静帧、只想先看个大概的时候。"
        if has_character
        else "这一格没有人物,文生视频直接出也不会有换脸问题,可以省掉出静帧那一步。"
    )
    return {
        "i2v": {
            "label": dict(VIDEO_PLATFORMS)["i2v"],
            "main": i2v_main,
            "negative": "",
            "hint": "先在站点上传这一格的静帧当首帧图,再整段粘进提示词框;"
                    "负面词已改写成「不要出现」并入正文(有负面框的站可以剪出来单独粘)。",
        },
        "i2v_en": {
            "label": dict(VIDEO_PLATFORMS)["i2v_en"],
            "main": i2v_en_main,
            "negative": VIDEO_NEGATIVE_EN,
            "hint": "Runway/Luma/Pika 传首帧图后粘正文;有 negative prompt 框的把负面词粘过去。",
        },
        "t2v": {
            "label": dict(VIDEO_PLATFORMS)["t2v"],
            "main": t2v_main,
            "negative": "",
            "hint": t2v_hint,
        },
    }


def shot_video_paste(shot, style, limit_s: int = CLIP_LIMIT_DEFAULT) -> dict[str, dict[str, str]]:
    """分镜格的视频粘贴版(便利封装:从 DramaShot + 风格卡取料)。"""
    motion_cn, motion_en = motion_tracks(shot)
    return video_paste(
        motion_cn=motion_cn,
        motion_en=motion_en,
        prompt_cn=shot.prompt_cn or "",
        camera=shot.camera or "",
        duration_s=shot.duration_s or 4,
        seq_label=f"第 {shot.seq} 格",
        style_negative=(getattr(style, "negative", "") or "") if style is not None else "",
        ratio=(getattr(style, "ratio", "") or "9:16") if style is not None else "9:16",
        limit_s=limit_s,
        has_character=bool(shot.characters),
    )


# =============== 视频段计划(治「一次最多 15 秒,再在画布里拼」)===============

def normalize_limit(raw: object) -> int:
    """把用户给的上限收敛到常见档位(1-15 秒内自由值也放行,别替用户做决定)。"""
    try:
        n = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return CLIP_LIMIT_DEFAULT
    return max(1, min(60, n))


def _same_group(a, b, acc_s: int, limit_s: int, acc_dialogue: bool) -> bool:
    """能不能把 b 并进 a 所在的这一段。

    四条全满足才并:同场景 / 不引入新角色 / 加起来不超上限 /
    合并后最多一条台词(两句话挤一段,字幕节奏与口型都对不上)。
    """
    if (a.scene_name or "") != (b.scene_name or ""):
        return False
    if set(b.characters or []) - set(a.characters or []):
        return False
    if acc_s + (b.duration_s or 0) > limit_s:
        return False
    if acc_dialogue and (b.dialogue or "").strip():
        return False
    return True


def clip_plan(shots: list, limit_s: int = CLIP_LIMIT_DEFAULT) -> dict:
    """把分镜格并成「一次生成一段」的视频段,供画布/时间线拼接。

    产出的每段都标清:用哪一格的静帧当首帧、怎么动、几秒、要压什么字幕。
    单格本身就超上限的,不硬塞——标 over_limit 并给「同一首帧连生两段」的接法。

    并段走 `media.segments.group_by_limit` 的公共贪心内核(边界只落在镜头边界上),
    漫剧比宣传片/短片多三条内聚条件,靠 `can_join` 加严。
    """
    limit = normalize_limit(limit_s)

    def can_join(cur: list, s, acc_s: int) -> bool:
        acc_dia = any((x.dialogue or "").strip() for x in cur)
        return _same_group(cur[0], s, acc_s, limit, acc_dia)

    groups = group_by_limit(shots, limit, can_join=can_join)
    segments = [_segment(i, g, limit) for i, g in enumerate(groups, start=1)]

    total_s = sum(seg["duration_s"] for seg in segments)
    over = [seg["index"] for seg in segments if seg["over_limit"]]
    ready = [seg["index"] for seg in segments if seg["first_frame_ready"]]
    return {
        "limit_s": limit,
        "options": list(CLIP_LIMITS),
        "segments": segments,
        "totals": {
            "segments": len(segments),
            "duration_s": total_s,
            "over_limit": len(over),
            "extra_runs": sum(seg["runs"] - 1 for seg in segments),
            # 首帧图已挂上来的段数:图生视频真正的前置条件,没图那一段根本开不了工
            "first_frames_ready": len(ready),
        },
        "note": (
            f"一共 {len(segments)} 段、合计 {total_s} 秒。在画布/剪映里按段号顺序首尾相接,"
            "段与段之间默认不加转场(只在剪辑清单标了转场的地方加),字幕统一压 SRT。"
            + (f"其中第 {'、'.join(map(str, over))} 段单格就超过 {limit} 秒,要分两次生成再接。"
               if over else "")
            + (f"首帧图已就位 {len(ready)}/{len(segments)} 段"
               "(在分镜格里把出好的静帧挂上来,这里就会亮)。" if segments else "")
        ),
    }


def _segment(index: int, group: list, limit_s: int) -> dict:
    """一段的清单:段号、含哪几格、首帧用谁、怎么动、几秒、字幕。"""
    first = group[0]
    total = sum(s.duration_s or 0 for s in group)
    seqs = [s.seq for s in group]
    label = f"第 {seqs[0]} 格" if len(seqs) == 1 else f"第 {seqs[0]}-{seqs[-1]} 格"
    # 多格并一段:运动段按顺序串成一句话,让模型知道这一段里先后发生什么
    parts = [motion_tracks(s)[0] for s in group]
    motion = parts[0] if len(parts) == 1 else "先" + ";然后".join(parts)
    chars: list[str] = []
    for s in group:
        for name in (s.characters or []):
            if name not in chars:
                chars.append(str(name))
    dialogue = " / ".join((s.dialogue or "").strip() for s in group if (s.dialogue or "").strip())
    over = total > limit_s
    runs = -(-total // limit_s) if limit_s > 0 else 1  # 向上取整:这一段实际要生成几次
    return {
        "index": index,
        "seqs": seqs,
        "label": label,
        "scene_name": first.scene_name or "",
        "characters": chars,
        "duration_s": total,
        "runs": max(1, runs),
        "over_limit": over,
        # 首帧一律用这一段第一格的静帧(多格并段时后面几格的静帧留着做尾帧校验)
        "first_frame": f"第 {seqs[0]} 格的静帧",
        # 那一格的静帧到底挂上来了没有:挂了才能开工,所以段表里直接标出来
        # (老库/测试替身没有这两个属性 → 当没挂)
        "first_frame_ready": bool(
            getattr(first, "done_still", False) or getattr(first, "assets", None)
        ),
        "motion": motion,
        "dialogue": dialogue,
        "split_hint": (
            f"这一段 {total} 秒 > 单次上限 {limit_s} 秒:生成 {max(1, runs)} 次——"
            f"第一次用首帧出 {limit_s} 秒,之后每次都拿上一段的**尾帧**当首帧接着出;"
            "或者回去把这一格拆成两格,各出一张静帧更可控。"
            if over else ""
        ),
    }


def clips_payload(shots: list, style, limit_s: int = CLIP_LIMIT_DEFAULT) -> dict:
    """视频段计划 + 每段的粘贴版(前端与导出手册同一套料)。"""
    plan = clip_plan(shots, limit_s)
    by_seq = {s.seq: s for s in shots}
    limit = plan["limit_s"]
    for seg in plan["segments"]:
        first = by_seq.get(seg["seqs"][0])
        motion_en = " then ".join(
            motion_tracks(by_seq[q])[1] for q in seg["seqs"] if q in by_seq
        )
        seg["paste"] = video_paste(
            motion_cn=seg["motion"],
            motion_en=motion_en,
            prompt_cn=(first.prompt_cn or "") if first is not None else "",
            camera=(first.camera or "") if first is not None else "",
            duration_s=seg["duration_s"],
            seq_label=f"第 {seg['seqs'][0]} 格",
            style_negative=(getattr(style, "negative", "") or "") if style is not None else "",
            ratio=(getattr(style, "ratio", "") or "9:16") if style is not None else "9:16",
            limit_s=limit,
            has_character=bool(seg["characters"]),
        )
    return plan
