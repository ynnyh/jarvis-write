# app/engines/drama/exporter.py
# -*- coding: utf-8 -*-
"""拍摄手册导出:Markdown(人读)/ CSV(表格导入)/ JSON(程序消费)。

纯格式化,不碰 LLM/DB(数据由 API 层查好传入),方便单测。
"""
from __future__ import annotations

import csv
import io
import json

from app.db.models import (
    DramaCharacterCard,
    DramaEpisode,
    DramaSceneCard,
    DramaShot,
    DramaStyleCard,
    Project,
)
from app.engines.drama.common import (
    MODE_DESC,
    has_ref_image,
    ref_image_list,
    shot_asset_list,
    shot_progress,
    source_chapter_label,
)
from app.engines.drama.paste import ref_sheet_paste, shot_paste
from app.engines.drama.video import CLIP_LIMIT_DEFAULT, clips_payload, motion_tracks

_STATUS_CN = {
    "planned": "已规划",
    "scripted": "已有剧本",
    "storyboarded": "已有分镜",
    "ready": "提示词就绪",
}


def _shot_scenes(shots: list[DramaShot]) -> set[str]:
    return {s.scene_name for s in shots if s.scene_name}


def _shot_characters(shots: list[DramaShot]) -> set[str]:
    names: set[str] = set()
    for s in shots:
        names.update(s.characters or [])
    return names


def _ref_names(shot: DramaShot, cards: list[DramaCharacterCard]) -> list[str]:
    """本格里**已经有定妆照**的角色名(没有的不提,免得让用户去传不存在的图)。

    导出层按名直配(不查库、不走别名):分镜的 characters 本就来自角色卡清单,
    别名命中是渲染提示词那一步的事,这里只决定「手册上要不要写参考图指令」。
    """
    with_ref = {c.name for c in cards if has_ref_image(c)}
    return [n for n in (shot.characters or []) if n in with_ref]


