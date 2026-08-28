# app/db/models/render.py
"""出片引擎:autodl.art ComfyUI 工作流的账号配置与渲染任务记录。

架构见 docs/adr/0003:工坊做「控制面」(哪个镜头、什么提示词、出完挂回哪),
视频生成本身外包给 autodl.art 托管的 ComfyUI 工作流(「执行面」)。
- RenderConfig:每用户一份出片账号。token 是账号凭据,照 provider_configs 的
  模式加密落库、接口打码回显;
- RenderTask:一次出片尝试一行——重 roll 攒版本,「哪版当成片」由各线自己的
  指针字段决定(drama_shots.clip_ref / ClipShoot.shoot[].result_link),
  本表只记历史,不做最终态。渲染出的视频文件落 uploads/render/,URL 短效
  必须当场下载,这也是本表存在的原因之一:离了它,出过的片就找不回来了。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin

# autodl.art 的默认入口(用户自建反代/换域名时在设置页改)
DEFAULT_RENDER_BASE_URL = "https://www.autodl.art"
# 轻量档两个预置工作流:有静帧走首尾帧,无静帧走纯文生
DEFAULT_WORKFLOW_I2V = "minimax_h3_lightx2v"
DEFAULT_WORKFLOW_T2V = "minimax_h3_lightx2v_no_pic"
# 完整档对白链:先 indextts2 配音,再对口型出"开口说话"的视频
DEFAULT_WORKFLOW_TTS = "indextts2-v1"
DEFAULT_WORKFLOW_TALK = "minimax_h3_image_audio_to_video"


class RenderConfig(Base, TimestampMixin):
    """某用户的出片引擎配置(每用户一行;token 走 crypto.encrypt 密文)。"""

    __tablename__ = "render_configs"
    __table_args__ = (UniqueConstraint("user_id", name="uq_render_config_per_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    base_url: Mapped[str] = mapped_column(String(300), default=DEFAULT_RENDER_BASE_URL)
    # 密文(ENC_PREFIX 前缀);明文只在提交请求的一瞬间存在
    token: Mapped[str] = mapped_column(String(500), default="")
    # 画质档:480p / 768p(竖横由各线的画幅折算,轻量档不追 1080p)
    resolution: Mapped[str] = mapped_column(String(10), default="768p")
    workflow_i2v: Mapped[str] = mapped_column(String(120), default=DEFAULT_WORKFLOW_I2V)
    workflow_t2v: Mapped[str] = mapped_column(String(120), default=DEFAULT_WORKFLOW_T2V)
    # 完整档对白链:先配音(workflow_tts)再对口型(workflow_talk)
    workflow_tts: Mapped[str] = mapped_column(String(120), default=DEFAULT_WORKFLOW_TTS)
    workflow_talk: Mapped[str] = mapped_column(String(120), default=DEFAULT_WORKFLOW_TALK)


class RenderTask(Base, TimestampMixin):
    """一次出片尝试。status 流转 queued → running → success/failed。

    unit 三态:漫剧填 shot_id(project_id 必有);情绪短片填 clip_id+chunk_index
    (短片不挂项目,project_id 为空)。params 存提交参数快照(prompt/时长/分辨率),
    出问题能对着复现,重 roll 也好对比两版差在哪。
    """

    __tablename__ = "render_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    line: Mapped[str] = mapped_column(String(10), index=True)  # drama | clips
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # 集级任务(整集一键合成 kind="synth")专用:shot_id/clip_id 皆为空时看这里
    episode_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    shot_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    clip_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=-1)
    kind: Mapped[str] = mapped_column(String(10), default="")  # i2v | t2v
    workflow_id: Mapped[str] = mapped_column(String(120), default="")
    provider_task_id: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # uploads/render/ 下的相对文件名(渲染产物;URL 短效,成功即下载落盘)
    result_path: Mapped[str] = mapped_column(String(300), default="")
    error: Mapped[str] = mapped_column(Text, default="")


class TtsTrack(Base, TimestampMixin):
    """一段已合成的配音(indextts2 结果缓存),cache_key 命中即免费复用。

    为什么缓存:重 roll 视频不该重付配音钱(¥0.02/次是小钱,但更要紧的是
    同一台词同一音色每次合成时长会差零点几秒,画面节奏就跟着漂)。key =
    sha256(workflow|voice_src|emotion|text) 前 16 位;文件落
    uploads/render/tts/<key>.wav。删项目不删这里——音色与台词是用户级资产,
    换个项目念同一句照样命中。
    """

    __tablename__ = "tts_tracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    cache_key: Mapped[str] = mapped_column(String(16), unique=True)
    voice_src: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    emotion: Mapped[str] = mapped_column(String(20), default="")
    workflow_id: Mapped[str] = mapped_column(String(120), default="")
    # 真实音频秒数(wav 头解析;对白格的 audio_duration 以它为准,TTS-first)
    duration_s: Mapped[float] = mapped_column(default=0.0)
    path: Mapped[str] = mapped_column(String(300), default="")
