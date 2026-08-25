# app/engines/birthday/__init__.py
"""H. 生日祝福引擎:30/60 秒寿星定制祝福片,一次产三个本子三选一(寿星资料驱动)。"""
from .batch import BirthdayBatchError, generate_batch, pick_wish, reexpand_batch
from .common import (
    BIRTHDAY_PACKS,
    BIRTHDAY_TONES,
    MAX_MEMORIES,
    MEMORY_MAX_CHARS,
    RELATIONSHIPS,
    STATUS_CN,
    VALID_DURATIONS,
    VALID_PACKS,
    VALID_RELATIONSHIPS,
    VALID_TONES,
    wish_dict,
)
from .exporter import export_json, export_markdown, export_srt

__all__ = [
    "BirthdayBatchError",
    "generate_batch",
    "reexpand_batch",
    "pick_wish",
    "BIRTHDAY_PACKS",
    "BIRTHDAY_TONES",
    "RELATIONSHIPS",
    "VALID_TONES",
    "VALID_PACKS",
    "VALID_RELATIONSHIPS",
    "VALID_DURATIONS",
    "MAX_MEMORIES",
    "MEMORY_MAX_CHARS",
    "STATUS_CN",
    "wish_dict",
    "export_markdown",
    "export_srt",
    "export_json",
]
