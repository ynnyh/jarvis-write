# app/engines/promo/__init__.py
"""F. 宣传片工坊引擎:主题(城市/景区/品牌)→ 多轮研讨 → 创作简报 → 解说词 → 分镜 → 三轨提示词 → 成片包。

与漫剧工坊共享锚段一致性与「只产提示词」哲学;独有纪律是素材点事实红线
(解说词只可引用素材点内的史实/数据,拿不准的进简报 cautions 清单)。
"""
from .assets import PromoAssetError, generate_landmarks, generate_style
from .brief import PromoBriefError, distill_brief
from .chat import PromoChatError, chat_stream
from .chunks import PromoChunkError, build_chunks
from .exporter import export_csv, export_json, export_markdown, export_srt
from .pack import PromoPackError, build_pack
from .prompt_render import PromoPromptError, render_shot_prompts
from .script import PromoScriptError, write_script
from .storyboard import PromoStoryboardError, build_storyboard

__all__ = [
    "PromoAssetError",
    "PromoBriefError",
    "PromoChatError",
    "PromoChunkError",
    "PromoPackError",
    "PromoPromptError",
    "PromoScriptError",
    "PromoStoryboardError",
    "chat_stream",
    "distill_brief",
    "generate_style",
    "generate_landmarks",
    "write_script",
    "build_storyboard",
    "render_shot_prompts",
    "build_pack",
    "build_chunks",
    "export_markdown",
    "export_csv",
    "export_json",
    "export_srt",
]
