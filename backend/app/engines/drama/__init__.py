# app/engines/drama/__init__.py
"""E. 漫剧工坊引擎:小说 → 拍摄手册(风格/角色/场景资产卡 → 集规划 → 剧本 → 分镜 → 三轨提示词)。

四步管线每步独立可重跑(借鉴 LumenX 阶段化);只产提示词不接生成模型
(沿用封面/主题曲哲学);准入门槛 = 有已定稿章节(衍生工坊定位)。
阶段 2 补齐出片最后一块:声线选型卡 + 成片包(配音稿/剪辑清单/SRT 字幕)。
"""
from .characters import DramaAssetError, generate_assets, generate_character_cards, generate_scene_cards
from .exporter import export_csv, export_json, export_markdown, export_pack_markdown, export_srt
from .planner import DramaPlanError, plan_episodes
from .production import DramaPackError, build_production_pack
from .prompt_render import DramaPromptError, render_shot_prompts
from .script import DramaScriptError, write_episode_script
from .storyboard import DramaStoryboardError, build_storyboard
from .style import generate_style_card
from .voice import DramaVoiceError, generate_voice_cast

__all__ = [
    "DramaAssetError",
    "DramaPlanError",
    "DramaScriptError",
    "DramaStoryboardError",
    "DramaPromptError",
    "DramaVoiceError",
    "DramaPackError",
    "generate_style_card",
    "generate_character_cards",
    "generate_scene_cards",
    "generate_assets",
    "generate_voice_cast",
    "plan_episodes",
    "write_episode_script",
    "build_storyboard",
    "render_shot_prompts",
    "build_production_pack",
    "export_markdown",
    "export_csv",
    "export_json",
    "export_srt",
    "export_pack_markdown",
]
