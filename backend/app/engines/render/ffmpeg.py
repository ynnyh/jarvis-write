# app/engines/render/ffmpeg.py
# -*- coding: utf-8 -*-
"""ffmpeg 外部二进制定位与末帧抽取(首尾帧自动接力的机械部分)。

ffmpeg 是本项目第一个外部二进制依赖,策略是「有就用、没有就藏」:
- 定位三级:环境变量 JARVIS_FFMPEG(指到可执行文件)→ 打包资源 bin/
  (桌面版随安装包带)→ PATH(源码/Docker 环境);
- available() 为 False 时,末帧接力整体隐藏(端点返回不可用),其余功能零影响;
- 抽取走 `-sseof -0.1`(从片尾倒搜 0.1 秒)取最后一帧,失败只返回 False
  不抛——接力是锦上添花,绝不能让出片本身报错。

二进制不进 git(体积与许可):桌面构建前手工放入 backend/bin/(见
scripts/build-desktop.sh 的提示),Docker 在镜像里 apt 安装。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from app.paths import is_frozen, resource_path

logger = logging.getLogger("jarvis-write.render")

# 单次抽帧超时:几 MB 的短片抽一帧,60 秒还出不来一定是环境坏了
_EXTRACT_TIMEOUT_S = 60


def ffmpeg_bin() -> str | None:
    """定位 ffmpeg 可执行文件;三级都落空返回 None。"""
    env = (os.environ.get("JARVIS_FFMPEG") or "").strip()
    if env and Path(env).is_file():
        return env
    try:
        bundled = resource_path("bin/ffmpeg.exe" if is_frozen() and os.name == "nt" else "bin/ffmpeg")
        if bundled.is_file():
            return str(bundled)
    except Exception:  # noqa: BLE001 — resource_path 在异常环境可能抛,按没带处理
        pass
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def available() -> bool:
    return ffmpeg_bin() is not None


def extract_last_frame(mp4_path: Path, out_png: Path) -> bool:
    """从 mp4 抽最后一帧存成 png。成功返回 True;任何失败返回 False(不抛)。"""
    bin_path = ffmpeg_bin()
    if bin_path is None or not mp4_path.is_file():
        return False
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        bin_path, "-y",
        "-sseof", "0.1",        # 从片尾前 0.1 秒起播:只关心最后一帧,倒搜最快
        "-i", str(mp4_path),
        "-frames:v", "1",
        "-q:v", "2",            # 高质量单帧(qscale 越小越好,2 足够当首帧用)
        str(out_png),
    ]
    try:
        proc = subprocess.run(  # noqa: S603 — 参数全是服务端构造的固定路径,无用户输入
            cmd, capture_output=True, timeout=_EXTRACT_TIMEOUT_S,
        )
        ok = proc.returncode == 0 and out_png.is_file()
        if not ok:
            logger.warning("末帧抽取失败(%s):%s", mp4_path.name, proc.stderr[-200:] if proc.stderr else "")
        return ok
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("末帧抽取异常(%s): %s", mp4_path.name, exc)
        return False
