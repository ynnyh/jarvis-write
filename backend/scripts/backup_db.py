# -*- coding: utf-8 -*-
"""SQLite 热备份(docs/37 数据留存):用 sqlite3 backup API 在线快照,不锁写、不拷半态 WAL。

为什么必须有:此前全库唯一的自动备份是「有 pending 迁移时」的迁移前快照
(保留 5 份)——平时裸奔,磁盘一坏所有用户的书一起没。本脚本给运维 cron 用:

    docker exec jarvis-write python /srv/backend/scripts/backup_db.py --keep 14

- 备份落到 <库目录>/backups/jarvis-write-<时间戳>.db(与库同一数据卷,随卷持久);
- 只保留最近 --keep 份,按文件时间裁剪;
- 在线安全:backup API 在页粒度拷贝且对源库只读,业务照常写。
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path


def _db_path_from_url(url: str) -> Path | None:
    """sqlite:///relative.db | sqlite:////abs/path.db → Path;非 sqlite 返回 None。"""
    m = re.match(r"^sqlite:///(?!/)(.+)$", url)
    if m:
        return Path(m.group(1))
    m = re.match(r"^sqlite:////(.+)$", url)
    if m:
        return Path("/") / m.group(1)
    return None


def backup(db_path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"jarvis-write-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)  # 页粒度在线拷贝,不锁写
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def prune(out_dir: Path, keep: int) -> int:
    """按文件名时间戳序保留最近 keep 份,返回删掉的份数。"""
    files = sorted(out_dir.glob("jarvis-write-*.db"))
    removed = 0
    for old in files[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
        removed += 1
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="jarvis-write SQLite 热备份")
    ap.add_argument("--db", default="", help="SQLite 库路径;缺省从 DATABASE_URL 解析")
    ap.add_argument("--dir", default="", help="备份目录;缺省 <库目录>/backups")
    ap.add_argument("--keep", type=int, default=14, help="保留最近几份(0=不清理)")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else None
    if db_path is None:
        url = os.environ.get("DATABASE_URL", "")
        db_path = _db_path_from_url(url) if url else None
    if db_path is None or not db_path.exists():
        print(f"[backup] 找不到库文件(--db 或 DATABASE_URL):{db_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.dir) if args.dir else db_path.parent / "backups"
    dest = backup(db_path, out_dir)
    removed = prune(out_dir, args.keep)
    size_mb = dest.stat().st_size / 1_048_576
    print(f"[backup] OK {dest} ({size_mb:.1f} MB, 保留期内共 {len(list(out_dir.glob('jarvis-write-*.db')))} 份, 清理 {removed} 份)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
