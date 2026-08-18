# app/api/projects/_common.py
# -*- coding: utf-8 -*-
"""项目接口子包的公共件:跨端点复用的 helper。

拆分自原单文件 app/api/projects.py(987 行)。路由/行为零变化:各子模块
(naming/architecture/style_profile/blueprint)只挂自己的端点,__init__.py 用带
prefix + 鉴权依赖的主 router 聚合,并自持项目 CRUD(create/list/get/patch/delete)。

_get_project_or_404 直接复用接口层统一入口 app.api.deps.get_project_or_404(取项目
+ 校验归属,不存在/不属己均 404),消除与其字节级重复的第二份实现;包内沿用私有
下划线命名,现有 `from ._common import _get_project_or_404` 与各处调用保持不变。
"""
from __future__ import annotations

from app.api.deps import get_project_or_404 as _get_project_or_404

__all__ = ["_get_project_or_404"]
