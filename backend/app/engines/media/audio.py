# app/engines/media/audio.py
# -*- coding: utf-8 -*-
"""三条出片线共用的**音频分轨口径**(确定性,不碰 LLM/DB)。

起因是一句该被问的话:「现在的 AI 生成视频不是有声音吗,咱们把声音给去了?」
先摆事实:全站没有一处让模型静音——视频提示词里唯一的「不要」是**画面上的文字
字幕**(见 `drama.video.VIDEO_NEGATIVE_CN`)。真正的缺口是**此前对音频一个字都
没交代**:支持原生音频的模型(Veo 3 一类会自己编对白、自己铺音乐)于是自由发挥,
和成片包给的配音稿、`bgm_tag` 撞成两层人声、两条错拍的音乐。

所以口径不是「去掉声音」,是**分轨**:
- **环境音/动作音**(风雨、脚步、器物、灶上爆炒):让视频模型出,这是它的长处,留着;
- **人声**(解说/台词)由我们这一轨来,三个理由:
  ① 逐段生成再拼接——每段自带人声的音色/语速/底噪在段边界全跳,一听就断;
  ② 词必须一字不差——slogan、金句(情绪短片的 `quote_source` 溯源就是为了不编造
     引用),模型自由发挥等于把这条红线作废;
  ③ 改一个字重配一句 TTS 是秒级、免费;重跑一段视频是分钟级、要钱,而且画面
     不可复现(改一个字赔上整段画面)。
- **BGM** 按段情绪词整片铺,分段自带音乐在段边界必然错拍。

别把话说死的例外:整片只有一段、一次就生成完(≤ 单次上限,常见 15s 情绪短片),
不存在拼接错位,直接用模型自带音频最省事——这时配音稿只当参考。见
`audio_track_note(single_segment=True)`。

注:音频口径**只写进提示词正文,不塞负面词**。负面词框在各站是给画面用的,
往里塞「人声」「背景音乐」既不生效,还可能干扰画面(「人声」被当成画面里的人)。
"""
from __future__ import annotations

# 追加到视频提示词末尾的一行(中/英)。不支持音频的站会忽略,支持的照做。
VIDEO_AUDIO_RULE_CN = (
    "【音频】只要环境音与动作音(风雨、脚步、器物、环境底噪),"
    "不要人声对白/旁白/歌词,不要背景音乐——人声与音乐整片后期统一铺"
)
VIDEO_AUDIO_RULE_EN = (
    "audio: ambient and action sounds only, no speech, no voice-over, "
    "no singing, no background music"
)


def ensure_audio_rules(prompt_cn: str, prompt_en: str) -> tuple[str, str]:
    """中英双轨音频口径兜底(全站唯一口径,与 `anchors.ensure_style_anchors` 同纪律)。

    模型自己已经交代过音频就不重复追加——重复的约束会让模型加权过头,
    把该留的环境音也一起掐掉。
    """
    return (
        _append_rule(prompt_cn, VIDEO_AUDIO_RULE_CN, ("【音频】", "环境音", "音频:")),
        _append_rule(prompt_en, VIDEO_AUDIO_RULE_EN, ("audio:", "ambient sound", "no speech")),
    )


def _append_rule(prompt: str, rule: str, markers: tuple[str, ...]) -> str:
    """把 `rule` 追加到提示词末尾;空提示词不追加(别造出只有约束没有内容的提示词)。"""
    p = (prompt or "").strip()
    if not p or not rule:
        return p
    low = p.lower()
    if any(m.lower() in low for m in markers):
        return p
    return f"{p}\n{rule}"


def audio_track_note(single_segment: bool = False) -> list[str]:
    """导出手册/成片包里的「音频三轨怎么来」说明(Markdown 引用块的若干行)。

    `single_segment=True`:整片一段一次生成完,没有拼接错位问题,给轻量口径。
    """
    if single_segment:
        return [
            "> **音频**:整片就一段、一次生成完,不存在段间错位——**直接用模型自带的音频最省事**"
            "(带原生音频的模型如 Veo 系列会连环境音一起给)。",
            "> 但台词/金句要一字不差时仍走配音稿:模型不保证念对你定稿的那句话。",
        ]
    return [
        "> **音频分三轨,别指望模型一次给全**:",
        "> ① **环境音/动作音**——生成时留着,这是视频模型的长处(可灵/即梦的「音效」开着就行);",
        "> ② **人声(解说/台词)**——按配音稿整片合成一条,别用模型自带的:"
        "分段自带人声在拼接处音色语速全跳,而且它不保证一字不差念你的稿(slogan/金句会被改写);",
        "> ③ **BGM**——按剪辑清单的「配乐」列整片铺,分段自带音乐在段边界必然错拍。",
        "> 所以视频提示词里留着「不要人声、不要背景音乐」那句:它是给 Veo 一类"
        "**自己会编对白**的模型看的,不是让你静音。",
    ]
