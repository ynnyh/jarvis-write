// 漫剧工坊共享常量与纯函数(从 DramaPanel.tsx 拆出,2026-09)。
import type { DramaEpisode } from "../../dramaApi";

// 对白出片的配音情绪选项(与后端 engines/render/emotion.py 同一份清单,别各改各的)
export const EMOTION_OPTIONS: { key: string; label: string }[] = [
  { key: "", label: "平静" },
  { key: "happy", label: "开心" },
  { key: "angry", label: "愤怒" },
  { key: "sad", label: "悲伤" },
  { key: "afraid", label: "惊恐" },
  { key: "disgusted", label: "厌恶" },
  { key: "surprised", label: "惊讶" },
  { key: "melancholic", label: "忧郁" },
];

// 记住用户上次选的生图站:换一格/刷新页面不用重选(全项目共用一个偏好)
export const PASTE_PLATFORM_KEY = "jarvis_drama_paste_platform";
// 视频站的偏好单独存:生图与生视频的 key 空间不同(oneframe/dualbox/mj vs i2v/i2v_en/t2v/r2v),
// 共用一个键会互相把对方的选择顶掉。
export const VIDEO_PLATFORM_KEY = "jarvis_drama_video_platform";
// 单次生成时长上限:各视频站不一样(5/10/15 秒),记住用户那家站的档位
export const CLIP_LIMIT_KEY = "jarvis_drama_clip_limit";

/** 选中这一集时,「单集流水线」还差哪一步(状态 → 该点哪个按钮的人话)。 */
export function nextEpisodeTodo(ep: DramaEpisode): string {
  const at = `第 ${ep.ep_index} 集`;
  if (ep.status === "planned") return `${at}还没剧本:点 ④-1 写剧本。`;
  if (ep.status === "scripted") return `${at}有剧本了,点 ④-2 拆分镜(把台词摊成一格格画面)。`;
  if (ep.status === "storyboarded") return `${at}有分镜了,点 ④-3 出提示词(每格的绘图提示词)。`;
  return `${at}提示词已就绪:点 ④-4 出成片包(配音稿 + 剪辑清单),再「导出手册」。`;
}