def export_markdown(
    project: Project,
    episode: DramaEpisode,
    shots: list[DramaShot],
    style: DramaStyleCard | None,
    cards: list[DramaCharacterCard],
    scenes: list[DramaSceneCard],
) -> str:
    """整集拍摄手册(Markdown):拿去出图/剪辑照着走。"""
    used_chars = _shot_characters(shots)
    used_scenes = _shot_scenes(shots)
    L: list[str] = []
    L.append(f"# 《{project.title}》漫剧拍摄手册 · 第 {episode.ep_index} 集《{episode.title}》")
    L.append("")
    L.append(
        f"- 模式:{MODE_DESC.get(episode.mode, episode.mode)} | 目标时长:{episode.duration_target_s} 秒"
        f" | 源章节:{source_chapter_label(episode)} | 状态:{_STATUS_CN.get(episode.status, episode.status)}"
    )
    L.append(f"- 开场钩子:{episode.hook}")
    L.append(f"- 结尾卡点:{episode.cliffhanger}")
    if shots:
        # 施工进度:导出的手册常被当进度表用,做到哪儿一开头就得看见
        prog = shot_progress(shots)
        L.append(
            f"- 施工进度:静帧 {prog['stills_done']}/{prog['shots']} 格"
            f" | 视频 {prog['videos_done']}/{prog['shots']} 格"
            f" | 已挂素材 {prog['assets']} 张(在站内每格「挂静帧」处更新)"
        )
    L.append("")

    if style is not None:
        L.append("## 美术风格卡(全片统一)")
        L.append(f"- 风格:{style.style_name} | 画幅:{style.ratio}")
        L.append(f"- 画风锁定段(中文):{style.style_cn}")
        L.append(f"- 画风锁定段(英文):{style.style_en}")
        L.append(f"- 负面词基座:{style.negative}")
        L.append("")

    if cards:
        L.append("## 角色卡(本集出场)")
        for c in cards:
            if c.name not in used_chars:
                continue
            L.append(f"### {c.name}" + ("(已锁定)" if c.locked else ""))
            L.append(f"- 锁定外貌:{c.appearance_cn}")
            L.append(f"- 英文锚段:{c.appearance_en}")
            if c.outfit_cn:
                L.append(f"- 标志服饰:{c.outfit_cn}")
            if c.voice_desc:
                L.append(f"- 配音声线:{c.voice_desc}")
            imgs = ref_image_list(c)
            if c.ref_prompt_cn or imgs:
                L.append("")
                L.append("**定妆照(先出这张,再拿它当每格的参考图)**")
                L.append("")
                if imgs:
                    srcs = "、".join(
                        (i["src"] if i["kind"] == "url" else f"随手册目录 {i['src']}")
                        for i in imgs
                    )
                    L.append(f"- 已有参考图 {len(imgs)} 张:{srcs}")
                else:
                    L.append("- ⚠ 还没出参考图:先用下面这段生成一张,存好备用。")
                if c.ref_prompt_cn:
                    paste = ref_sheet_paste(c, style)
                    L.append("- 单框站(GPT-image / 豆包 / 通义)整段粘这个:")
                    L.append("")
                    L.append(paste["oneframe"]["main"])
                    L.append("")
                    if paste["mj"]["main"]:
                        L.append(f"- Midjourney:`{paste['mj']['main']}`")
                if c.ref_prompt_en and not c.ref_prompt_cn:
                    L.append(f"- 英文定妆照提示词:{c.ref_prompt_en}")
        L.append("")

    if scenes:
        L.append("## 场景卡(本集出场)")
        for sc in scenes:
            if sc.name not in used_scenes:
                continue
            L.append(f"### {sc.name}")
            L.append(f"- 定调描述:{sc.appearance_cn}")
            if sc.appearance_en:
                L.append(f"- 英文锚段:{sc.appearance_en}")
        L.append("")

    script = episode.script or {}
    lines = script.get("lines") or []
    if lines:
        L.append("## 剧本")
        for i, l in enumerate(lines, start=1):
            if isinstance(l, dict):
                L.append(f"{i}. **{l.get('speaker', '')}**:{l.get('text', '')}")
        L.append("")

    if shots:
        L.append("## 分镜表")
        L.append("| # | 场景 | 角色 | 景别 | 运镜 | 时长(s) | 画面 | 台词 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for s in shots:
            who = "、".join(s.characters or [])
            dia = (s.dialogue or "").replace("|", "/").replace("\n", " ")
            act = (s.action_desc or "").replace("|", "/").replace("\n", " ")
            L.append(
                f"| {s.seq} | {s.scene_name} | {who} | {s.shot_type} | {s.camera} "
                f"| {s.duration_s} | {act} | {dia} |"
            )
        L.append("")
        L.append("## 分镜提示词(按你用的生图站选一版粘)")
        L.append("")
        L.append(
            "> 生图站长相不一样:**只有一个描述框**的站(GPT-image / DALL·E / 豆包 / 通义)"
            "要用 ① ——负面词已改写成「不要出现」并进正文,直接整段粘;"
            "**有负面词框**的站(即梦 / 可灵 / SD)用 ②,正反分开粘;Midjourney 用 ③。"
        )
        L.append("")
        for s in shots:
            paste = shot_paste(s, style, _ref_names(s, cards))
            L.append(f"### 镜头 {s.seq}({s.shot_type}/{s.camera}/{s.duration_s}s)")
            L.append("")
            L.append("**① 单框站:整段粘这个**")
            L.append("")
            L.append(paste["oneframe"]["main"] or "(未生成)")
            L.append("")
            L.append("**② 有负面词框的站**")
            L.append("")
            L.append(f"- 正文:{paste['dualbox']['main'] or '(未生成)'}")
            L.append(f"- 负面词:{paste['dualbox']['negative'] or '(无)'}")
            L.append("")
            L.append("**③ Midjourney / Niji**")
            L.append("")
            L.append(paste["mj"]["main"] or "(没有英文轨,先出提示词)")
            L.append("")

        L.extend(_video_section(shots, style))

    return "\n".join(L)


def _video_section(shots: list[DramaShot], style: DramaStyleCard | None) -> list[str]:
    """让静帧动起来那一步:视频段计划(按单次时长上限并段)+ 每段的视频提示词。

    单列一节而不是混进出图那节:生图与生视频吃的提示词根本不是一回事(i2v 不许
    带外貌词,见 engines/drama/video.py),而且视频站有单次时长上限,得先并段。
    """
    plan = clips_payload(shots, style, CLIP_LIMIT_DEFAULT)
    L = ["## 让它动起来:视频段计划(按单次上限 %d 秒并段)" % plan["limit_s"], ""]
    L.append(
        "> 视频站单次只能出 5-15 秒,而分镜格是 2-8 秒——所以**一段生成一次,再在画布/"
        "剪映里按段号首尾相接**。首帧图用该段第一格出好的静帧,人物长相全靠它锁住;"
        "提示词里**刻意不写外貌**(写了模型会重画脸)。"
    )
    L.append("")
    L.append(f"> {plan['note']}")
    L.append("")
    L.append("| 段 | 含分镜 | 场景 | 角色 | 秒 | 生成次数 | 首帧 | 首帧图 | 这一段怎么动 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for seg in plan["segments"]:
        who = "、".join(seg["characters"]) or "(空镜)"
        motion = seg["motion"].replace("|", "/").replace("\n", " ")
        ready = "✓ 已挂" if seg["first_frame_ready"] else "待出图"
        L.append(
            f"| {seg['index']} | {'、'.join(map(str, seg['seqs']))} | {seg['scene_name']} "
            f"| {who} | {seg['duration_s']} | {seg['runs']} | {seg['first_frame']} "
            f"| {ready} | {motion} |"
        )
    L.append("")
    for seg in plan["segments"]:
        paste = seg["paste"]
        L.append(f"### 视频段 {seg['index']}(分镜 {'、'.join(map(str, seg['seqs']))},{seg['duration_s']}s)")
        L.append("")
        if seg["split_hint"]:
            L.append(f"> ⚠ {seg['split_hint']}")
            L.append("")
        L.append("**① 图生视频·中文站(即梦 / 可灵 / 海螺):传首帧图 + 整段粘**")
        L.append("")
        L.append(paste["i2v"]["main"])
        L.append("")
        L.append("**② 图生视频·英文站(Runway / Luma / Pika)**")
        L.append("")
        L.append(f"- 正文:{paste['i2v_en']['main']}")
        L.append(f"- 负面词:{paste['i2v_en']['negative']}")
        L.append("")
        L.append("**③ 文生视频(没有首帧,慎用)**")
        L.append("")
        L.append(paste["t2v"]["main"] or "(这一格还没有出图提示词,先出提示词)")
        L.append("")
        L.append(f"> {paste['t2v']['hint']}")
        L.append("")
        if seg["dialogue"]:
            L.append(f"> 这一段的字幕/配音:{seg['dialogue']}")
            L.append("")
    return L


def export_csv(
    episode: DramaEpisode,
    shots: list[DramaShot],
    style: DramaStyleCard | None = None,
    cards: list[DramaCharacterCard] | None = None,
) -> str:
    """分镜表 CSV(带 BOM,Excel 打开中文不乱码)。

    最后几列是「让它动起来」那一步的:clip 说明这一格属于第几个视频段(段内共用一次
    生成),paste_i2v 是那一段的图生视频提示词;still_asset 是你挂回来的静帧、
    clip_ref 是成片在哪,done_* 是两个打勾栏(站内勾过的这里就是 ✓,没勾的留空给你手打)。
    paste_oneframe 是「单框站直接粘贴版」(负面词已并入正文):
    批量出图的人普遍拿 Excel 一行行复制,没这列就得自己拼负面词。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    # 逐格施工单:出图三栏 + 运动轨两栏 + 段号/生成次数 + 素材两栏 + 两个打勾栏。
    # 打勾栏取站内的真实状态而不是一律留空——在站里勾过的进度,导出来还要重勾一遍就白勾了。
    writer.writerow(
        ["seq", "scene_name", "characters", "shot_type", "camera", "duration_s",
         "action_desc", "dialogue", "prompt_cn", "prompt_en", "negative",
         "paste_oneframe", "motion_cn", "motion_en", "clip", "clip_seqs",
         "clip_duration_s", "clip_runs", "paste_i2v", "still_asset", "clip_ref",
         "done_still", "done_video"]
    )
    # 段号按格反查:表是逐格的,而视频段是「几格并一段」,不给映射用户对不上号
    plan = clips_payload(shots, style, CLIP_LIMIT_DEFAULT)
    seg_of: dict[int, dict] = {}
    for seg in plan["segments"]:
        for q in seg["seqs"]:
            seg_of[q] = seg
    for s in shots:
        paste = shot_paste(s, style, _ref_names(s, cards or []))
        motion_cn, motion_en = motion_tracks(s)
        seg = seg_of.get(s.seq) or {}
        assets = shot_asset_list(s)
        writer.writerow(
            [s.seq, s.scene_name, "、".join(s.characters or []), s.shot_type,
             s.camera, s.duration_s, s.action_desc, s.dialogue,
             s.prompt_cn, s.prompt_en, s.negative, paste["oneframe"]["main"],
             motion_cn, motion_en,
             seg.get("index", ""), "、".join(str(q) for q in seg.get("seqs", [])),
             seg.get("duration_s", ""), seg.get("runs", ""),
             (seg.get("paste") or {}).get("i2v", {}).get("main", ""),
             "、".join(a["src"] for a in assets),
             getattr(s, "clip_ref", "") or "",
             "✓" if getattr(s, "done_still", False) else "",
             "✓" if getattr(s, "done_video", False) else ""]
        )
    return "\ufeff" + buf.getvalue()


def export_json(
    project: Project,
    episode: DramaEpisode,
    shots: list[DramaShot],
    style: DramaStyleCard | None,
    cards: list[DramaCharacterCard],
    scenes: list[DramaSceneCard],
) -> str:
    """全量 JSON(结构同 API 返回,程序化处理用)。"""
    from app.engines.drama.common import (
        character_card_dict,
        episode_dict,
        scene_card_dict,
        shot_dict,
        style_card_dict,
    )

    payload = {
        "project_title": project.title,
        "episode": episode_dict(episode),
        "style": style_card_dict(style),
        "characters": [character_card_dict(c, style) for c in cards],
        "scenes": [scene_card_dict(sc) for sc in scenes],
        "shots": [
            shot_dict(s, paste=shot_paste(s, style, _ref_names(s, cards)))
            for s in shots
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# =============== 阶段 2:字幕 / 成片包 ===============

def _srt_ts(sec: float) -> str:
    """秒 → SRT 时间码 HH:MM:SS,mmm。"""
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt_blocks(items: list[tuple[int, str]]) -> str:
    """(时长秒, 字幕文本) 序列 → 标准 SRT。有文本才有字幕条;空条时长仍计入时间轴。"""
    blocks: list[str] = []
    t = 0.0
    idx = 0
    for duration, text in items:
        start, end = t, t + duration
        t = end
        text = (text or "").strip()
        if not text:
            continue
        idx += 1
        blocks.append(f"{idx}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{text}\n")
    return "\n".join(blocks)


def export_srt(shots: list[DramaShot]) -> str:
    """标准 SRT 字幕(剪映/PR 直接导入):时间轴按分镜时长累计,有台词才有字幕条。

    纯确定性输出——时间轴来自分镜表,和剪辑清单同一口径。
    """
    return _srt_blocks([(s.duration_s, s.dialogue or "") for s in shots])


def export_trailer_srt(shots: list[dict]) -> str:
    """预告片 SRT(dict 版 shots,与 export_srt 同一时间轴口径)。"""
    return _srt_blocks([(int(s.get("duration_s") or 0), str(s.get("dialogue") or "")) for s in shots])


def export_trailer_markdown(project: Project, trailer: dict) -> str:
    """预告片拍摄手册:文案骨架 + 混剪分镜 + 三轨提示词。"""
    L: list[str] = []
    totals = trailer.get("totals") or {}
    L.append(f"# 《{project.title}》漫剧预告片 · {trailer.get('title') or ''}")
    L.append("")
    L.append(
        f"- 目标时长:{trailer.get('target_s', '?')} 秒 | 镜头:{totals.get('shots', '?')} 格 "
        f"| 分镜总时长:{totals.get('duration_s', '?')}s | 取材:第 {totals.get('from_ep', '?')}-{totals.get('to_ep', '?')} 集"
    )
    L.append("")
    lines = trailer.get("lines") or []
    if lines:
        L.append("## 文案骨架(旁白 + 金句)")
        L.append("")
        for l in lines:
            if isinstance(l, dict):
                L.append(f"- **{l.get('speaker', '')}**:{l.get('text', '')}")
        L.append("")
    shots = trailer.get("shots") or []
    if shots:
        L.append("## 混剪分镜")
        L.append("")
        L.append("| # | 取材 | 场景 | 角色 | 景别 | 运镜 | 秒 | 画面 | 台词 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for s in shots:
            src = s.get("source_ep") or 0
            src_cn = "新创" if not src else f"第{src}集"
            dia = str(s.get("dialogue") or "").replace("|", "/").replace("\n", " ")
            act = str(s.get("action_desc") or "").replace("|", "/").replace("\n", " ")
            L.append(
                f"| {s.get('seq')} | {src_cn} | {s.get('scene_name') or ''} "
                f"| {'、'.join(s.get('characters') or [])} | {s.get('shot_type')} | {s.get('camera')} "
                f"| {s.get('duration_s')} | {act} | {dia} |"
            )
        L.append("")
        L.append("## 三轨提示词(即拿即用)")
        L.append("")
        for s in shots:
            L.append(f"### 镜头 {s.get('seq')}({s.get('shot_type')}/{s.get('camera')}/{s.get('duration_s')}s)")
            L.append(f"**中文提示词(即梦/可灵)**")
            L.append("")
            L.append(s.get("prompt_cn") or "(未生成)")
            L.append("")
            L.append(f"**英文提示词(Midjourney)**")
            L.append("")
            L.append(s.get("prompt_en") or "(未生成)")
            L.append("")
            L.append(f"**负面提示词**:{s.get('negative') or '(无)'}")
            L.append("")
        L.append("> 混剪顺序:炸点开场 → 世界/人设速览 → 冲突升级连切 → 悬念定格 + 标题卡(后期加字)。")
    return "\n".join(L)


def export_pack_markdown(
    project: Project, episode: DramaEpisode, pack: dict
) -> str:
    """成片包 Markdown:配音稿 + 剪辑清单(TTS + 剪映照着走完出片)。"""
    L: list[str] = []
    L.append(f"# 《{project.title}》漫剧成片包 · 第 {episode.ep_index} 集《{episode.title}》")
    totals = pack.get("totals") or {}
    L.append("")
    L.append(
        f"- 模式:{MODE_DESC.get(episode.mode, episode.mode)} | 镜头:{totals.get('shots', '?')} 格"
        f" | 分镜总时长:{totals.get('storyboard_s', '?')}s(目标 {totals.get('target_s', '?')}s)"
        f" | 配音总估时:{totals.get('voice_s', '?')}s"
    )
    if pack.get("synopsis"):
        L.append(f"- 本集梗概:{pack['synopsis']}")
    L.append("")

    dubbing = pack.get("dubbing") or []
    if dubbing:
        L.append("## 配音稿(按镜头顺序)")
        L.append("")
        L.append("| # | 说话人 | 声线 | 朗读文本 | 估时/画面 | 选型建议 |")
        L.append("|---|---|---|---|---|---|")
        for d in dubbing:
            voice = str(d.get("voice") or "").replace("|", "/")
            tts = str(d.get("tts_text") or "").replace("|", "/").replace("\n", " ")
            L.append(
                f"| {d.get('seq')} | {d.get('speaker')} | {voice} | {tts} "
                f"| {d.get('est_s')}s/{d.get('shot_duration_s')}s | {str(d.get('tts_hint') or '').replace('|', '/')} |"
            )
        L.append("")
        for d in dubbing:
            if d.get("reading_notes"):
                L.append(f"- **{d.get('speaker')}** 朗读指示:{d['reading_notes']}")
        L.append("")

    narration = pack.get("narration_full")
    if narration:
        L.append("## 整段口播(旁白一把梭版,粘给 TTS)")
        L.append("")
        L.append(narration)
        L.append("")

    checklist = pack.get("checklist") or []
    if checklist:
        L.append("## 剪辑清单(按镜头顺序)")
        L.append("")
        L.append("| # | 场景 | 时长 | 字幕 | 转场 | 配乐 | 备注 |")
        L.append("|---|---|---|---|---|---|---|")
        for c in checklist:
            sub = str(c.get("subtitle") or "").replace("|", "/").replace("\n", " ")
            L.append(
                f"| {c.get('seq')} | {c.get('scene') or ''} | {c.get('duration_s')}s "
                f"| {sub} | {c.get('transition') or ''} | {c.get('bgm_tag') or ''} | {c.get('note') or ''} |"
            )
        L.append("")
        L.append("> 出片顺序:先出角色定妆照(手册「角色卡」段) → 每格「定妆照当参考图 + 分镜提示词」出图"
                 " → 图生视频/加轻动 → 按配音稿合成语音 → 按剪辑清单拼接 → 压 SRT 字幕 → 铺 BGM。")
    return "\n".join(L)
