# app/engines/drama/exporter.py
# -*- coding: utf-8 -*-
"""拍摄手册导出:Markdown(人读)/ CSV(表格导入)/ JSON(程序消费)。

纯格式化,不碰 LLM/DB(数据由 API 层查好传入),方便单测。
Markdown 的装配骨架(GFM 表格 / 三轨提示词块 / 小节空行)由 `media.markdown`
统一提供,和宣传片、情绪短片、生日祝福走同一份口径。
"""
from __future__ import annotations

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
    shot_refs_by_seq,
    source_chapter_label,
)
from app.engines.drama.paste import ref_sheet_paste, shot_paste
from app.engines.drama.video import CLIP_LIMIT_DEFAULT, clips_payload, motion_tracks
from app.engines.media.audio import audio_track_note
from app.engines.media.markdown import Md
from app.engines.media.subtitles import srt_blocks, srt_from_rows
from app.engines.media.text import csv_text

_STATUS_CN = {
    "planned": "已规划",
    "scripted": "已有剧本",
    "storyboarded": "已有分镜",
    "ready": "提示词就绪",
}

# 三轨提示词的漫剧文案(预告片那节与宣传片同款长标签)
_CN_LABEL = "**中文提示词(即梦/可灵)**"
_EN_LABEL = "**英文提示词(Midjourney)**"
_NEG_LABEL = "**负面提示词**"


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


def _refs_by_seq(shots: list[DramaShot], cards: list[DramaCharacterCard]) -> dict[int, list[str]]:
    """格号 → 已有定妆照的出场角色名(视频段计划的 r2v 主体绑定按这个并集取)。"""
    return shot_refs_by_seq(shots, cards)


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

    md = Md()
    md.h1(f"《{project.title}》漫剧拍摄手册 · 第 {episode.ep_index} 集《{episode.title}》")
    md.kv([
        ("模式", MODE_DESC.get(episode.mode, episode.mode)),
        ("目标时长", f"{episode.duration_target_s} 秒"),
        ("源章节", source_chapter_label(episode)),
        ("状态", _STATUS_CN.get(episode.status, episode.status)),
    ], blank=False)
    md.bullet(f"开场钩子:{episode.hook}")
    md.bullet(f"结尾卡点:{episode.cliffhanger}")
    if shots:
        # 施工进度:导出的手册常被当进度表用,做到哪儿一开头就得看见
        prog = shot_progress(shots)
        md.bullet(
            f"施工进度:静帧 {prog['stills_done']}/{prog['shots']} 格"
            f" | 视频 {prog['videos_done']}/{prog['shots']} 格"
            f" | 已挂素材 {prog['assets']} 张(在站内每格「挂静帧」处更新)"
        )
    md.add("")

    if style is not None:
        md.h2("美术风格卡(全片统一)", blank=False)
        md.bullet(f"风格:{style.style_name} | 画幅:{style.ratio}")
        md.bullet(f"画风锁定段(中文):{style.style_cn}")
        md.bullet(f"画风锁定段(英文):{style.style_en}")
        md.bullet(f"负面词基座:{style.negative}")
        md.add("")

    if cards:
        md.h2("角色卡(本集出场)", blank=False)
        for c in cards:
            if c.name not in used_chars:
                continue
            md.h3(c.name + ("(已锁定)" if c.locked else ""))
            md.bullet(f"锁定外貌:{c.appearance_cn}")
            md.bullet(f"英文锚段:{c.appearance_en}")
            if c.outfit_cn:
                md.bullet(f"标志服饰:{c.outfit_cn}")
            if c.voice_desc:
                md.bullet(f"配音声线:{c.voice_desc}")
            imgs = ref_image_list(c)
            if c.ref_prompt_cn or imgs:
                md.add("", "**定妆照(先出这张,再拿它当每格的参考图)**", "")
                if imgs:
                    srcs = "、".join(
                        (i["src"] if i["kind"] == "url" else f"随手册目录 {i['src']}")
                        for i in imgs
                    )
                    md.bullet(f"已有参考图 {len(imgs)} 张:{srcs}")
                else:
                    md.bullet("⚠ 还没出参考图:先用下面这段生成一张,存好备用。")
                if c.ref_prompt_cn:
                    paste = ref_sheet_paste(c, style)
                    md.bullet("单框站(GPT-image / 豆包 / 通义)整段粘这个:")
                    md.add("", paste["oneframe"]["main"], "")
                    if paste["mj"]["main"]:
                        md.bullet(f"Midjourney:`{paste['mj']['main']}`")
                if c.ref_prompt_en and not c.ref_prompt_cn:
                    md.bullet(f"英文定妆照提示词:{c.ref_prompt_en}")
        md.add("")

    if scenes:
        md.h2("场景卡(本集出场)", blank=False)
        for sc in scenes:
            if sc.name not in used_scenes:
                continue
            md.h3(sc.name)
            md.bullet(f"定调描述:{sc.appearance_cn}")
            if sc.appearance_en:
                md.bullet(f"英文锚段:{sc.appearance_en}")
        md.add("")

    lines = (episode.script or {}).get("lines") or []
    if lines:
        md.h2("剧本", blank=False)
        md.speech(lines, numbered=True)

    if shots:
        md.h2("分镜表", blank=False)
        md.table(
            ["#", "场景", "角色", "景别", "运镜", "时长(s)", "画面", "台词"],
            [
                [s.seq, s.scene_name, "、".join(s.characters or []), s.shot_type,
                 s.camera, s.duration_s, s.action_desc, s.dialogue]
                for s in shots
            ],
        )
        md.h2("分镜提示词(按你用的生图站选一版粘)")
        md.quote(
            "生图站长相不一样:**只有一个描述框**的站(GPT-image / DALL·E / 豆包 / 通义)"
            "要用 ① ——负面词已改写成「不要出现」并进正文,直接整段粘;"
            "**有负面词框**的站(即梦 / 可灵 / SD)用 ②,正反分开粘;Midjourney 用 ③。",
            blank=True,
        )
        for s in shots:
            paste = shot_paste(s, style, _ref_names(s, cards))
            md.h3(f"镜头 {s.seq}({s.shot_type}/{s.camera}/{s.duration_s}s)", blank=True)
            md.add("**① 单框站:整段粘这个**", "", paste["oneframe"]["main"] or "(未生成)", "")
            md.add(
                "**② 有负面词框的站**", "",
                f"- 正文:{paste['dualbox']['main'] or '(未生成)'}",
                f"- 负面词:{paste['dualbox']['negative'] or '(无)'}",
                "",
            )
            md.add(
                "**③ Midjourney / Niji**", "",
                paste["mj"]["main"] or "(没有英文轨,先出提示词)", "",
            )

        _video_section(md, shots, style, _refs_by_seq(shots, cards))

    return md.text()


