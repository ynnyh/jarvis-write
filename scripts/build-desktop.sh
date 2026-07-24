#!/usr/bin/env bash
# jarvis-write 桌面版一键构建:前端 → 后端(PyInstaller)→ Tauri 壳。
# 产物:src-tauri/target/release/bundle/ 下的安装包与免安装 exe。
#
# 前置:Node、Python venv(backend/.venv 已装 requirements + pyinstaller)、
#       Rust/cargo、前端已 npm install(含 @tauri-apps/cli)。
# 用法(在仓库根):bash scripts/build-desktop.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> [1/3] 构建前端产物 frontend/dist"
( cd frontend && npm run build )

echo "==> [2/3] 冻结后端为 onedir(PyInstaller)"
( cd backend && .venv/Scripts/python -m PyInstaller desktop.spec --noconfirm )

echo "==> [3/3] 构建 Tauri 桌面壳 + 打包"
( cd frontend && npx tauri build )

echo
echo "完成。产物在 src-tauri/target/release/bundle/ ——"
echo "  · nsis/*.exe   → Windows 安装包(推荐分发)"
echo "  · 免安装:src-tauri/target/release/jarvis-write-desktop.exe(需同目录 resources)"
