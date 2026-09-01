# app/storage.py
# -*- coding: utf-8 -*-
"""用户上传的图片资产落盘(漫剧的角色定妆照 + 分镜静帧)。

为什么要落盘而不是只存外链:定妆照是人物一致性的锚点,后面每一格出图都要拿它当
参考图。生图站给的图片链接普遍带时效签名,过几天就 404,靠外链等于锚点会自己消失。
所以上传优先、外链兜底(用户想贴链接也允许,但会提示可能失效)。
分镜静帧同理:出好的图挂回那一格,才知道这一集做到哪儿、哪一段的首帧图已就位。
视频成片刻意不收上传(动辄几十 MB,一集就吃满配额),只在分镜上记「成片在哪」。

安全取舍(公网试用环境,上传是新攻击面):
- 只认 PNG/JPEG/WebP,且按**文件头**判定,不信扩展名与 Content-Type;
- 文件名一律由服务端生成(项目号-卡号-序号),用户输入不参与路径;
- 大小、每角色张数、每项目总量三道上限;
- 读取走鉴权端点(不挂 StaticFiles),避免整个上传目录被公网直读。

目录:<上传根>/drama/<project_id>/<card_id>-<n>.<ext>(项目资产);
    <上传根>/clips/<clip_id>/<段号>-<n>.<ext>(短片出片参考图);
    <上传根>/birthday/<wish_id>/<段号>-<n>.<ext>(生日祝福出片参考图,多为寿星真实照片);
    <上传根>/series/<character_id>/ref-<n>.<ext>(角色系列短片的定妆参考图,全系列人物锚);
上传根默认取 SQLite 库所在目录下的 uploads/(Docker 里就是数据卷 /srv/data/uploads,随卷一起备份)。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger("jarvis-write.storage")

MAX_IMAGE_BYTES = 4 * 1024 * 1024      # 单张上限 4MB(定妆照够用,防塞大图)
MAX_REFS_PER_CARD = 3                  # 每个角色最多 3 张(正面/侧面/表情)
MAX_REFS_PER_SEGMENT = 3               # 短片出片工作台每段最多 3 张参考图(挑一版/多角度)
MAX_ASSETS_PER_SHOT = 2                # 每格分镜最多挂 2 张静帧(出两版挑一版)
MAX_PROJECT_UPLOAD_BYTES = 80 * 1024 * 1024  # 单项目上传总量上限 80MB
MAX_CLIP_UPLOAD_BYTES = 20 * 1024 * 1024     # 短片参考图上限 20MB(短片未必挂项目,单独限量)
MAX_WISH_UPLOAD_BYTES = 20 * 1024 * 1024     # 祝福片参考图上限 20MB(同理按 wish 号单独限量)
MAX_SERIES_UPLOAD_BYTES = 20 * 1024 * 1024   # 角色定妆参考图上限 20MB(按角色号单独限量)
MAX_REFS_PER_CHARACTER = 3                   # 每个系列角色最多 3 张定妆参考图(正面/侧面/表情)
MAX_RENDER_BYTES = 120 * 1024 * 1024        # 单个渲染草片上限 120MB(15s 768p 通常几 MB,放宽防意外)
MAX_AUDIO_BYTES = 8 * 1024 * 1024           # 音色参考音频上限 8MB(5-10 秒 mp3/wav 远用不满)
MAX_BGM_BYTES = 15 * 1024 * 1024            # 每集 BGM 上限 15MB(一首主题曲 mp3 通常 3-8MB)

# 文件头 → 扩展名(WebP 还要校验第 8-12 字节的 "WEBP")
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"RIFF", "webp"),
)
_CONTENT_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "webp": "image/webp",
    "mp4": "video/mp4", "mp3": "audio/mpeg", "wav": "audio/wav",
}
# 两种资产共用一个项目目录,所以分镜静帧的文件名带 shot 前缀:
# 角色卡 id 与分镜 id 来自不同表,同一个数字完全可能撞上(卡 7 与第 7 格)。
# 短片出片工作台的参考图进 clips/<clip_id>/(短片不必挂项目,独占目录,按 clip 号隔离);
# 祝福片同理进 birthday/<wish_id>/(回忆杀段的寿星真实照片是人物一致性锚点,同一定位)。
# render/r<任务号>.mp4 是出片引擎的渲染产物(引擎自己落的,不走用户上传白名单);
# render/tts/<哈希>.wav 是配音缓存(同上);drama 下的 voice<卡号> 是角色音色参考音频(固定名,重传即换)。
_REL_RE = re.compile(
    r"^(?:drama/\d+/(?:shot)?\d+-\d+\.(?:png|jpg|webp)"
    r"|drama/\d+/voice\d+\.(?:mp3|wav)"
    r"|drama/\d+/bgm\d+\.(?:mp3|wav)"
    r"|clips/\d+/\d+-\d+\.(?:png|jpg|webp)"
    r"|birthday/\d+/\d+-\d+\.(?:png|jpg|webp)"
    r"|series/\d+/ref-\d+\.(?:png|jpg|webp)"
    r"|render/r\d+\.mp4"
    r"|render/tts/[0-9a-f]{16}\.wav"
    r"|render/lf/r\d+\.png"
    r"|render/synth/e\d+-t\d+\.mp4)$"
)


class UploadError(ValueError):
    """上传相关的业务性错误(信息直接上屏)。"""


def sniff_audio_ext(data: bytes) -> str:
    """按文件头判定音频类型,返回扩展名;不是支持的音频就抛。

    mp3 两种合法开头:ID3 标签(b"ID3")或裸 MPEG 帧同步字(0xFF Ex/Fx);
    wav 是 RIFF 容器且第 8-12 字节为 "WAVE"(与渲染草片的 ftyp 校验同理,
    防上游回错误页存成音频)。
    """
    if data.startswith(b"ID3"):
        return "mp3"
    if len(data) > 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "mp3"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    raise UploadError("音色参考只支持 MP3 / WAV 音频(按文件内容判定,改扩展名无效)。")


def upload_root() -> Path:
    """上传根目录(不存在则建)。跟着 SQLite 库走,天然落在 Docker 数据卷里。"""
    url = get_settings().database_url
    if url.startswith("sqlite"):
        # sqlite:///./x.db / sqlite:////srv/data/x.db → 取库文件所在目录
        raw = url.split("///", 1)[-1]
        base = Path(raw).expanduser().resolve().parent
    else:
        base = Path.cwd()
    root = base / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def sniff_image_ext(data: bytes) -> str:
    """按文件头判定图片类型,返回扩展名;不是支持的图片就抛。"""
    for sig, ext in _SIGNATURES:
        if data.startswith(sig):
            if ext == "webp" and data[8:12] != b"WEBP":
                continue
            return ext
    raise UploadError("只支持 PNG / JPG / WebP 图片(按文件内容判定,改扩展名无效)。")


def content_type_of(rel_path: str) -> str:
    return _CONTENT_TYPES.get(rel_path.rsplit(".", 1)[-1].lower(), "application/octet-stream")


def _dir_usage(d: Path) -> int:
    """某目录下已占用的上传空间(字节)。"""
    if not d.is_dir():
        return 0
    return sum(f.stat().st_size for f in d.iterdir() if f.is_file())


def project_usage_bytes(project_id: int) -> int:
    """某项目已占用的上传空间(字节)。"""
    return _dir_usage(upload_root() / "drama" / str(int(project_id)))


def clip_usage_bytes(clip_id: int) -> int:
    """某短片已占用的参考图空间(字节)。"""
    return _dir_usage(upload_root() / "clips" / str(int(clip_id)))


def wish_usage_bytes(wish_id: int) -> int:
    """某祝福片已占用的参考图空间(字节)。"""
    return _dir_usage(upload_root() / "birthday" / str(int(wish_id)))


def series_usage_bytes(character_id: int) -> int:
    """某系列角色已占用的定妆参考图空间(字节)。"""
    return _dir_usage(upload_root() / "series" / str(int(character_id)))


def save_character_ref(project_id: int, card_id: int, data: bytes, taken: int) -> str:
    """保存一张定妆照,返回相对路径(存进 DramaCharacterCard.ref_images)。

    taken 是该卡已有的张数,用来定序号;调用方负责张数上限的业务判断。
    """
    return _save_image(
        "drama", project_id, str(int(card_id)), data, taken,
        "定妆照", MAX_PROJECT_UPLOAD_BYTES, project_usage_bytes(project_id),
    )


def save_shot_asset(project_id: int, shot_id: int, data: bytes, taken: int) -> str:
    """保存一张分镜静帧(出好的图挂回那一格),返回相对路径(存进 DramaShot.assets)。

    文件名带 shot 前缀:与定妆照共用项目目录,而卡 id 和分镜 id 会撞号。
    """
    return _save_image(
        "drama", project_id, f"shot{int(shot_id)}", data, taken,
        "分镜静帧", MAX_PROJECT_UPLOAD_BYTES, project_usage_bytes(project_id),
    )


def save_clip_ref(clip_id: int, segment_index: int, data: bytes, taken: int) -> str:
    """保存短片出片工作台的一张段级参考图,返回相对路径(存进 ClipShoot 对应段的 ref_images)。

    stem 用段号(用户输入不参与路径);短片按 clip 号独占 clips/ 目录,不跟别的改名冲突。
    """
    return _save_image(
        "clips", clip_id, str(int(segment_index)), data, taken,
        "参考图", MAX_CLIP_UPLOAD_BYTES, clip_usage_bytes(clip_id),
    )


def save_wish_ref(wish_id: int, segment_index: int, data: bytes, taken: int) -> str:
    """保存生日祝福出片工作台的一张段级参考图(多为寿星真实照片),返回相对路径。

    与短片参考图同一定位:图生视频的人物一致性锚点,按 wish 号独占 birthday/ 目录。
    """
    return _save_image(
        "birthday", wish_id, str(int(segment_index)), data, taken,
        "参考图", MAX_WISH_UPLOAD_BYTES, wish_usage_bytes(wish_id),
    )


def save_series_ref(character_id: int, data: bytes, taken: int) -> str:
    """保存系列角色的一张定妆参考图,返回相对路径(存进 SeriesCharacter.ref_images)。

    固定主角的系列短片里,这张图是**全系列**的人物一致性锚点(每集出片都用它),
    按角色号独占 series/ 目录;文件名 ref-<n>,用户输入不参与路径。
    """
    return _save_image(
        "series", character_id, "ref", data, taken,
        "定妆参考图", MAX_SERIES_UPLOAD_BYTES, series_usage_bytes(character_id),
    )


def save_render_result(task_id: int, data: bytes) -> str:
    """落盘一个渲染草片(出片引擎的产物),返回相对路径(存 RenderTask.result_path)。

    为什么引擎产物要落盘而用户成片不收:渲染平台的结果 URL 有效期很短,不当场
    下载,出的片就永远丢了。这是引擎自己写的文件,不走用户上传的白名单/配额,
    但同样按文件头验一下 mp4(防上游回了个错误页 HTML 存成视频)。
    """
    if not data:
        raise UploadError("渲染结果是空的。")
    if len(data) > MAX_RENDER_BYTES:
        raise UploadError(f"渲染视频超过 {MAX_RENDER_BYTES // 1024 // 1024}MB 上限,已丢弃。")
    if len(data) < 12 or data[4:8] != b"ftyp":
        raise UploadError("渲染结果不是有效的 MP4 文件(平台可能返回了错误信息)。")
    d = upload_root() / "render"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"r{int(task_id)}.mp4"
    path.write_bytes(data)
    rel = f"render/{path.name}"
    logger.info("渲染草片落盘 %s(%.1fMB)", rel, len(data) / 1024 / 1024)
    return rel


def save_tts_cache(key: str, data: bytes) -> str:
    """落盘一段配音缓存(indextts2 结果),返回相对路径(存 TtsTrack.path)。"""
    if not data:
        raise UploadError("配音结果是空的。")
    if len(data) > MAX_RENDER_BYTES:
        raise UploadError("配音音频超过大小上限,已丢弃。")
    if not data.startswith(b"RIFF") or data[8:12] != b"WAVE":
        raise UploadError("配音结果不是有效的 WAV 文件(平台可能返回了错误信息)。")
    d = upload_root() / "render" / "tts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.wav").write_bytes(data)
    rel = f"render/tts/{key}.wav"
    logger.info("配音缓存落盘 %s(%.1fKB)", rel, len(data) / 1024)
    return rel


def delete_render_file(rel_path: str) -> None:
    """删一个渲染草片文件(任务/项目级联清理用;路径不合法只记日志不抛)。"""
    try:
        resolve(rel_path).unlink(missing_ok=True)
    except UploadError:
        logger.warning("跳过非法渲染产物路径的删除: %r", rel_path)


def save_character_voice(project_id: int, card_id: int, data: bytes) -> str:
    """保存一段角色音色参考音频,返回相对路径(存 DramaCharacterCard.voice_ref)。

    与定妆照的「最多 3 张挑着用」不同:音色每角色**就一段**,固定文件名
    voice<卡号>.<ext>,重传直接覆盖(换音色 = 传新的,旧的自然作废)。
    """
    if not data:
        raise UploadError("音频是空的,请重新选择文件。")
    if len(data) > MAX_AUDIO_BYTES:
        raise UploadError(
            f"音频最大 {MAX_AUDIO_BYTES // 1024 // 1024}MB,当前 "
            f"{len(data) / 1024 / 1024:.1f}MB——5-10 秒干净人声就够,请截一段再传。"
        )
    ext = sniff_audio_ext(data)
    d = upload_root() / "drama" / str(int(project_id))
    d.mkdir(parents=True, exist_ok=True)
    name = f"voice{int(card_id)}.{ext}"
    (d / name).write_bytes(data)
    rel = f"drama/{int(project_id)}/{name}"
    logger.info("音色参考落盘 %s(%.1fKB)", rel, len(data) / 1024)
    return rel


def save_episode_bgm(project_id: int, episode_id: int, data: bytes) -> str:
    """保存一集的 BGM(主题曲/垫乐),返回相对路径(集级,固定名重传即换)。"""
    if not data:
        raise UploadError("音频是空的,请重新选择文件。")
    if len(data) > MAX_BGM_BYTES:
        raise UploadError(
            f"BGM 最大 {MAX_BGM_BYTES // 1024 // 1024}MB,当前 "
            f"{len(data) / 1024 / 1024:.1f}MB,请压缩后再传。"
        )
    ext = sniff_audio_ext(data)
    d = upload_root() / "drama" / str(int(project_id))
    d.mkdir(parents=True, exist_ok=True)
    name = f"bgm{int(episode_id)}.{ext}"
    (d / name).write_bytes(data)
    rel = f"drama/{int(project_id)}/{name}"
    logger.info("BGM 落盘 %s(%.1fKB)", rel, len(data) / 1024)
    return rel


def _save_image(
    area: str, owner_id: int, stem: str, data: bytes, taken: int,
    what: str, limit: int, usage: int,
) -> str:
    """落盘一张用户上传的图:校验(空/大小/文件头/配额)→ 服务端定名 → 写文件。

    area 是资产分区(drama=按项目隔离,clips=按短片隔离),owner_id 是该分区内的归属号;
    stem 是文件名前缀(定妆照用卡号,分镜静帧用 shot<格 id>,出片参考图用段号),
    用户输入一律不参与路径构造。
    """
    if not data:
        raise UploadError("图片是空的,请重新选择文件。")
    if len(data) > MAX_IMAGE_BYTES:
        raise UploadError(
            f"单张图片最大 {MAX_IMAGE_BYTES // 1024 // 1024}MB,当前 "
            f"{len(data) / 1024 / 1024:.1f}MB,请压缩后再传。"
        )
    ext = sniff_image_ext(data)
    if usage + len(data) > limit:
        raise UploadError(
            f"图片空间已接近上限({limit // 1024 // 1024}MB),请先删掉不用的图片。"
        )
    d = upload_root() / area / str(int(owner_id))
    d.mkdir(parents=True, exist_ok=True)
    # 序号避让已存在的文件:删掉中间某张后再传,不覆盖别人
    n = max(taken, 0) + 1
    while (d / f"{stem}-{n}.{ext}").exists():
        n += 1
    path = d / f"{stem}-{n}.{ext}"
    path.write_bytes(data)
    rel = f"{area}/{int(owner_id)}/{path.name}"
    logger.info("%s落盘 %s(%.1fKB)", what, rel, len(data) / 1024)
    return rel


def resolve(rel_path: str) -> Path:
    """相对路径 → 绝对路径(严格白名单校验,防路径穿越)。"""
    rel = (rel_path or "").strip().replace("\\", "/")
    if not _REL_RE.match(rel):
        raise UploadError("图片路径不合法。")
    root = upload_root()
    path = (root / rel).resolve()
    if not path.is_relative_to(root.resolve()):
        raise UploadError("图片路径不合法。")
    return path


def delete(rel_path: str) -> None:
    """删一张上传的图(文件已不在也算成功——目标是「最终没有它」)。"""
    try:
        resolve(rel_path).unlink(missing_ok=True)
    except UploadError:
        logger.warning("跳过非法图片路径的删除: %r", rel_path)


def delete_project_dir(project_id: int) -> int:
    """删掉某项目的整个上传目录,返回删掉的文件数。

    删项目/删用户时调用:数据库行走了,文件不能留在卷里吃配额(每项目 80MB 的
    上限是按目录实际占用算的,不清理等于配额被幽灵文件占着)。目录名由服务端
    按项目号生成、内容只可能是我们自己落的图,所以整目录删是安全的。
    失败不往上抛:数据已经删了,清不掉文件只该记日志,不该让删除接口报错。
    """
    return _delete_owner_dir("drama", project_id)


def delete_clip_dir(clip_id: int) -> int:
    """删掉某短片的整个参考图目录,返回删掉的文件数(删短片时调用,理由同项目)。"""
    return _delete_owner_dir("clips", clip_id)


def delete_wish_dir(wish_id: int) -> int:
    """删掉某祝福片的整个参考图目录,返回删掉的文件数(删祝福片时调用,理由同项目)。"""
    return _delete_owner_dir("birthday", wish_id)


def delete_series_dir(character_id: int) -> int:
    """删掉某系列角色的整个定妆参考图目录(删角色时调用,理由同项目)。"""
    return _delete_owner_dir("series", character_id)


def _delete_owner_dir(area: str, owner_id: int) -> int:
    d = upload_root() / area / str(int(owner_id))
    if not d.is_dir():
        return 0
    removed = 0
    try:
        for f in d.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)
                removed += 1
        d.rmdir()  # 只有空目录才删得掉;有意外的子目录就留着,不递归乱删
    except OSError as exc:
        logger.warning("清理 %s/%s 上传目录未完成: %s", area, owner_id, exc)
    if removed:
        logger.info("清理 %s/%s 上传目录:删除 %d 个文件", area, owner_id, removed)
    return removed
