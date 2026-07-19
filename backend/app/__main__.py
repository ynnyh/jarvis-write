# app/__main__.py
# -*- coding: utf-8 -*-
"""让 `python -m app` 直接起服务。"""
from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