def _video_section(
    md: Md,
    shots: list[DramaShot],
    style: DramaStyleCard | None,
    refs_by_seq: dict[int, list[str]] | None = None,
) -> None:
    """让静帧动起来那一步:视频段计划(按单次时长上限并段)+ 每段的视频提示词。

    单列一节而不是混进出图那节:生图与生视频吃的提示词根本不是一回事(i2v 不许
    带外貌词,见 engines/drama/video.py),而且视频站有单次时长上限,得先并段。
    """
    plan = clips_payload(shots, style, CLIP_LIMIT_DEFAULT, refs_by_seq=refs_by_seq)
    md.h2(f"让它动起来:视频段计划(按单次上限 {plan['limit_s']} 秒并段)")
    md.quote(
        "视频站单次只能出 5-15 秒,而分镜格是 2-8 秒——所以**一段生成一次,再在画布/"
        "剪映里按段号首尾相接**。首帧图用该段第一格出好的静帧,人物长相全靠它锁住;"
        "提示词里**刻意不写外貌**(写了模型会重画脸)。",
        blank=True,
    )
    md.quote(plan["note"], blank=True)
    md.table(
        ["段", "含分镜", "场景", "角色", "秒", "生成次数", "首帧", "首帧图", "这一段怎么动"],
        [
            [seg["index"], "、".join(map(str, seg["seqs"])), seg["scene_name"],
             "、".join(seg["characters"]) or "(空镜)", seg["duration_s"], seg["runs"],
             seg["first_frame"], "✓ 已挂" if seg["first_frame_ready"] else "待出图",
             seg["motion"]]
            for seg in plan["segments"]
        ],
    )
    for seg in plan["segments"]:
        paste = seg["paste"]
        md.h3(
            f"视频段 {seg['index']}(分镜 {'、'.join(map(str, seg['seqs']))},{seg['duration_s']}s)",
            blank=True,
        )
        if seg["split_hint"]:
            md.quote(f"⚠ {seg['split_hint']}", blank=True)
        md.add(
            "**① 图生视频·中文站(即梦 / 可灵 / 海螺):传首帧图 + 整段粘**", "",
            paste["i2v"]["main"], "",
        )
        md.add(
            "**② 图生视频·英文站(Runway / Luma / Pika)**", "",
            f"- 正文:{paste['i2v_en']['main']}",
            f"- 负面词:{paste['i2v_en']['negative']}", "",
        )
        md.add(
            "**③ 文生视频(没有首帧,慎用)**", "",
            paste["t2v"]["main"] or "(这一格还没有出图提示词,先出提示词)", "",
        )
        md.quote(paste["t2v"]["hint"], blank=True)
        md.add(
            "**④ 参考生视频·多图主体绑定(Vidu / PixStag / 可灵多图):按序传定妆照**", "",
            paste["r2v"]["main"], "",
        )
        md.quote(paste["r2v"]["hint"], blank=True)
        if seg["dialogue"]:
            md.quote(f"这一段的字幕/配音:{seg['dialogue']}", blank=True)


