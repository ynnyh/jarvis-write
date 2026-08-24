# tests/test_media_audio.py
# -*- coding: utf-8 -*-
"""音频分轨口径的测试(三条出片线共用,口径见 app/engines/media/audio.py)。

为什么值得一个文件:这一层回答的是「AI 视频自带声音,你把声音去了?」——
没去,是**分轨**:环境音让视频模型出,人声与 BGM 整片后期铺。这个区别只写在
注释里靠不住,所以钉三条:
① 视频提示词里必须带着音频那句(不带 → Veo 一类自己编对白,和配音稿撞成两层人声);
② 音频词绝不许进负面词框(那个框是给画面的,塞「人声」既不生效还可能干扰画面);
③ 导出手册必须解释这是分轨而非静音,且**整片一段一次出完时口径反过来**
  (没有拼接错位,直接用模型自带音频最省事)。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.engines.clips.exporter import export_markdown as clips_md
from app.engines.drama.video import video_paste, video_negative
from app.engines.media.audio import (
    VIDEO_AUDIO_RULE_CN,
    VIDEO_AUDIO_RULE_EN,
    audio_track_note,
    ensure_audio_rules,
)


# =============== 兜底注入 ===============

def test_rule_appended_when_model_said_nothing_about_audio():
    cn, en = ensure_audio_rules("蒸汽升腾中缓缓推近", "slow push in through steam")
    assert cn.endswith(VIDEO_AUDIO_RULE_CN)
    assert cn.startswith("蒸汽升腾中缓缓推近")  # 原文在前,约束在后
    assert en.endswith(VIDEO_AUDIO_RULE_EN)


def test_rule_not_stacked_when_model_already_handled_audio():
    """模型自己交代过音频就不重复——重复约束会加权过头,把该留的环境音也掐掉。"""
    cn, en = ensure_audio_rules("灶上爆炒,只留环境音", "ambient sound only, no speech")
    assert cn == "灶上爆炒,只留环境音"
    assert en == "ambient sound only, no speech"


def test_empty_prompt_stays_empty():
    """提示词还没生成时不许造出「只有约束没有内容」的提示词。"""
    assert ensure_audio_rules("", "") == ("", "")
    assert ensure_audio_rules("   ", "") == ("", "")


# =============== 漫剧图生/文生视频粘贴版 ===============

def test_video_paste_carries_audio_rule_on_all_tracks():
    v = video_paste(motion_cn="她抬手抹雪", motion_en="wipes snow off the blade",
                    prompt_cn="沈砚,雪夜", duration_s=4)
    assert "【音频】" in v["i2v"]["main"]
    assert "【音频】" in v["t2v"]["main"]
    assert "no speech" in v["i2v_en"]["main"]
    # 分轨的两个「不要」都在:人声与背景音乐
    for track in ("i2v", "t2v"):
        assert "不要人声对白" in v[track]["main"]
        assert "不要背景音乐" in v[track]["main"]
        assert "环境音" in v[track]["main"]  # 环境音是留的,不是去的


def test_audio_words_never_enter_the_negative_box():
    """负面词框在各站是给画面用的:塞「人声/背景音乐」既不生效,还可能干扰画面。"""
    neg = video_negative("文字水印")
    for word in ("人声", "背景音乐", "旁白", "音频"):
        assert word not in neg, word
    v = video_paste(motion_cn="动", motion_en="move")
    assert "speech" not in v["i2v_en"]["negative"]
    assert "music" not in v["i2v_en"]["negative"]


# =============== 导出手册的说明 ===============

def test_note_explains_three_tracks_not_muting():
    note = "\n".join(audio_track_note())
    assert "环境音" in note and "人声" in note and "BGM" in note
    assert "不是让你静音" in note  # 这句就是给「你把声音去了?」的回答


def test_single_segment_note_flips_to_use_the_model_audio():
    note = "\n".join(audio_track_note(single_segment=True))
    assert "直接用模型自带的音频最省事" in note
    assert "分三轨" not in note  # 一段出完没有拼接问题,不给用户加负担


def _clip_row(chunk_count: int) -> SimpleNamespace:
    shots = [
        {"seq": 1, "duration_s": 5, "scene_name": "巷口", "shot_type": "近景", "camera": "固定",
         "action_desc": "她低头", "dialogue": "别走", "prompt_cn": "巷口近景", "prompt_en": "alley",
         "negative": "多手多指"}
    ]
    chunks = [
        {"index": i + 1, "start_s": i * 5, "end_s": i * 5 + 5, "duration_s": 5,
         "shot_seqs": [1], "over_limit": False}
        for i in range(chunk_count)
    ]
    return SimpleNamespace(
        clip={"take": "A 版", "shots": shots, "chunks": chunks, "lines": [], "punchline": "回头"},
        theme="regret", custom_theme="", duration_s=15, direction="anime",
        style_name="国风厚涂", style_cn="国风厚涂", style_en="ink-wash", negative="多手多指",
    )


def test_clips_manual_picks_note_by_segment_count():
    one = clips_md(_clip_row(1))
    assert "直接用模型自带的音频最省事" in one          # 15s 常见:一段就出完
    many = clips_md(_clip_row(3))
    assert "音频分三轨" in many                        # 要拼接了,人声必须自己来
    assert "直接用模型自带的音频最省事" not in many
