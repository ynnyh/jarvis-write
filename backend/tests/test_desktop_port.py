# -*- coding: utf-8 -*-
"""desktop_main._bind_server_socket:端口绑定语义(锁死探测窗口,见其 docstring)。

只测纯 socket 逻辑,不起 uvicorn/app(desktop_main 顶层只 import 标准库,
可以直接导入)。
"""
from __future__ import annotations

import socket

import pytest

from desktop_main import PREFERRED_PORT, _bind_server_socket

HOST = "127.0.0.1"


def test_preferred_port_when_free():
    """8756 空闲时优先使用,且返回的 socket 已绑定在该端口上。"""
    # 占住 8756 再释放,确保本条测试拿到的是「空闲」前提
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as hold:
        hold.bind((HOST, 0))
        pass
    sock = _bind_server_socket(HOST, 0)
    try:
        assert sock.getsockname()[1] == PREFERRED_PORT
    finally:
        sock.close()


def test_falls_back_when_preferred_occupied():
    """8756 被占时回退到 OS 分配的空闲端口(非 8756,且真正可用)。"""
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind((HOST, PREFERRED_PORT))
    try:
        sock = _bind_server_socket(HOST, 0)
        try:
            got = sock.getsockname()[1]
            assert got != PREFERRED_PORT
            # 返回的端口确实被本 socket 独占:第三方再绑同端口应失败
            other = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with pytest.raises(OSError):
                    other.bind((HOST, got))
            finally:
                other.close()
        finally:
            sock.close()
    finally:
        blocker.close()


def test_env_port_conflict_raises():
    """显式 JARVIS_PORT 被占用 → 抛 OSError(调用方据此清晰报错退出)。"""
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind((HOST, 0))
    occupied = blocker.getsockname()[1]
    try:
        with pytest.raises(OSError):
            _bind_server_socket(HOST, occupied)
    finally:
        blocker.close()


def test_env_port_free_binds_it():
    """显式 JARVIS_PORT 空闲 → 精确绑定该端口。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind((HOST, 0))
    free_port = probe.getsockname()[1]
    probe.close()
    sock = _bind_server_socket(HOST, free_port)
    try:
        assert sock.getsockname()[1] == free_port
    finally:
        sock.close()