def export_csv(
    episode: DramaEpisode,
    shots: list[DramaShot],
    style: DramaStyleCard | None = None,
    cards: list[DramaCharacterCard] | None = None,
) -> str:
    """分镜表 CSV(带 BOM,Excel 打开中文不乱码)。

    最后几列是「让它动起来」那一步的:clip 说明这一格属于第几个视频段(段内共用一次
    生成),paste_i2v 是那一段的图生视频提示词,paste_r2v 是参考生视频的主体绑定版
    (角色定妆照按序当参考);still_asset 是你挂回来的静帧、
    clip_ref 是成片在哪,done_* 是两个打勾栏(站内勾过的这里就是 ✓,没勾的留空给你手打)。
    paste_oneframe 是「单框站直接粘贴版」(负面词已并入正文):
    批量出图的人普遍拿 Excel 一行行复制,没这列就得自己拼负面词。
    """
    # 逐格施工单:出图三栏 + 运动轨两栏 + 段号/生成次数 + 素材两栏 + 两个打勾栏。
    # 打勾栏取站内的真实状态而不是一律留空——在站里勾过的进度,导出来还要重勾一遍就白勾了。
    header = [
        "seq", "scene_name", "characters", "shot_type", "camera", "duration_s",
        "action_desc", "dialogue", "prompt_cn", "prompt_en", "negative",
        "paste_oneframe", "motion_cn", "motion_en", "clip", "clip_seqs",
        "clip_duration_s", "clip_runs", "paste_i2v", "paste_r2v",
        "still_asset", "clip_ref",
        "done_still", "done_video",
    ]
    # 段号按格反查:表是逐格的,而视频段是「几格并一段」,不给映射用户对不上号
    plan = clips_payload(shots, style, CLIP_LIMIT_DEFAULT, refs_by_seq=_refs_by_seq(shots, cards or []))
    seg_of: dict[int, dict] = {}
    for seg in plan["segments"]:
        for q in seg["seqs"]:
            seg_of[q] = seg
    rows: list[list] = []
    for s in shots:
        paste = shot_paste(s, style, _ref_names(s, cards or []))
        motion_cn, motion_en = motion_tracks(s)
        seg = seg_of.get(s.seq) or {}
        assets = shot_asset_list(s)
        rows.append(
            [s.seq, s.scene_name, "、".join(s.characters or []), s.shot_type,
             s.camera, s.duration_s, s.action_desc, s.dialogue,
             s.prompt_cn, s.prompt_en, s.negative, paste["oneframe"]["main"],
             motion_cn, motion_en,
             seg.get("index", ""), "、".join(str(q) for q in seg.get("seqs", [])),
             seg.get("duration_s", ""), seg.get("runs", ""),
             (seg.get("paste") or {}).get("i2v", {}).get("main", ""),
             (seg.get("paste") or {}).get("r2v", {}).get("main", ""),
             "、".join(a["src"] for a in assets),
             getattr(s, "clip_ref", "") or "",
             "✓" if getattr(s, "done_still", False) else "",
             "✓" if getattr(s, "done_video", False) else ""]
        )
    return csv_text(header, rows)


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
# SRT 内核(时间码 + 累计时间轴)已挪进 media.subtitles:三条出片线共用一份口径。

def export_srt(shots: list[DramaShot]) -> str:
    """标准 SRT 字幕(剪映/PR 直接导入):时间轴按分镜时长累计,有台词才有字幕条。

    纯确定性输出——时间轴来自分镜表,和剪辑清单同一口径。
    """
    return srt_blocks([(s.duration_s, s.dialogue or "") for s in shots])


def export_trailer_srt(shots: list[dict]) -> str:
    """预告片 SRT(dict 版 shots,与 export_srt 同一时间轴口径)。"""
    return srt_from_rows(shots)


