# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包规格:把 FastAPI 后端冻结成单个 Windows exe(桌面版)。

产物:dist/jarvis-write-backend/jarvis-write-backend.exe(onedir 模式,启动快)。
入口 desktop_main.py 会设 local 模式 + 用户数据目录,再拉起 uvicorn。

打包资源:
- app/static  → 冻结根的 app/static(settings.html)
- ../frontend/dist → 冻结根的 frontend/dist(SPA 产物;须先 npm run build)

依赖收集:uvicorn/fastapi/pydantic 有大量运行时动态子模块,用 collect_submodules
全量纳入,避免冻结后 ModuleNotFoundError。
"""
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = []
for pkg in (
    "uvicorn",
    "fastapi",
    "pydantic",
    "pydantic_settings",
    "app",
):
    hidden += collect_submodules(pkg)

datas = [
    ("app/static", "app/static"),
    ("../frontend/dist", "frontend/dist"),
]
# pydantic 等可能带数据文件
datas += collect_data_files("pydantic")

a = Analysis(
    ["desktop_main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 明确排除已移除/无关的重依赖,缩小体积
        "chromadb",
        "tkinter",
        "matplotlib",
        "pytest",
        "PyInstaller",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="jarvis-write-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 桌面壳会隐藏窗口;调试期保留控制台看日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="jarvis-write-backend",
)
