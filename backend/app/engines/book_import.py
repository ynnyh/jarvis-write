# app/engines/book_import.py
# -*- coding: utf-8 -*-
"""整本旧书导入:TXT / DOCX → 解析分卷/章节 → 建为可继续写作的项目。

定位:已有书翻新、全书检索、阅读器这些功能的「进料口」——作者手里最常见的
载体是一个大 TXT(或 DOCX),本模块负责把它切成章节骨架:

- 卷标题(第X卷 / 【第X卷】 / Volume N)→ 本书为章制不建卷表,卷名折进章节标题前缀
- 章标题(第X章 / Chapter N / 序章 / 楔子 / 引子 / 尾声 / 后记 / 番外…)→ 一章一条
- 全文没有可识别章标题 → 按段落边界按长度兜底切章(~4000 字/章)
- 编码:utf-8(带 BOM)/ utf-16(BOM)/ gb18030(覆盖 GBK/GB2312)依次尝试

DOCX 不引入第三方依赖:docx 本质是 zip + XML,用标准库抽取正文段落即可
(只要文本,不要样式)。
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import datetime
from xml.etree import ElementTree

from sqlalchemy.orm import Session

from app.db.models import Chapter, Outline, Project

logger = logging.getLogger("jarvis-write.book_import")

# 单文件上限:网文长篇一年 300 万字约 6-10MB 纯文本,20MB 绰绰有余且防误传
MAX_IMPORT_BYTES = 20 * 1024 * 1024
# 无章标题时的兜底切章目标长度(字)
_FALLBACK_CHAPTER_CHARS = 4000
# 章标题行最大长度:超过它基本是正文里引用的标题而非标题本身
_MAX_HEADING_LEN = 60

# 章标题:第X章/节/回(X 支持汉字数字),后缀直到行尾(如「第十二章 灰塔之下」)
_RE_CHAPTER_NUM = r"[0-9零一二三四五六七八九十百千万两〇零]+"
_RE_CHAPTER = re.compile(rf"^\s*(?:第{_RE_CHAPTER_NUM}\s*[章节回])\s*(.*)$")
_RE_CHAPTER_EN = re.compile(r"^\s*(?:Chapter|CHAPTER|chapter)\s+(\d+)\s*(.*)$")
# 特殊章:序章/楔子/引子/尾声/结局/后记/番外(可带编号或副题)
_RE_CHAPTER_SPECIAL = re.compile(
    r"^\s*(序章|楔子|引子|尾声|结局|终章|后记|番外[篇0-9零一二三四五六七八九十]*)\s*(.*)$"
)
# 卷标题:第X卷 / 卷X;卷行不应再含「章/节/回」(那是章节行,上面的正则先吃掉)
_RE_VOLUME = re.compile(
    rf"^\s*【?\s*(?:第{_RE_CHAPTER_NUM}\s*卷|卷{_RE_CHAPTER_NUM})\s*】?\s*[:：·\-\s]*(.*)$"
)


def decode_text(raw: bytes) -> str:
    """按 utf-8(BOM)/ utf-16(BOM)/ gb18030 依次尝试解码;都不行再容错降级。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_docx_text(raw: bytes) -> str:
    """从 .docx(zip + XML)里抽正文段落,一段一行。只取 w:p 下的 w:t 文本。"""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            xml_bytes = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ValueError("这不是有效的 .docx 文件(无法读取正文)") from e
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as e:
        raise ValueError("这不是有效的 .docx 文件(正文 XML 解析失败)") from e
    paragraphs: list[str] = []
    for p in root.iter(f"{ns}p"):
        text = "".join(t.text or "" for t in p.iter(f"{ns}t"))
        paragraphs.append(text)
    return "\n".join(paragraphs)


def _clean_heading(raw: str, prefix: str = "") -> str:
    """章标题清洗:去首尾空白、压掉多余分隔符,限长。"""
    t = re.sub(r"\s+", " ", raw).strip(" -:：·—-、.。")
    t = (prefix + t).strip()
    return t[:50] if t else prefix.strip()[:50] or "未命名"


