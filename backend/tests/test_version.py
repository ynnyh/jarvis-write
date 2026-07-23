# tests/test_version.py
# -*- coding: utf-8 -*-
"""更新提醒接口测试:/api/version 形状 + CHANGELOG 解析(取最新一条/缺文件兜底)。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.system import _latest_changelog
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_latest_changelog_parses_first_section(tmp_path):
    f = tmp_path / "CHANGELOG.md"
    f.write_text(
        "# 更新日志\n\n"
        "## 2026-07-23 新版\n- 功能甲\n- 功能乙\n\n"
        "## 2026-07-01 旧版\n- 旧的\n",
        encoding="utf-8",
    )
    result = _latest_changelog(f)
    assert result["title"] == "2026-07-23 新版"
    assert "功能甲" in result["body"] and "功能乙" in result["body"]
    # 只取最新一条,旧版内容不混入
    assert "旧的" not in result["body"]


def test_latest_changelog_missing_file_returns_empty():
    assert _latest_changelog(Path("/nonexistent/CHANGELOG.md")) == {"title": "", "body": ""}


def test_version_endpoint_shape(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["commit"], str) and data["commit"]
    assert set(data["changelog"]) == {"title", "body"}
