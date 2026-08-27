# app/prompts/loader.py
# -*- coding: utf-8 -*-
"""Prompt 模板加载器。

支持从独立的 .txt 文件加载 prompt 模板,用 str.format() 注入变量。
模板文件放在 app/prompts/templates/ 目录下,按模块分子目录。

设计要点:
- 模板用 Python str.format() 语法:{variable} 占位符,{{ }} 转义字面大括号
- 加载时带缓存,避免每次都读文件
- 支持 PyInstaller 冻结环境(从 _MEIPASS 解析路径)
- 缺失变量时 str.format() 会抛 KeyError,便于发现问题

使用方式:
    from app.prompts.loader import load_prompt

    # 加载模板并注入变量
    prompt = load_prompt("rolling/macro_plan.txt", number_of_chapters=30, ...)

    # 只加载模板字符串(不注入变量)
    template = load_prompt("rolling/macro_plan.txt")
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("jarvis-write.prompts")

# 模板目录:源码环境下是 app/prompts/templates/,冻结环境下从 _MEIPASS 解析
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _resolve_template_path(name: str) -> Path:
    """解析模板文件的绝对路径。

    支持 PyInstaller 冻结环境:优先从 _MEIPASS 查找,找不到则回退到源码目录。
    """
    # 冻结环境:_MEIPASS 是 PyInstaller 解包目录
    meipass = getattr(__import__("sys"), "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "app" / "prompts" / "templates" / name
        if candidate.exists():
            return candidate

    # 源码环境
    candidate = _TEMPLATES_DIR / name
    if candidate.exists():
        return candidate

    raise FileNotFoundError(f"Prompt 模板不存在: {name} (查找路径: {candidate})")


@lru_cache(maxsize=128)
def _load_template_text(name: str) -> str:
    """加载模板文件内容(带缓存)。"""
    path = _resolve_template_path(name)
    logger.debug("加载 prompt 模板: %s", path)
    return path.read_text(encoding="utf-8")


def load_prompt(name: str, **kwargs) -> str:
    """加载 prompt 模板并注入变量。

    Args:
        name: 模板文件名(相对于 templates/ 目录),如 "rolling/macro_plan.txt"
        **kwargs: 要注入的变量

    Returns:
        注入变量后的 prompt 字符串

    Raises:
        FileNotFoundError: 模板文件不存在
        KeyError: 模板中有未提供的变量
    """
    template = _load_template_text(name)
    if not kwargs:
        return template
    return template.format(**kwargs)


def clear_cache() -> None:
    """清除模板缓存(开发时修改模板文件后调用,或测试时使用)。"""
    _load_template_text.cache_clear()
    logger.info("Prompt 模板缓存已清除")