def export_trailer_markdown(project: Project, trailer: dict) -> str:
    """预告片拍摄手册:文案骨架 + 混剪分镜 + 三轨提示词。"""
    totals = trailer.get("totals") or {}
    md = Md()
    md.h1(f"《{project.title}》漫剧预告片 · {trailer.get('title') or ''}")
    md.kv([
        ("目标时长", f"{trailer.get('target_s', '?')} 秒"),
        ("镜头", f"{totals.get('shots', '?')} 格"),
        ("分镜总时长", f"{totals.get('duration_s', '?')}s"),
        ("取材", f"第 {totals.get('from_ep', '?')}-{totals.get('to_ep', '?')} 集"),
    ])

    lines = trailer.get("lines") or []
    if lines:
        md.h2("文案骨架(旁白 + 金句)")
        md.speech(lines)

    shots = trailer.get("shots") or []
    if shots:
        md.h2("混剪分镜")
        md.table(
            ["#", "取材", "场景", "角色", "景别", "运镜", "秒", "画面", "台词"],
            [
                [s.get("seq"),
                 "新创" if not s.get("source_ep") else f"第{s.get('source_ep')}集",
                 s.get("scene_name"), "、".join(s.get("characters") or []),
                 s.get("shot_type"), s.get("camera"), s.get("duration_s"),
                 s.get("action_desc"), s.get("dialogue")]
                for s in shots
            ],
        )
        md.h2("三轨提示词(即拿即用)")
        for s in shots:
            md.shot_tracks(s.get("seq"), s.get("shot_type"), s.get("camera"),
                           s.get("duration_s"), s.get("prompt_cn"), s.get("prompt_en"),
                           s.get("negative"),
                           cn_label=_CN_LABEL, en_label=_EN_LABEL, neg_label=_NEG_LABEL)
        md.quote("混剪顺序:炸点开场 → 世界/人设速览 → 冲突升级连切 → 悬念定格 + 标题卡(后期加字)。")
    return md.text()


def export_pack_markdown(
    project: Project, episode: DramaEpisode, pack: dict
) -> str:
    """成片包 Markdown:配音稿 + 剪辑清单(TTS + 剪映照着走完出片)。"""
    totals = pack.get("totals") or {}
    md = Md()
    md.h1(f"《{project.title}》漫剧成片包 · 第 {episode.ep_index} 集《{episode.title}》")
    md.kv([
        ("模式", MODE_DESC.get(episode.mode, episode.mode)),
        ("镜头", f"{totals.get('shots', '?')} 格"),
        ("分镜总时长", f"{totals.get('storyboard_s', '?')}s(目标 {totals.get('target_s', '?')}s)"),
        ("配音总估时", f"{totals.get('voice_s', '?')}s"),
    ], blank=False)
    if pack.get("synopsis"):
        md.bullet(f"本集梗概:{pack['synopsis']}")
    md.add("")

    dubbing = pack.get("dubbing") or []
    if dubbing:
        md.h2("配音稿(按镜头顺序)")
        md.table(
            ["#", "说话人", "声线", "朗读文本", "估时/画面", "选型建议"],
            [
                [d.get("seq"), d.get("speaker"), d.get("voice"), d.get("tts_text"),
                 f"{d.get('est_s')}s/{d.get('shot_duration_s')}s", d.get("tts_hint")]
                for d in dubbing
            ],
        )
        for d in dubbing:
            if d.get("reading_notes"):
                md.bullet(f"**{d.get('speaker')}** 朗读指示:{d['reading_notes']}")
        md.add("")

    narration = pack.get("narration_full")
    if narration:
        md.h2("整段口播(旁白一把梭版,粘给 TTS)")
        md.add(narration, "")

    checklist = pack.get("checklist") or []
    if checklist:
        md.h2("剪辑清单(按镜头顺序)")
        md.table(
            ["#", "场景", "时长", "字幕", "转场", "配乐", "备注"],
            [
                [c.get("seq"), c.get("scene"), f"{c.get('duration_s')}s", c.get("subtitle"),
                 c.get("transition"), c.get("bgm_tag"), c.get("note")]
                for c in checklist
            ],
        )
        md.quote(
            "出片顺序:先出角色定妆照(手册「角色卡」段) → 每格「定妆照当参考图 + 分镜提示词」出图"
            " → 图生视频/加轻动 → 按配音稿合成语音 → 按剪辑清单拼接 → 压 SRT 字幕 → 铺 BGM。",
            blank=True,
        )
        # 音频三轨说明:视频提示词里那句「不要人声」是分轨,不是静音(口径见 media.audio)
        md.extend(audio_track_note())
    return md.text()
