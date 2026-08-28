# app/engines/media/video.py
# -*- coding: utf-8 -*-
"""视频生成的共用确定性件:运镜词表、视频特有负面词、时长/分辨率口径。

为什么在 media 而不在某条线里:漫剧(engines/drama/video.py)与情绪短片
(engines/clips/render_input.py)都要构造「拿去视频站/出片引擎」的提示词,
负面词与运镜词表一旦各写一份就会口径分叉(先例:宣传片转引漫剧的私有
SRT 函数,两线口径从此对不上)。依赖方向:两条线往这里看,这里不回看。
"""
from __future__ import annotations

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


def camera_en(camera: str) -> str:
    """运镜中文 → 英文镜头词(白名单外的值给个中性兜底,不硬翻)。"""
    return _CAMERA_EN.get((camera or "").strip(), "steady camera")


def video_negative(style_negative: str = "") -> str:
    """视频负面词 = 视频特有项 + 画风卡的负面词基座(去重靠包含判断)。"""
    base = (style_negative or "").strip()
    if not base:
        return VIDEO_NEGATIVE_CN
    return f"{VIDEO_NEGATIVE_CN},{base}"


def clamp_duration_s(raw: object, upper: int = 15, default: int = 4) -> int:
    """镜头/段时长 → 出片引擎可接受的整数秒(1..upper)。

    上限 15 秒来自 autodl.art 各视频工作流的 duration 参数域;下限 1 秒是
    API 的硬边界。非数值得兜底,不能把一次出片卡死在脏数据上。
    """
    try:
        n = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default  # 0/负数 = 脏数据,回落默认时长,而不是硬夹到 1 秒
    return max(1, min(upper, n))


def resolution_value(quality: str, ratio: str, default_quality: str = "768p") -> str:
    """画质档 + 画幅 → 工作流 resolution 枚举值(如「768p竖」)。

    quality 只认 480p/768p(1080p 仅个别对口型工作流有,轻量档不开);
    ratio 出现 16:9 视为横屏,其余(9:16/1:1/空)一律按竖屏——竖屏是
    短视频默认画幅,拿不准时竖着出不会错得离谱。
    """
    q = (quality or "").strip().lower()
    if q not in ("480p", "768p"):
        q = default_quality
    orient = "横" if "16:9" in (ratio or "") else "竖"
    return f"{q}{orient}"
