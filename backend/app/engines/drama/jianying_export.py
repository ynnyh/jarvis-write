# app/engines/drama/jianying_export.py
# -*- coding: utf-8 -*-
"""剪映草稿导出(docs v0.55.0,业界对照 ArcReel 的「导出剪映草稿」)。

用 pyJianYingDraft 生成剪映 6/7 草稿目录(zip 交付):
- 文本轨:分镜台词按累计时间码排好(复用 SRT 导出逻辑,import_srt 一步入轨);
- 视频轨:MVP 不铺素材(站内素材在用户本地没有路径,硬引用会成黑块),
  留一条带每镜占位说明的文本轨「分镜对照」供对齐。
用户解压到剪映草稿目录即可打开:字幕轨已排好,挨个拖素材对齐即可。

pyJianYingDraft 是纯 Python 库无重依赖;草稿格式随剪映版本演进,若导出
打不开优先升级该库(而不是自写 JSON——字段面太大,版本敏感)。
"""
from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from app.db.models import DramaEpisode, DramaShot
from app.engines.media.subtitles import srt_blocks


def _shots_srt(shots: list[DramaShot]) -> str:
    """分镜台词 → SRT(统一走 media/subtitles 内核,与全站时间轴口径一致)。"""
    return srt_blocks(
        [(max(1, int(s.duration_s or 1)), (s.dialogue or "").strip()) for s in shots]
    )


def _board_notes_srt(shots: list[DramaShot]) -> str:
    """每镜占位说明轨:无台词的镜也有一条「第N镜·景别·画面」标记,
    用户拖素材时按它对齐位置。"""
    items: list[tuple[int, str]] = []
    for i, s in enumerate(shots, 1):
        desc = (s.action_desc or "").strip()
        note = f"第{i}镜 {s.shot_type or ''}·{desc[:30]}"
        items.append((max(1, int(s.duration_s or 1)), note))
    return srt_blocks(items)


def build_jianying_draft_zip(project_title: str, episode: DramaEpisode,
                             shots: list[DramaShot]) -> bytes:
    """生成剪映草稿 zip(解压到剪映草稿目录即被识别)。"""
    import pyJianYingDraft as draft

    sf = draft.ScriptFile(1080, 1920, 30, True)
    with TemporaryDirectory() as td:
        srt_path = Path(td) / "lines.srt"
        notes_path = Path(td) / "notes.srt"
        srt_path.write_text(_shots_srt(shots), encoding="utf-8")
        notes_path.write_text(_board_notes_srt(shots), encoding="utf-8")
        if _shots_srt(shots):
            sf.import_srt(str(srt_path), "台词")
        sf.import_srt(str(notes_path), "分镜占位(替换素材时删除此轨)")

        draft_dir = Path(td) / f"{project_title}-第{episode.ep_index}集"
        draft_dir.mkdir()
        sf.dump(str(draft_dir / "draft_content.json"))

        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in draft_dir.iterdir():
                zf.write(f, f"{draft_dir.name}/{f.name}")
            readme = (
                f"《{project_title}》第 {episode.ep_index}集《{episode.title}》剪映草稿\n\n"
                "使用方法:\n"
                "1. 解压整个文件夹到剪映草稿目录\n"
                "   Windows 通常在: C:\\\\Users\\\\你\\\\AppData\\\\Local\\\\JianyingPro\\\\User Data\\\\Projects\\\\com.lveditor.draft\\\\\n"
                "   (或从剪映首页「草稿管理」右键任意草稿打开所在目录)\n"
                "2. 重启剪映,草稿列表会出现本集,打开即是:\n"
                "   - 「台词」文本轨:每句台词已按分镜时间码排好;\n"
                "   - 「分镜占位」文本轨:每镜一条标记(第N镜·景别·画面),拖素材对齐用,替换完删除。\n"
                "3. 把你生成的每镜视频/静帧按占位位置拖上视频轨即可。\n"
            )
            zf.writestr(f"{draft_dir.name}/使用说明.txt", readme)
        return buf.getvalue()
