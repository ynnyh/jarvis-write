# tests/test_drama_video.py
# -*- coding: utf-8 -*-
"""分镜 → 视频那一步(video.py):图生视频提示词 + 视频段计划。

为什么单测这一层:
- 图生视频的第一大翻车是「提示词里又写了一遍长相」——首帧已经把脸钉死,
  文字再描述一遍模型就重画脸。所以 i2v 版**不含外貌锚**是硬约定,得钉住;
- 文生视频反过来:没有首帧,必须自带出图提示词,否则每段一张脸;
- 视频站单次时长有上限(5/10/15 秒),分镜格是 2-8 秒。并段规则(同场景/不引入
  新角色/不超上限/最多一条台词)一错,用户就得自己重排,这一层的价值全没了;
- 单格本身超上限时,必须如实标出来并给接法,不许假装一次能出。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.engines.drama.video import (
    CLIP_LIMIT_DEFAULT,
    CLIP_LIMITS,
    VIDEO_PLATFORMS,
    camera_en,
    clip_plan,
    clips_payload,
    motion_fallback,
    motion_tracks,
    normalize_limit,
    shot_video_paste,
    video_negative,
    video_paste,
)
from app.main import app

INVITE = "test-invite"


class _Shot:
    """分镜格的最小替身(video.py 不碰 DB,给属性就够)。"""

    def __init__(
        self,
        seq=1,
        scene_name="荒山雪道",
        characters=("沈砚",),
        action_desc="拔刀出鞘",
        shot_type="近景",
        camera="推",
        dialogue="",
        duration_s=4,
        prompt_cn="沈砚,黑短发,玄色劲装,雪夜拔刀,国风厚涂",
        motion_cn="",
        motion_en="",
    ):
        self.seq, self.scene_name = seq, scene_name
        self.characters = list(characters)
        self.action_desc, self.shot_type, self.camera = action_desc, shot_type, camera
        self.dialogue, self.duration_s = dialogue, duration_s
        self.prompt_cn, self.prompt_en, self.negative = prompt_cn, "en prompt", "文字水印"
        self.motion_cn, self.motion_en = motion_cn, motion_en


class _Style:
    def __init__(self, ratio="9:16", negative="文字水印、五官错位"):
        self.ratio, self.negative = ratio, negative


# =============== 运动轨:模型漏给也不能空 ===============

def test_motion_fallback_uses_camera_and_action():
    cn, en = motion_fallback(_Shot(camera="推", action_desc="缓缓抬头"))
    assert "镜头推" in cn and "缓缓抬头" in cn
    assert "幅度小" in cn                        # 动态漫默认微动
    assert "dolly in" in en                      # 运镜翻成视频站吃的镜头词
    assert "identity" in en and "unchanged" in en  # 英文轨也要求别换人


def test_motion_fallback_handles_blank_shot():
    """LLM 漏了运动轨、分镜画面也是空的:兜底给一句「只有呼吸的微动」,不能空。"""
    cn, en = motion_fallback(_Shot(camera="", action_desc=""))
    assert "呼吸" in cn and "衣料" in cn
    assert "镜头固定" in cn                       # 运镜栏空 → 回落固定
    assert en


def test_motion_tracks_fills_only_the_missing_side():
    cn, en = motion_tracks(_Shot(motion_cn="她抬手抹去刀锋上的雪,镜头缓推", motion_en=""))
    assert cn == "她抬手抹去刀锋上的雪,镜头缓推"   # 模型给了就用模型的
    assert "dolly in" in en                       # 英文缺 → 兜底补


def test_camera_en_unknown_value_is_neutral():
    assert camera_en("环绕") == "slow orbit around the subject"
    assert camera_en("升降") == "steady camera"    # 白名单外不硬翻
    assert camera_en("") == "steady camera"


# =============== 图生视频:绝不能带外貌词 ===============

def test_i2v_carries_motion_and_never_repeats_appearance():
    """i2v 的硬约定:只写怎么动,不复述长相——复述会让模型把脸重画一遍。"""
    v = video_paste(
        motion_cn="她抬手抹去刀锋上的雪",
        motion_en="she wipes snow off the blade",
        prompt_cn="沈砚,黑短发,玄色劲装,雪夜,国风厚涂",
        camera="推",
        duration_s=4,
        seq_label="第 3 格",
    )["i2v"]
    assert "【首帧】" in v["main"] and "第 3 格" in v["main"]
    assert "不许换脸" in v["main"]
    assert "【怎么动】她抬手抹去刀锋上的雪" in v["main"]
    assert "【时长】" in v["main"] and "4 秒" in v["main"]
    assert "【不要出现】" in v["main"]
    # 外貌/画风词一个都不许出现在 i2v 正文里
    for word in ("黑短发", "玄色劲装", "国风厚涂", "沈砚"):
        assert word not in v["main"], word
    assert v["negative"] == ""                     # 已改写进正文


def test_i2v_en_pairs_prompt_with_separate_negative():
    v = video_paste(motion_cn="缓缓抬头", motion_en="slowly raises her head",
                    camera="拉", duration_s=6, ratio="9:16")["i2v_en"]
    assert v["main"].startswith("slowly raises her head")
    assert "dolly out" in v["main"] and "vertical 9:16" in v["main"] and "6s" in v["main"]
    assert "face morphing" in v["negative"]        # 英文站有负面框,分开粘


def test_video_negative_stacks_style_base():
    neg = video_negative("文字水印、五官错位")
    assert "中途换脸" in neg                       # 视频特有毛病
    assert "文字水印、五官错位" in neg              # 画风卡的基座也要并进来
    assert video_negative("") .endswith("logo")    # 没基座也自成一套


# =============== 文生视频:反过来必须自带锚 ===============

def test_t2v_must_self_carry_appearance_anchor():
    v = video_paste(
        motion_cn="她抬手抹去刀锋上的雪",
        motion_en="wipes the blade",
        prompt_cn="沈砚,黑短发,玄色劲装,雪夜,国风厚涂",
        duration_s=4,
    )["t2v"]
    assert v["main"].startswith("沈砚,黑短发")     # 没首帧,外貌只能靠文字
    assert "【怎么动】" in v["main"]
    assert "每段都可能换脸" in v["hint"]            # 有人物就得警告


def test_t2v_hint_relaxes_for_empty_shot():
    v = video_paste(motion_cn="雪片斜落", motion_en="snow drifts",
                    prompt_cn="空镜,雪道", has_character=False)["t2v"]
    assert "不会有换脸问题" in v["hint"]              # 空镜没有脸可换
    assert "省掉出静帧" in v["hint"]                  # 所以可以直接文生
    assert "每段都可能换脸" not in v["hint"]           # 不给空镜扣有人物那顶警告


def test_t2v_empty_when_no_image_prompt_yet():
    """还没出图提示词时 t2v 只剩运动句——前端/导出据此提示「先出提示词」。"""
    v = video_paste(motion_cn="雪片斜落", motion_en="snow drifts", prompt_cn="")["t2v"]
    assert v["main"].startswith("【怎么动】雪片斜落")   # 结构不炸,只是没了外貌锚


def test_platform_keys_match_variant_keys():
    keys = {k for k, _ in VIDEO_PLATFORMS}
    v = video_paste(motion_cn="动", motion_en="move")
    assert keys == set(v)
    for k in keys:
        assert set(v[k]) == {"label", "main", "negative", "hint"}  # 与生图版同构


def test_shot_video_paste_reads_style():
    v = shot_video_paste(_Shot(motion_cn="缓缓抬头", motion_en="raises head"), _Style(ratio="16:9"))
    assert "16:9" in v["i2v"]["main"]
    assert "五官错位" in v["i2v"]["main"]           # 画风卡负面基座并入
    assert shot_video_paste(_Shot(), None)["i2v"]["main"]  # 没风格卡也不炸


# =============== 时长上限:超了要说,不许假装一次能出 ===============

def test_duration_over_limit_tells_you_to_chain():
    v = video_paste(motion_cn="奔跑", motion_en="running", duration_s=8, limit_s=5)["i2v"]
    assert "超过单次上限 5 秒" in v["main"]
    assert "尾帧" in v["main"]                      # 给接法:尾帧当下一次的首帧


def test_duration_within_limit_says_pick_shorter_tier():
    v = video_paste(motion_cn="奔跑", motion_en="running", duration_s=4, limit_s=10)["i2v"]
    assert "超过单次上限" not in v["main"]
    assert "宁短不长" in v["main"]                  # 站点只有固定档时选更短的


def test_normalize_limit_clamps_garbage():
    assert normalize_limit(15) == 15
    assert normalize_limit("5") == 5
    assert normalize_limit(0) == 1
    assert normalize_limit(999) == 60
    assert normalize_limit("随便") == CLIP_LIMIT_DEFAULT
    assert normalize_limit(None) == CLIP_LIMIT_DEFAULT


# =============== 并段规则:四条全满足才并 ===============

def _shots(*specs) -> list:
    """(场景, 角色, 秒, 台词) 序列 → 分镜格列表(seq 自动递增)。"""
    out = []
    for i, (scene, chars, dur, dia) in enumerate(specs, start=1):
        out.append(_Shot(seq=i, scene_name=scene, characters=chars,
                         duration_s=dur, dialogue=dia))
    return out


def test_merges_adjacent_shots_up_to_limit():
    plan = clip_plan(_shots(
        ("雪道", ("沈砚",), 3, ""),
        ("雪道", ("沈砚",), 4, ""),
        ("雪道", ("沈砚",), 4, ""),   # 3+4+4=11 > 10,这格另起一段
    ), limit_s=10)
    assert [seg["seqs"] for seg in plan["segments"]] == [[1, 2], [3]]
    assert plan["segments"][0]["duration_s"] == 7
    assert plan["totals"] == {"segments": 2, "duration_s": 11, "over_limit": 0,
                              "extra_runs": 0, "first_frames_ready": 0}
    assert plan["limit_s"] == 10 and plan["options"] == list(CLIP_LIMITS)


def test_scene_change_breaks_the_segment():
    plan = clip_plan(_shots(
        ("雪道", ("沈砚",), 2, ""),
        ("破庙", ("沈砚",), 2, ""),
    ), limit_s=10)
    assert [seg["seqs"] for seg in plan["segments"]] == [[1], [2]]


def test_new_character_breaks_the_segment():
    """并段后共用一张首帧图:第二格多了个人,那张首帧里根本没有他。"""
    plan = clip_plan(_shots(
        ("雪道", ("沈砚",), 2, ""),
        ("雪道", ("沈砚", "阿七"), 2, ""),
    ), limit_s=10)
    assert [seg["seqs"] for seg in plan["segments"]] == [[1], [2]]
    # 反过来:人变少(同一张首帧里有)可以并
    plan2 = clip_plan(_shots(
        ("雪道", ("沈砚", "阿七"), 2, ""),
        ("雪道", ("沈砚",), 2, ""),
    ), limit_s=10)
    assert [seg["seqs"] for seg in plan2["segments"]] == [[1, 2]]


def test_two_dialogue_lines_never_share_a_segment():
    """一段两句台词 = 字幕节奏与口型全对不上,宁可多生成一次。"""
    plan = clip_plan(_shots(
        ("雪道", ("沈砚",), 3, "你们要的东西不在这。"),
        ("雪道", ("沈砚",), 3, "在我手上。"),
    ), limit_s=10)
    assert [seg["seqs"] for seg in plan["segments"]] == [[1], [2]]
    # 一句台词 + 一格空镜可以并
    plan2 = clip_plan(_shots(
        ("雪道", ("沈砚",), 3, "你们要的东西不在这。"),
        ("雪道", ("沈砚",), 3, ""),
    ), limit_s=10)
    assert [seg["seqs"] for seg in plan2["segments"]] == [[1, 2]]
    assert plan2["segments"][0]["dialogue"] == "你们要的东西不在这。"


def test_single_shot_over_limit_is_declared_not_hidden():
    plan = clip_plan(_shots(("雪道", ("沈砚",), 12, "")), limit_s=5)
    seg = plan["segments"][0]
    assert seg["over_limit"] is True
    assert seg["runs"] == 3                       # 12 秒 / 5 秒上限 → 向上取整 3 次
    assert "尾帧" in seg["split_hint"] and "拆成两格" in seg["split_hint"]
    assert plan["totals"]["over_limit"] == 1 and plan["totals"]["extra_runs"] == 2
    assert "要分两次生成再接" in plan["note"]


def test_segment_meta_for_multi_shot_group():
    plan = clip_plan(_shots(
        ("雪道", ("沈砚",), 3, ""),
        ("雪道", ("沈砚",), 3, ""),
    ), limit_s=10)
    seg = plan["segments"][0]
    assert seg["index"] == 1 and seg["label"] == "第 1-2 格"
    assert seg["first_frame"] == "第 1 格的静帧"   # 首帧一律取段首格
    assert seg["motion"].startswith("先") and ";然后" in seg["motion"]
    assert seg["characters"] == ["沈砚"] and seg["scene_name"] == "雪道"
    assert seg["runs"] == 1 and seg["split_hint"] == ""
    assert clip_plan(_shots(("雪道", ("沈砚",), 3, "")))["segments"][0]["label"] == "第 1 格"


def test_empty_shots_gives_empty_plan():
    plan = clip_plan([], limit_s=10)
    assert plan["segments"] == [] and plan["totals"]["segments"] == 0
    assert plan["totals"]["extra_runs"] == 0       # sum([]) 不能炸


def test_clips_payload_attaches_paste_per_segment():
    shots = _shots(("雪道", ("沈砚",), 3, ""), ("雪道", ("沈砚",), 3, ""))
    plan = clips_payload(shots, _Style(), 10)
    paste = plan["segments"][0]["paste"]
    assert "第 1 格" in paste["i2v"]["main"]        # 首帧指到段首格
    assert "6 秒" in paste["i2v"]["main"]           # 时长是**整段**的和,不是单格
    assert " then " in paste["i2v_en"]["main"]      # 英文轨串起两格的运动
    assert "五官错位" in paste["i2v"]["main"]


# =============== API:视频段计划端点 + 手改运动轨 ===============

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _project(client: TestClient, headers: dict, title: str) -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_episode(pid: int, specs) -> tuple[int, list[int]]:
    """种一集 + 若干分镜格,返回 (集 id, 分镜 id 列表)。"""
    from app.db.models import DramaEpisode, DramaShot, DramaStyleCard
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        s.add(DramaStyleCard(project_id=pid, style_name="国风厚涂", style_cn="国风厚涂",
                             style_en="chinese ink painting", negative="文字水印", ratio="9:16"))
        ep = DramaEpisode(project_id=pid, ep_index=1, title="雪夜", source_chapters=[1])
        s.add(ep)
        s.flush()
        ids = []
        for i, (scene, chars, dur, dia) in enumerate(specs, start=1):
            shot = DramaShot(
                episode_id=ep.id, seq=i, scene_name=scene, characters=list(chars),
                action_desc="拔刀", shot_type="近景", camera="推", duration_s=dur,
                dialogue=dia, prompt_cn="沈砚,黑短发,雪夜,国风厚涂", prompt_en="draw sword",
                negative="文字水印",
            )
            s.add(shot)
            s.flush()
            ids.append(shot.id)
        s.commit()
        return ep.id, ids


def test_clips_endpoint_replans_on_limit_change(client):
    headers = _auth(client, "vid_plan")
    pid = _project(client, headers, "视频段漫剧书")
    ep_id, _ = _seed_episode(pid, [
        ("雪道", ("沈砚",), 4, ""),
        ("雪道", ("沈砚",), 4, ""),
        ("雪道", ("沈砚",), 4, ""),
    ])
    base = f"/api/projects/{pid}/drama/episodes/{ep_id}/clips"

    r = client.get(base, headers=headers)
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan["limit_s"] == CLIP_LIMIT_DEFAULT
    assert [seg["seqs"] for seg in plan["segments"]] == [[1, 2], [3]]

    # 换上限即时重算:15 秒能把三格并成一段,5 秒只能一格一段
    assert [s["seqs"] for s in client.get(f"{base}?limit_s=15", headers=headers).json()["plan"]["segments"]] == [[1, 2, 3]]
    assert [s["seqs"] for s in client.get(f"{base}?limit_s=5", headers=headers).json()["plan"]["segments"]] == [[1], [2], [3]]


def test_clips_endpoint_isolates_other_users(client):
    """别人的项目一律 404——这一条曾经真的漏过(集详情/删除/改分镜都能越权)。"""
    headers = _auth(client, "vid_owner")
    pid = _project(client, headers, "别人的漫剧书")
    ep_id, shot_ids = _seed_episode(pid, [("雪道", ("沈砚",), 4, "")])
    thief = _auth(client, "vid_thief")

    prefix = f"/api/projects/{pid}/drama"
    assert client.get(f"{prefix}/episodes/{ep_id}/clips", headers=thief).status_code == 404
    assert client.get(f"{prefix}/episodes/{ep_id}", headers=thief).status_code == 404
    assert client.patch(f"{prefix}/shots/{shot_ids[0]}", headers=thief,
                        json={"motion_cn": "被改了"}).status_code == 404
    assert client.get(f"{prefix}/episodes/{ep_id}/export?format=md", headers=thief).status_code == 404
    assert client.delete(f"{prefix}/episodes/{ep_id}", headers=thief).status_code == 404
    # 主人自己还能正常读,且没被上面那几刀改到
    r = client.get(f"{prefix}/episodes/{ep_id}", headers=headers)
    assert r.status_code == 200 and r.json()["shots"][0]["motion_cn"] == ""


def test_clips_endpoint_400_without_shots(client):
    headers = _auth(client, "vid_noshot")
    pid = _project(client, headers, "无分镜漫剧书")
    ep_id, _ = _seed_episode(pid, [])
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}/clips", headers=headers)
    assert r.status_code == 400
    assert "拆分镜" in r.json()["detail"]


def test_episode_detail_ships_video_paste_and_patch_motion(client):
    headers = _auth(client, "vid_patch")
    pid = _project(client, headers, "运动轨漫剧书")
    ep_id, shot_ids = _seed_episode(pid, [("雪道", ("沈砚",), 4, "")])

    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}", headers=headers)
    shot = r.json()["shots"][0]
    assert shot["motion_cn"] == ""                        # 还没出提示词
    assert "【怎么动】" in shot["video_paste"]["i2v"]["main"]  # 但粘贴版已兜底可用

    r = client.patch(
        f"/api/projects/{pid}/drama/shots/{shot_ids[0]}",
        headers=headers,
        json={"motion_cn": "她抬手抹去刀锋上的雪,镜头缓推", "motion_en": "wipes the blade, dolly in"},
    )
    assert r.status_code == 200, r.text
    shot = r.json()["shot"]
    assert shot["motion_cn"] == "她抬手抹去刀锋上的雪,镜头缓推"
    assert "【怎么动】她抬手抹去刀锋上的雪" in shot["video_paste"]["i2v"]["main"]
    assert shot["prompt_cn"].startswith("沈砚")            # 别的栏没被顺手清掉


def test_export_carries_video_plan_in_md_and_csv(client):
    headers = _auth(client, "vid_export")
    pid = _project(client, headers, "导出漫剧书")
    ep_id, _ = _seed_episode(pid, [
        ("雪道", ("沈砚",), 4, "你们要的东西不在这。"),
        ("破庙", ("沈砚",), 12, ""),
    ])
    base = f"/api/projects/{pid}/drama/episodes/{ep_id}/export"

    md = client.get(f"{base}?format=md", headers=headers).text
    assert "让它动起来:视频段计划" in md
    assert "图生视频·中文站" in md and "文生视频" in md
    assert "尾帧" in md                                    # 12 秒那段给了接法
    assert "这一段的字幕/配音:你们要的东西不在这。" in md

    csv_text = client.get(f"{base}?format=csv", headers=headers).text
    header = csv_text.splitlines()[0]
    for col in ("motion_cn", "clip", "clip_runs", "paste_i2v", "done_still", "done_video"):
        assert col in header, col
    # 逐格施工单:每格都带段号与该段的 i2v 提示词(paste 列里有换行,整体断言)
    assert "【怎么动】" in csv_text and "出好的静帧当首帧图" in csv_text
    assert "超过单次上限" in csv_text                  # 12 秒那格如实标出来
    assert csv_text.startswith("﻿")              # Excel 打开不乱码


# =============== 参考生视频 r2v:多图主体绑定 ===============

def test_r2v_binds_subjects_in_upload_order():
    """r2v 的核心:参考图编号与上传顺序一一对应,身份锚从「文字」换到「图」。"""
    v = video_paste(
        motion_cn="她抬手抹去刀锋上的雪",
        motion_en="she wipes snow off the blade",
        prompt_cn="沈砚,黑短发,玄色劲装,雪夜拔刀,国风厚涂",
        ref_names=("沈砚", "陆雪"),
    )
    r2v = v["r2v"]
    assert "参考图1 = 「沈砚」" in r2v["main"]
    assert "参考图2 = 「陆雪」" in r2v["main"]
    assert "严格照这张定妆照" in r2v["main"]
    # 上传顺序要能照着做:hint 里写明第几张是谁
    assert "第 1 张 = 「沈砚」" in r2v["hint"] and "第 2 张 = 「陆雪」" in r2v["hint"]
    assert "以参考图为最高优先" in r2v["hint"]      # 文字与图冲突时照图
    assert "【画面】" in r2v["main"] and "【怎么动】" in r2v["main"]
    assert "【音频】" in r2v["main"]                # 音频分轨口径同样生效


def test_r2v_without_refs_guides_back_to_ref_sheets():
    """没有定妆照时 r2v 没有意义:不给主体块,hint 明确指路,别让人拿去空跑。"""
    v = video_paste(motion_cn="雪片斜落", motion_en="snow drifts", ref_names=[])
    r2v = v["r2v"]
    assert "主体绑定" not in r2v["main"]
    assert "先回角色卡" in r2v["hint"] and "i2v / t2v" in r2v["hint"]


def test_r2v_is_last_platform_and_default_untouched():
    """新平台追加到末尾,老用户的默认偏好(i2v)不受影响。"""
    keys = [k for k, _ in VIDEO_PLATFORMS]
    assert keys == ["i2v", "i2v_en", "t2v", "r2v"]


def test_shot_video_paste_carries_refs():
    v = shot_video_paste(
        _Shot(seq=3, characters=("沈砚",)),
        _Style(),
        ref_names=("沈砚",),
    )
    assert "参考图1 = 「沈砚」" in v["r2v"]["main"]


def test_clips_payload_segment_refs_are_union_in_order():
    """段级 r2v 主体 = 段内各格参考角色的并集(按首次出现顺序去重、编号连续)。"""
    # 两格同角色才会并成一段(并段规则:不引入新角色)——这正是要并的场景
    shots = [
        _Shot(seq=1, characters=("沈砚", "陆雪")),
        _Shot(seq=2, characters=("沈砚", "陆雪")),
    ]
    plan = clips_payload(
        shots, _Style(), 10,
        refs_by_seq={1: ["沈砚"], 2: ["沈砚", "陆雪"]},
    )
    assert len(plan["segments"]) == 1
    main = plan["segments"][0]["paste"]["r2v"]["main"]
    assert "参考图1 = 「沈砚」" in main
    assert "参考图2 = 「陆雪」" in main
    assert main.count("参考图1") == 1               # 重复角色不重复编号


def test_clips_payload_without_refs_still_renders_r2v():
    """refs_by_seq 不传(旧调用方):r2v 退化成纯画面版,不许报错。"""
    plan = clips_payload([_Shot(seq=1)], _Style(), 10)
    r2v = plan["segments"][0]["paste"]["r2v"]
    assert "主体绑定" not in r2v["main"] and r2v["main"]
