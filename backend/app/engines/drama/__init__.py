# app/engines/drama/__init__.py
"""E. 漫剧工坊引擎:小说 → 拍摄手册(风格/角色/场景资产卡 → 集规划 → 剧本 → 分镜 → 三轨提示词)。

四步管线每步独立可重跑(借鉴 LumenX 阶段化);只产提示词不接生成模型
(沿用封面/主题曲哲学);准入门槛 = 有已定稿章节(衍生工坊定位)。
"""
from .characters import DramaAssetError, generate_assets, generate_character_cards, generate_scene_cards
from .exporter import export_csv, export_json, export_markdown
from .planner import DramaPlanError, plan_episodes
from .prompt_render import DramaPromptError, render_shot_prompts
from .script import DramaScriptError, write_episode_script
from .storyboard import DramaStoryboardError, build_storyboard
from .style import generate_style_card

__all__ = [
    "DramaAssetError",
    "DramaPlanError",
    "DramaScriptError",
    "DramaStoryboardError",
    "DramaPromptError",
    "generate_style_card",
    "generate_character_cards",
    "generate_scene_cards",
    "generate_assets",
    "plan_episodes",
    "write_episode_script",
    "build_storyboard",
    "render_shot_prompts",
    "export_markdown",
    "export_csv",
    "export_json",
]
