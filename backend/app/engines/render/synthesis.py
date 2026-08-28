# app/engines/render/synthesis.py
# -*- coding: utf-8 -*-
"""整集一键合成:逐镜草片拼接 + 静帧占位 + 字幕烧录 + BGM 垫底 → 整集 mp4。

拼装策略(一条 ffmpeg 滤镜链搞定,重编码不可避免但省去中间文件):
- 逐输入归一(scale+pad+fps=30)→ concat=v1a1 → 字幕烧录(可选)→ BGM 低音量 amix;
- 缺视频的格用**静帧定格**占位(-loop 1 -t 时长),故事不断;连静帧都没有的格跳过并如实上报;
- 时长与音轨判定优先 ffprobe 实测(engines/render/ffmpeg.py),探测不出回落
  分镜表 duration_s / 任务 kind(talk 格才有配音音轨)——探测是优化不是门槛;
- 字幕文件写在输出目录、ffmpeg 以 cwd=输出目录 + 相对文件名引用,躲开
  Windows 盘符转义(subtitles=fC\\:/... 这种坑)。

本模块不含一条线的业务:collect_plan 吃查询好的分镜列表(service/api 负责
查库与归属),音色/BGM 等口径分别归各自模块。
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.engines.media.subtitles import srt_blocks
from app.engines.render.ffmpeg import probe_clip

logger = logging.getLogger("jarvis-write.render")

# 本站草片的 clip_ref 形态(引擎回写的指针);外链(http)不可拼
RENDER_CLIP_RE = re.compile(r"^render/r\d+\.mp4$")
# BGM 音量:对白要压得住,垫底别抢戏
_BGM_VOLUME = 0.12
_SYNTH_TIMEOUT_S = 1800


class SynthError(RuntimeError):
    """合成相关的业务性错误(信息直接上屏)。"""


@dataclass
class SynthItem:
    """一个参与拼接的片段:kind=clip(视频草片)或 still(静帧定格)。"""

    kind: str                 # clip | still
    path: Path
    duration_s: float
    text: str                 # 字幕文本(该格台词;空=这段没字幕)
    has_audio: bool           # clip 是否自带音轨(静帧恒 False)


@dataclass
class SynthPlan:
    """一次合成的完整计划:参与项 + 被跳过的格(如实上报,不静默)。"""

    items: list[SynthItem] = field(default_factory=list)
    skipped_seqs: list[int] = field(default_factory=list)
    width: int = 720
    height: int = 1280

    @property
    def clip_count(self) -> int:
        return sum(1 for i in self.items if i.kind == "clip")

    @property
    def still_count(self) -> int:
        return sum(1 for i in self.items if i.kind == "still")

    @property
    def total_s(self) -> float:
        return sum(i.duration_s for i in self.items)


def _clip_path_of(shot) -> Path | None:
    """该格的成片指针若是本站草片且文件还在 → 返回绝对路径;否则 None。"""
    ref = (getattr(shot, "clip_ref", "") or "").strip()
    if not RENDER_CLIP_RE.match(ref):
        return None
    try:
        from app import storage

        path = storage.resolve(ref)
    except Exception:  # noqa: BLE001 — 路径不合法当没有
        return None
    return path if path.is_file() else None


def _still_path_of(shot) -> Path | None:
    """该格第一张本地静帧(占位定格用);外链不算(拉不到就不占位)。"""
    for a in getattr(shot, "assets", None) or []:
        if a.get("kind") == "upload":
            try:
                from app import storage

                path = storage.resolve(a["src"])
            except Exception:  # noqa: BLE001
                continue
            if path.is_file():
                return path
    return None


def _latest_task_kind(db, shot_id: int) -> str:
    """该格最新一次成功出片的类型(talk 格才有配音音轨;探测不出时的回落依据)。"""
    try:
        from app.db.models import RenderTask

        row = (
            db.query(RenderTask)
            .filter(RenderTask.shot_id == shot_id, RenderTask.status == "success")
            .order_by(RenderTask.id.desc())
            .first()
        )
        return (row.kind if row else "") or ""
    except Exception:  # noqa: BLE001
        return ""


def collect_plan(db, shots: list) -> SynthPlan:
    """把一集的分镜格收敛成合成计划(顺序即 seq)。

    三分支:有本站草片 → 视频片段;没草片但有本地静帧 → 静帧定格占位;
    两样皆无 → 跳过(记 seq,合成结果里如实告知)。
    """
    plan = SynthPlan()
    for shot in sorted(shots, key=lambda s: s.seq):
        text = (getattr(shot, "dialogue", "") or "").strip()
        clip_path = _clip_path_of(shot)
        if clip_path is not None:
            probe = probe_clip(clip_path)  # 模块顶层导入:测试可整体替换
            dur = (probe or {}).get("duration_s") or max(1.0, float(shot.duration_s or 4))
            has_audio = bool((probe or {}).get("has_audio")) or _latest_task_kind(db, shot.id) == "talk"
            plan.items.append(SynthItem(
                kind="clip", path=clip_path, duration_s=float(dur),
                text=text, has_audio=has_audio,
            ))
            if (probe or {}).get("width"):
                plan.width, plan.height = int(probe["width"]), int(probe["height"])
            continue
        still = _still_path_of(shot)
        if still is not None:
            dur = max(1.0, min(15.0, float(shot.duration_s or 4)))
            plan.items.append(SynthItem(
                kind="still", path=still, duration_s=dur, text=text, has_audio=False,
            ))
            continue
        plan.skipped_seqs.append(int(shot.seq))
    # 分辨率锚:优先首个视频片段的实测值;全是静帧时按竖屏默认
    if plan.width <= 0:
        plan.width, plan.height = 720, 1280
    return plan


def build_srt(plan: SynthPlan) -> str:
    """字幕:文本取每格台词,时间轴按**实际片段时长**累计(空台词格只跳字幕不跳时长)。"""
    return srt_blocks([(round(i.duration_s, 3), i.text) for i in plan.items])


def _subtitle_font() -> str:
    """中文字体按平台给一个稳妥默认(libass 找得到就用,找不到回退默认字体)。"""
    return "Microsoft YaHei" if os.name == "nt" else "Noto Sans CJK SC"


def build_command(plan: SynthPlan, *, burn_subtitles: bool, bgm_path: Path | None,
                  out_path: Path, srt_rel: str = "sub.srt") -> list[str]:
    """拼 ffmpeg 命令。输入顺序:各片段(clip 普通 / still -loop)→ BGM(无限循环)。"""
    cmd: list[str] = ["ffmpeg", "-y"]
    for item in plan.items:
        if item.kind == "still":
            cmd += ["-loop", "1", "-t", f"{item.duration_s:.3f}", "-i", str(item.path)]
        else:
            cmd += ["-i", str(item.path)]
    if bgm_path is not None:
        cmd += ["-stream_loop", "-1", "-i", str(bgm_path)]
    bgm_idx = len(plan.items)

    w, h = plan.width, plan.height
    chains: list[str] = []
    concat_in = ""
    for i, item in enumerate(plan.items):
        chains.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]"
        )
        if item.has_audio and item.kind == "clip":
            chains.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo[a{i}]")
        else:
            chains.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{item.duration_s:.3f}[a{i}]")
        concat_in += f"[v{i}][a{i}]"
    chains.append(f"{concat_in}concat=n={len(plan.items)}:v=1:a=1[vc][ac]")

    video_out = "vc"
    if burn_subtitles:
        style = f"FontName={_subtitle_font()},FontSize=14,Outline=1,Shadow=0,MarginV=30"
        chains.append(f"[vc]subtitles={srt_rel}:force_style='{style}'[vf]")
        video_out = "vf"
    if bgm_path is not None:
        chains.append(f"[{bgm_idx}:a]volume={_BGM_VOLUME}[bg]")
        chains.append("[ac][bg]amix=inputs=2:duration=first:dropout_transition=0[aout]")
        audio_out = "aout"
    else:
        audio_out = "ac"

    cmd += [
        "-filter_complex", ";".join(chains),
        "-map", f"[{video_out}]", "-map", f"[{audio_out}]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    return cmd


def run_synthesis(progress, plan: SynthPlan, *, burn_subtitles: bool,
                  bgm_path: Path | None, out_path: Path) -> Path:
    """跑一次整集合成(同步阻塞,调用方放线程池)。成功返回输出路径。"""
    if len(plan.items) == 0:
        raise SynthError("没有可合成的片段:先出几格草片(或挂静帧当占位)。")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    srt_rel = "sub.srt"
    if burn_subtitles:
        srt = build_srt(plan)
        if not srt.strip():
            burn_subtitles = False  # 全集无台词:别留一个空 srt 让 ffmpeg 报错
        else:
            (out_path.parent / srt_rel).write_text(srt, encoding="utf-8")
    cmd = build_command(plan, burn_subtitles=burn_subtitles,
                        bgm_path=bgm_path, out_path=out_path, srt_rel=srt_rel)
    logger.info("整集合成开始:%d 片段(视频 %d/静帧 %d),跳过 %s",
                len(plan.items), plan.clip_count, plan.still_count, plan.skipped_seqs or "无")
    progress(f"ffmpeg 合成中(共 {plan.total_s:.0f} 秒成片,几分钟属正常)…")
    try:
        proc = subprocess.run(  # noqa: S603 — 参数全部服务端构造,无用户输入
            cmd, cwd=str(out_path.parent), capture_output=True, timeout=_SYNTH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise SynthError(f"合成超时(超过 {_SYNTH_TIMEOUT_S // 60} 分钟),请减少格数或重试。") from exc
    if proc.returncode != 0 or not out_path.is_file():
        tail = (proc.stderr or b"")[-300:].decode("utf-8", errors="replace")
        raise SynthError(f"ffmpeg 合成失败:{tail or '未知错误(查看后端日志)'}")
    logger.info("整集合成完成 → %s(%.1fMB)", out_path.name, out_path.stat().st_size / 1024 / 1024)
    return out_path


def find_bgm(project_id: int, episode_id: int) -> Path | None:
    """找该集已上传的 BGM(drama/<pid>/bgm<eid>.<ext>);没有返回 None。"""
    from app import storage

    d = storage.upload_root() / "drama" / str(int(project_id))
    if not d.is_dir():
        return None
    for ext in ("mp3", "wav"):
        p = d / f"bgm{int(episode_id)}.{ext}"
        if p.is_file():
            return p
    return None


def cleanup_old_synth(episode_id: int, keep: Path) -> int:
    """同集只留最新一条成片,旧的删掉(一条 30MB+,堆着吃卷)。"""
    from app import storage

    d = storage.upload_root() / "render" / "synth"
    removed = 0
    if d.is_dir():
        for f in d.glob(f"e{int(episode_id)}-t*.mp4"):
            if f.resolve() != keep.resolve():
                f.unlink(missing_ok=True)
                removed += 1
    return removed
