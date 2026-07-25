#!/usr/bin/env bash
# jarvis-write 桌面版一键构建:前端 → 后端(PyInstaller)→ 图标 → Tauri 壳。
# 产物:src-tauri/target/release/bundle/nsis/ 下的 Windows 安装包(.exe)。
#
# 前置:Node、Python venv(backend/.venv 已装 requirements + pyinstaller)、
#       Rust/cargo。Tauri CLI 由 npx 按需拉取,无需预装。
# 用法(在仓库根):bash scripts/build-desktop.sh
#
# 注意:Tauri 配置在仓库根的 src-tauri/,frontendDist 指向 ../frontend/dist,
# 所以 `tauri build` 必须在【仓库根】运行(而非 frontend/ 内),否则找不到配置。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> [1/4] 构建前端产物 frontend/dist"
( cd frontend && npm install && npm run build )

echo "==> [2/4] 冻结后端为 onedir(PyInstaller)→ backend/dist/jarvis-write-backend"
( cd backend && .venv/Scripts/python -m PyInstaller desktop.spec --noconfirm )

echo "==> [3/4] 生成 Tauri 图标(从 src-tauri/icon-source.png → src-tauri/icons/)"
npx --yes @tauri-apps/cli@^2 icon src-tauri/icon-source.png

echo "==> [4/4] 构建 Tauri 桌面壳 + 打包 NSIS 安装包"
# 开启了 createUpdaterArtifacts,本地构建也必须签名,否则 tauri build 会失败。
# 优先用环境变量 TAURI_SIGNING_PRIVATE_KEY;否则读本地密钥文件 src-tauri/updater.key(已 gitignore)。
if [ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ]; then
  if [ -f "src-tauri/updater.key" ]; then
    export TAURI_SIGNING_PRIVATE_KEY="$(cat src-tauri/updater.key)"
    echo "    (已从 src-tauri/updater.key 载入更新签名密钥)"
  else
    echo "错误:缺少更新签名密钥。" >&2
    echo "  请把更新签名私钥放到 src-tauri/updater.key,或设置环境变量 TAURI_SIGNING_PRIVATE_KEY。" >&2
    echo "  (私钥备份在第一次生成时给过你;丢失后将无法再发布可自动更新的版本。)" >&2
    exit 1
  fi
fi
npx --yes @tauri-apps/cli@^2 build --bundles nsis

echo
echo "完成。产物在 src-tauri/target/release/bundle/nsis/ ——"
echo "  · jarvis-write_<版本>_x64-setup.exe        → Windows 安装包(分发用)"
echo "  · jarvis-write_<版本>_x64-setup.nsis.zip   → 自动更新包(配 .sig 签名,CI 发版用)"
