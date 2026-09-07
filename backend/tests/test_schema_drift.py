# tests/test_schema_drift.py
# -*- coding: utf-8 -*-
"""把 CI 的 Schema 漂移门禁挂进 pytest:models 与 Alembic 迁移必须一致。

CI 在独立 step 跑 scripts/check_schema_drift.py,本地 pytest 不跑它——
0011 迁移漏列/缺索引就是本地全绿、CI 才拦下。挂进来后本地发版前同样被拦。
"""
from __future__ import annotations

import os
import subprocess
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_schema_drift():
    r = subprocess.run(
        [sys.executable, os.path.join(BACKEND_ROOT, "scripts", "check_schema_drift.py")],
        capture_output=True, text=True, cwd=BACKEND_ROOT,
    )
    assert r.returncode == 0, (r.stdout + r.stderr).strip()
