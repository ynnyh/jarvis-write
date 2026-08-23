# app/storage.py
# -*- coding: utf-8 -*-
"""用户上传的图片资产落盘(目前只有漫剧角色定妆照)。

为什么要落盘而不是只存外链:定妆照是人物一致性的锚点,后面每一格出图都要拿它当
参考图。生图站给的图片链接普遍带时效签名,过几天就 404,靠外链等于锚点会自己消失。
所以上传优先、外链兜底(用户想贴链接也允许,但会提示可能失效)。

安全取舍(公网试用环境,上传是新攻击面):
- 只认 PNG/JPEG/WebP,且按**文件头**判定,不信扩展名与 Content-Type;
- 文件名一律由服务端生成(项目号-卡号-序号),用户输入不参与路径;
- 大小、每角色张数、每项目总量三道上限;
- 读取走鉴权端点(不挂 StaticFiles),避免整个上传目录被公网直读。

目录:<上传根>/drama/<project_id>/<card_id>-<n>.<ext>;上传根默认取 SQLite 库
所在目录下的 uploads/(Docker 里就是数据卷 /srv/data/uploads,随卷一起备份)。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger("jarvis-write.storage")

MAX_IMAGE_BYTES = 4 * 1024 * 1024      # 单张上限 4MB(定妆照够用,防塞大图)
MAX_REFS_PER_CARD = 3                  # 每个角色最多 3 张(正面/侧面/表情)
MAX_PROJECT_UPLOAD_BYTES = 80 * 1024 * 1024  # 单项目上传总量上限 80MB

# 文件头 → 扩展名(WebP 还要校验第 8-12 字节的 "WEBP")
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"RIFF", "webp"),
)
_CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
_REL_RE = re.compile(r"^drama/\d+/\d+-\d+\.(png|jpg|webp)$")


class UploadError(ValueError):
    """上传相关的业务性错误(信息直接上屏)。"""


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


def project_usage_bytes(project_id: int) -> int:
    """某项目已占用的上传空间(字节)。"""
    d = upload_root() / "drama" / str(int(project_id))
    if not d.is_dir():
        return 0
    return sum(f.stat().st_size for f in d.iterdir() if f.is_file())


def save_character_ref(project_id: int, card_id: int, data: bytes, taken: int) -> str:
    """保存一张定妆照,返回相对路径(存进 DramaCharacterCard.ref_images)。

    taken 是该卡已有的张数,用来定序号;调用方负责张数上限的业务判断。
    """
    if not data:
        raise UploadError("图片是空的,请重新选择文件。")
    if len(data) > MAX_IMAGE_BYTES:
        raise UploadError(
            f"单张图片最大 {MAX_IMAGE_BYTES // 1024 // 1024}MB,当前 "
            f"{len(data) / 1024 / 1024:.1f}MB,请压缩后再传。"
        )
    ext = sniff_image_ext(data)
    if project_usage_bytes(project_id) + len(data) > MAX_PROJECT_UPLOAD_BYTES:
        raise UploadError(
            f"本项目上传总量已接近上限({MAX_PROJECT_UPLOAD_BYTES // 1024 // 1024}MB),"
            "请先删掉不用的定妆照。"
        )
    d = upload_root() / "drama" / str(int(project_id))
    d.mkdir(parents=True, exist_ok=True)
    # 序号避让已存在的文件:删掉中间某张后再传,不覆盖别人
    n = max(taken, 0) + 1
    while (d / f"{int(card_id)}-{n}.{ext}").exists():
        n += 1
    path = d / f"{int(card_id)}-{n}.{ext}"
    path.write_bytes(data)
    rel = f"drama/{int(project_id)}/{path.name}"
    logger.info("定妆照落盘 %s(%.1fKB)", rel, len(data) / 1024)
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
    d = upload_root() / "drama" / str(int(project_id))
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
        logger.warning("清理项目 %s 上传目录未完成: %s", project_id, exc)
    if removed:
        logger.info("清理项目 %s 上传目录:删除 %d 个文件", project_id, removed)
    return removed
