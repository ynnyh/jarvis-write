"""先写完整可演短剧，再拆镜；按版本保存台词与节奏，避免分镜阶段另编故事。"""
from __future__ import annotations

import json
import re
import math

from app.engines.anime.common import AnimeError, cast_block, episode_dict
from app.engines.creative import render_goal
from app.engines.consistency.extractor import parse_llm_json
from app.llm.router import Task, get_adapter_for


def episode_cast(series, episode) -> list[dict]:
    return list(series.cast or []) + list(episode.guests or [])


def invalidate_script(episode):
    if episode.script:
        episode.script = {**episode.script, "stale": True}
    episode.shots = []
    episode.film_prompt = ""


def normalize_script(data: dict, series, episode) -> dict:
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes or len(scenes) > 8:
        raise AnimeError("剧本需要1—8个完整场次")
    names = {c["name"] for c in episode_cast(series, episode)} | {"旁白", ""}
    clean = []
    total = 0
    count = 0
    for scene in scenes:
        if not isinstance(scene, dict) or not isinstance(scene.get("lines"), list) or not scene["lines"]:
            raise AnimeError("场次缺可演的动作与对白")
        lines = []
        for line in scene["lines"]:
            if not isinstance(line, dict):
                raise AnimeError("台词格式不完整")
            speaker = str(line.get("speaker") or "").strip()
            text = str(line.get("text") or "").strip()
            action = str(line.get("action") or "").strip()
            if speaker not in names:
                raise AnimeError(f"{speaker}尚未定妆，请先添加单集客串角色")
            if len(text) > 180 or len(action) > 400 or not (text or action):
                raise AnimeError("动作/对白为空或过长，请按可演节拍写")
            if text and not speaker:
                raise AnimeError("有对白的节拍必须写说话人")
            try:
                duration = int(line.get("duration_s", 0))
                pause = float(line.get("pause_s", 0))
            except (ValueError, TypeError, OverflowError):
                raise AnimeError("节拍时长格式不对") from None
            if not math.isfinite(pause) or not 1 <= duration <= 20 or not 0 <= pause <= duration or len(text) > (duration - pause) * 5:
                raise AnimeError("对白与停顿装不进节拍时长，请减少台词或留足时间")
            total += duration
            count += 1
            lines.append({"speaker": speaker, "text": text, "action": action, "duration_s": duration, "pause_s": pause})
        clean.append({"slug": str(scene.get("slug") or "")[:100], "purpose": str(scene.get("purpose") or "")[:150], "lines": lines})
    if not 4 <= count <= 60 or abs(total - int(series.episode_s or 60)) > 8:
        raise AnimeError(f"剧本{count}个节拍、{total}秒，与目标时长不匹配")
    return {"title": str(data.get("title") or episode.title or "")[:60], "scenes": clean,
            "goal_version": (series.creative_goal or {}).get("version", 0), "total_s": total,
            "synopsis": episode.synopsis, "feedback": str(data.get("feedback") or "")[:1000]}


def save_script(episode, series, data: dict) -> dict:
    script = normalize_script(data, series, episode)
    if episode.script:
        previous = {k: v for k, v in episode.script.items() if k != "history"}
        script["history"] = [*(episode.script.get("history") or []), previous][-8:]
    episode.script = script
    episode.title = script["title"] or episode.title
    episode.shots = []
    episode.film_prompt = ""
    episode.creative_stale = False
    episode.status = "script_ready"
    return episode_dict(episode)


async def gen_script(db, series, episode, progress=lambda s: None, feedback: str = "") -> dict:
    if not episode.synopsis_ok or not (episode.synopsis or "").strip():
        raise AnimeError("先定好本集简介，再试写完整短剧本")
    if episode.creative_stale:
        raise AnimeError("方向已经变更，请先按新方向重新打磨并确认简介")
    prompt = """你是短剧编剧。按已选方向把简介写成完整可演剧本，不能只列梗概。
每个场次明确人物想得到什么、如何说话/行动以及对方怎样接招；笑点或情绪落点必须在实际台词动作里发生。
独立喜剧以人物一本正经的目的、信息差和误导为主，不用身份揭晓打脸替代包袱，不用旁白解释哪里好笑。
连续剧情保留追剧线索；生活剧允许克制；多段子合集分场独立起落，演员可以饰演不同职业但用已登记名字标说话人。
不要套万能四拍，也不要所有台词带肢体反应；给反应和停顿留时间，落点后及时结束。
只借鉴参考的机制，不复刻情节、角色和标志台词。禁止新增未定妆的角色。
输出 JSON：{"title":"","scenes":[{"slug":"内·地点·日","purpose":"本场作用","lines":[{"speaker":"已定妆名字；无台词为空","text":"实际对白","action":"可演动作","duration_s":5,"pause_s":0}]}]}。
每节拍1—20秒，pause_s计入duration_s，台词按正常说话速度；总时长接近目标±8秒。
""" + render_goal(series.creative_goal, "完整剧本") + f"\n目标：{series.episode_s}秒\n设定：{series.premise}\n简介：{episode.synopsis}\n卡司：{cast_block(episode_cast(series, episode))}\n作者反馈：{feedback[:1000]}"
    if episode.script and not episode.script.get("stale") and feedback.strip():
        prompt += "\n旧剧本（未要求的有效铺垫与事实保留）：" + json.dumps(episode.script, ensure_ascii=False)
    progress("正在试写可演的完整短剧本…")
    for attempt in range(2):
        try:
            data = parse_llm_json(await get_adapter_for(Task.ANIME_TAKES, max_tokens=8000, timeout=300).ask(prompt))
            result = save_script(episode, series, {**data, "feedback": feedback})
            db.commit()
            return result
        except ValueError as exc:
            if attempt:
                raise AnimeError(str(exc)) from exc
            prompt += f"\n上一版未通过可演检查：{exc}。请修正后重新返回完整JSON。"
    raise AnimeError("剧本未生成")


def validate_script_shots(shots, script, cast):
    """分镜可切镜，但不能吞掉/调换包袱台词，也不能凭空加对白。"""
    def compact(t):
        return re.sub(r"[\s，。！？、；：‘’“”\"',.!?;:…—]+", "", t)
    expected = [(line["speaker"], compact(line["text"])) for scene in script["scenes"] for line in scene["lines"] if compact(line["text"])]
    if not isinstance(shots, list) or any(not isinstance(s, dict) for s in shots):
        raise AnimeError("分镜应为完整镜头数组")
    actual = [(str(s.get("speaker") or ""), compact(str(s.get("dialogue") or ""))) for s in shots if compact(str(s.get("dialogue") or ""))]
    # 同一台词允许拆为相邻多个镜头；合并后逐说话人、逐文本核对。
    def merged(lines):
        out = []
        for name, text in lines:
            if out and out[-1][0] == name:
                out[-1] = (name, out[-1][1] + text)
            else:
                out.append((name, text))
        return out
    if merged(actual) != merged(expected):
        raise AnimeError("分镜丢失、改写或调换了剧本台词，请按完整剧本重新拆镜")
    names = {c["name"] for c in cast}
    if any(c not in names for s in shots for c in (s.get("characters") or [])):
        raise AnimeError("分镜出现未定妆角色")
    for shot in shots:
        if len(str(shot.get("dialogue") or "")) > float(shot.get("duration_s") or 0) * 5:
            raise AnimeError("镜头台词无法在标注时长内正常说完，请延长或拆镜")