def parse_chapters(text: str) -> list[dict[str, str]]:
    """把整本文本切成章节列表。

    返回 [{"title": 章标题(含卷前缀), "body": 章正文}, ...];切不出章时走
    按长度兜底。解析统计(volume/recognized)由调用方按需日志。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    lines = text.split("\n")

    chapters: list[dict[str, str]] = []
    cur_volume = ""
    cur_title: str | None = None
    cur_lines: list[str] = []
    recognized = 0
    volumes_seen: set[str] = set()

    def flush() -> None:
        nonlocal cur_title, cur_lines
        if cur_title is None:
            return
        body = "\n".join(cur_lines).strip("\n")
        if body.strip():
            chapters.append({"title": cur_title, "body": body})
        cur_lines = []

    for line in lines:
        stripped = line.strip()
        m_vol = _RE_VOLUME.match(stripped)
        if m_vol and len(stripped) <= _MAX_HEADING_LEN:
            flush()
            cur_volume = _clean_heading(stripped)[:30]
            volumes_seen.add(cur_volume)
            cur_title = None
            continue
        m_ch = _RE_CHAPTER.match(stripped)
        m_en = _RE_CHAPTER_EN.match(stripped)
        m_sp = _RE_CHAPTER_SPECIAL.match(stripped)
        if (m_ch or m_en or m_sp) and len(stripped) <= _MAX_HEADING_LEN:
            flush()
            recognized += 1
            if m_ch:
                num_and_rest = stripped.strip()
                title = _clean_heading(num_and_rest, prefix=(cur_volume + " · " if cur_volume else ""))
            elif m_en:
                title = _clean_heading(stripped, prefix=(cur_volume + " · " if cur_volume else ""))
            else:
                base = (m_sp.group(1) + (" " + m_sp.group(2) if m_sp.group(2) else "")).strip()
                title = _clean_heading(base, prefix=(cur_volume + " · " if cur_volume else ""))
            cur_title = title
            continue
        cur_lines.append(line)
    flush()

    if chapters:
        logger.info("导入解析:识别章标题 %d 个,卷 %d 个,共 %d 章", recognized, len(volumes_seen), len(chapters))
        return chapters

    # 兜底:没有可识别章标题 → 按段落边界按长度切章
    paragraphs = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
    if not paragraphs:
        # 没有空行分段:按单行聚合
        paragraphs = [ln.strip() for ln in lines if ln.strip()]
    buf: list[str] = []
    size = 0
    idx = 1
    for para in paragraphs:
        buf.append(para)
        size += len(para)
        if size >= _FALLBACK_CHAPTER_CHARS:
            chapters.append({"title": f"第{idx}章", "body": "\n\n".join(buf)})
            idx += 1
            buf, size = [], 0
    if buf:
        chapters.append({"title": f"第{idx}章", "body": "\n\n".join(buf)})
    logger.info("导入解析:无章标题,兜底切为 %d 章", len(chapters))
    return chapters


def import_book_to_project(
    db: Session,
    user_id: int | None,
    filename: str,
    text: str,
    title_override: str | None = None,
) -> Project:
    """解析整本文本并落库为新项目:每章一条大纲 + 一条已定稿正文。

    导入内容是作者自己的成稿,直接置为 approved(计入总字数、可检索、
    翻新/去 AI 味都能吃),is_stale=False;大纲 summary 取本章开头一段,
    给后续的翻新/续写管线留上下文。
    """
    chapters = parse_chapters(text)
    if not chapters:
        raise ValueError("文件里没有可导入的正文内容")

    base_title = (title_override or "").strip()
    if not base_title:
        base_title = re.sub(r"\.(txt|docx)$", "", filename, flags=re.IGNORECASE).strip() or "导入的书"

    sizes = [len(c["body"]) for c in chapters]
    project = Project(
        user_id=user_id,
        title=base_title[:200],
        topic="",
        genre="",
        target_chapters=len(chapters),
        target_words_per_chapter=sorted(sizes)[len(sizes) // 2] or 3000,
    )
    db.add(project)
    db.flush()

    for i, ch in enumerate(chapters, 1):
        body = ch["body"]
        first_para = re.sub(r"\s+", "", body)[:80]
        outline = Outline(
            project_id=project.id,
            chapter_number=i,
            title=ch["title"][:200],
            summary=first_para,
            current_version=1,
        )
        db.add(outline)
        db.flush()
        db.add(
            Chapter(
                project_id=project.id,
                outline_id=outline.id,
                chapter_number=i,
                draft_content="",
                final_content=body,
                word_count=len(body),
                outline_version_used=1,
                status="approved",
                is_stale=False,
            )
        )

    db.commit()
    db.refresh(project)
    total = sum(sizes)
    logger.info(
        "导入落库:《%s》%d 章 %d 字(来源文件 %s, %s)",
        project.title, len(chapters), total, filename,
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    return project
