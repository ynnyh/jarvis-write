# -*- coding: utf-8 -*-
"""生成 NSIS 安装器品牌图:header.bmp(150x57)+ sidebar.bmp(164x314)。

素材:src-tauri/icon-source.png(512 圆角紫底 jW 标)。
- header.bmp:安装器每页顶部右侧的小图,MUI 头部为白底,直接放白底 + 右侧图标。
- sidebar.bmp:欢迎/完成页左侧大图,用品牌紫铺底 + 居中图标。
输出到 src-tauri/installer/(tauri.conf.json 的 bundle.nsis 引用)。
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent / "src-tauri"
OUT = ROOT / "installer"
OUT.mkdir(exist_ok=True)

icon = Image.open(ROOT / "icon-source.png").convert("RGBA")
# 品牌紫:取出现最多的颜色(中心是白色 jW 字母,不能直接取中心点)
BRAND = max(icon.getcolors(icon.width * icon.height), key=lambda c: c[0])[1][:3]
print("品牌色:", "#%02x%02x%02x" % BRAND)


def paste_center(bg: Image, fg: Image, size: int, cx: int, cy: int) -> None:
    fg_r = fg.resize((size, size), Image.LANCZOS)
    bg.paste(fg_r, (cx - size // 2, cy - size // 2), fg_r)


# ---- header.bmp:150x57 白底,图标靠右垂直居中(NSIS 头部右对齐展示)----
header = Image.new("RGB", (150, 57), (255, 255, 255))
paste_center(header, icon, 44, 150 - 30, 28)
header.save(OUT / "header.bmp")

# ---- sidebar.bmp:164x314 品牌紫铺底,图标居中偏上 ----
sidebar = Image.new("RGB", (164, 314), BRAND)
paste_center(sidebar, icon, 110, 82, 120)
sidebar.save(OUT / "sidebar.bmp")

print("已生成:", OUT / "header.bmp", OUT / "sidebar.bmp", sep="\n  ")
