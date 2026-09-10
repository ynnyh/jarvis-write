# tests/test_drama_quality.py
# -*- coding: utf-8 -*-
"""漫剧线质量件(docs/15 §5.2):格式门禁 / 集末交接契约 / 版本快照。

漫剧线此前只在部分环节查「输出非空」——没有格式门禁、没有版本快照、零一致性
校验(代码量却最大)。本文件只测**确定性判据与存取**,不调 LLM:
剧本编排(重试/调用顺序)在 engines/drama/script.py,由 test_drama_script_* 覆盖。

测试用隔离内存库(app.db.session 的共享库会把 alembic_version 弄脏,
导致启动迁移跳过 FTS5 建表——细则见 MEMORY.md「测试基建纪律」)。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.db.models  # noqa: F401 — 注册全部模型
from app.db.models import Chapter, DramaEpisode, Project
from app.engines.drama import script as drama_script
from app.engines.drama.common import episode_dict
from app.engines.drama.quality import (
    MIN_LINES,
    end_state_block,
    end_state_of,
    find_version,
    push_version,
    store_end_state,
    tail_lines_text,
    validate_drama_script,
    version_list,
)


def _valid_data(n: int = 6) -> dict:
    """一份能过门禁的台词稿。"""
    return {
        "synopsis": "林砚在剑炉前认出旧敌。",
        "lines": [
            {"speaker": "林砚", "text": f"第 {i} 句台词", "action": "握剑"}
            for i in range(n)
        ],
    }


# ==================== 格式门禁 ====================

def test_gate_rejects_non_dict():
    for bad in (None, [], "文本", 42):
        ok, why = validate_drama_script(bad)
        assert not ok and why, f"{bad!r} 应被挡下"
    assert not validate_drama_script(None)[0]


def test_gate_rejects_missing_or_empty_lines():
    ok, why = validate_drama_script({"synopsis": "有梗概但没台词"})
    assert not ok and "lines" in why
    ok, why = validate_drama_script({"lines": []})
    assert not ok and "空" in why


def test_gate_rejects_too_few_lines():
    """只有 1-2 条 = 被截断(中转网关常见的尾截断),必须挡下。"""
    data = _valid_data(MIN_LINES - 1)
    ok, why = validate_drama_script(data)
    assert not ok
    assert str(MIN_LINES - 1) in why and str(MIN_LINES) in why
    # 正好达标即放行(边界)
    assert validate_drama_script(_valid_data(MIN_LINES))[0]


def test_gate_rejects_all_blank_text():
    data = {"lines": [{"speaker": "A", "text": "   "} for _ in range(8)]}
    ok, why = validate_drama_script(data)
    assert not ok and "空" in why


def test_gate_rejects_no_speakers():
    """全是空说话人 = 模型没写戏,只是复述。"""
    data = {"lines": [{"speaker": "  ", "text": f"旁白{i}"} for i in range(8)]}
    ok, why = validate_drama_script(data)
    assert not ok and "说话人" in why


def test_gate_accepts_narration_mode_all_narration():
    """口播解说模式旁白为主是正常的:有「旁白」这个说话人就算有主。"""
    data = {"lines": [{"speaker": "旁白", "text": f"解说{i}"} for i in range(8)]}
    ok, why = validate_drama_script(data)
    assert ok, why


def test_gate_counts_only_usable_lines_toward_min():
    """坏条目(非 dict / 空 text)不算数——不能靠灌垃圾凑条数过关。"""
    data = {
        "lines": [
            {"speaker": "A", "text": "真的台词1"},
            {"speaker": "B", "text": "真的台词2"},
            "坏条目",
            {"speaker": "C", "text": ""},
            None,
        ]
    }
    ok, why = validate_drama_script(data)
    assert not ok and "2 条" in why


# ==================== 台词清洗 ====================

def test_clean_lines_drops_blanks_and_caps():
    raw = [{"speaker": "A", "text": "  "}, {"speaker": "", "text": "有效"}, "垃圾"]
    out = drama_script._clean_lines(raw)
    assert out == [{"speaker": "旁白", "text": "有效", "action": ""}]

    many = [{"speaker": "A", "text": f"t{i}"} for i in range(100)]
    assert len(drama_script._clean_lines(many)) == drama_script._MAX_LINES


# ==================== 版本快照 ====================

def _ep(script=None) -> DramaEpisode:
    return DramaEpisode(
        project_id=1, ep_index=1, title="第一集", source_chapter=1,
        mode="dialogue", duration_target_s=60, script=script or {},
    )


def test_push_version_skips_when_no_old_script():
    ep = _ep()
    assert push_version(ep) == 0
    assert version_list(ep) == []


def test_push_version_round_trip():
    ep = _ep({"mode": "dialogue", "synopsis": "旧版", "lines": [{"speaker": "A", "text": "旧"}]})
    assert push_version(ep, source="generated") == 1
    # 再存一版:版本号递增,最新的在前
    ep_again = _ep(ep.script)
    ep_again.script["lines"] = [{"speaker": "B", "text": "更新"}]
    assert push_version(ep_again, source="manual") == 2
    rows = version_list(ep_again)
    assert [r["version"] for r in rows] == [2, 1]
    assert rows[0]["source"] == "manual"
    assert rows[0]["lines"][0]["text"] == "更新"
    assert find_version(ep_again, 1)["lines"][0]["text"] == "旧"
    assert find_version(ep_again, 99) is None


def test_push_version_caps_history():
    ep = _ep({"lines": [{"speaker": "A", "text": "v0"}]})
    for _ in range(15):
        push_version(ep)
        ep.script = dict(ep.script)
        ep.script["lines"] = [{"speaker": "A", "text": "next"}]
    rows = version_list(ep)
    assert len(rows) == 10
    assert rows[0]["version"] == 15  # 版本号仍连续递增,只是老版被丢


def test_version_list_survives_bad_data():
    ep = _ep({"lines": [{"speaker": "A", "text": "x"}], "_versions": [
        "垃圾", {"version": 0}, {"version": 2, "lines": [], "line_count": 0},
    ]})
    rows = version_list(ep)
    assert [r["version"] for r in rows] == [2]


# ==================== 集末交接契约 ====================

def test_end_state_round_trip_and_block():
    ep = _ep({"lines": [{"speaker": "A", "text": "x"}]})
    assert end_state_of(ep) is None  # 未提取
    state = {
        "in_story_time": "第三日 深夜",
        "location": "剑炉",
        "on_stage": ["林砚", "老周"],
        "character_states": [{"name": "林砚", "state": "左臂受伤", "doing": "握剑"}],
        "open_threads": ["断裂的剑柄"],
    }
    store_end_state(ep, state)
    assert end_state_of(ep) == state
    block = end_state_block(ep)
    for token in ("第三日 深夜", "剑炉", "林砚", "老周", "左臂受伤", "断裂的剑柄"):
        assert token in block
    # 存契约不能丢掉原本的 lines(挂在同一份 script JSON 上)
    assert ep.script["lines"][0]["text"] == "x"


def test_store_end_state_failure_keeps_lines_and_records_reason():
    ep = _ep({"lines": [{"speaker": "A", "text": "正文"}], "synopsis": "梗概"})
    store_end_state(ep, None, "模型超时")
    assert end_state_of(ep) is None
    assert end_state_block(ep) == ""  # 失败块整块省略
    assert ep.script["_end_state"]["status"] == "failed"
    assert ep.script["_end_state"]["error"] == "模型超时"
    assert ep.script["lines"][0]["text"] == "正文"  # 正文没被契约污染


def test_end_state_block_empty_for_none_or_empty_state():
    assert end_state_block(None) == ""
    ep = _ep()
    store_end_state(ep, {})
    assert end_state_block(ep) == ""  # 有 status=ok 但没有任何字段 → 不出块


def test_tail_lines_text_takes_last_n():
    ep = _ep({
        "lines": [{"speaker": "A", "text": f"第{i}句", "action": "动作" if i == 9 else ""}
                  for i in range(10)]
    })
    text = tail_lines_text(ep, limit=3)
    lines = text.split("\n")
    assert len(lines) == 3
    assert "第7句" in lines[0] and "第9句" in lines[-1]
    assert "(动作)" in lines[-1]
    # 默认上限下,全部台词都该在
    assert len(tail_lines_text(ep).split("\n")) == 10


def test_tail_lines_text_skips_blank_and_handles_no_script():
    assert tail_lines_text(_ep()) == ""
    ep = _ep({"lines": [{"speaker": "A", "text": "  "}, {"speaker": "B", "text": "有"}]})
    assert tail_lines_text(ep) == "B:有"


def test_episode_dict_exposes_end_state_through_script():
    """前端读集末契约走 episode_dict → script,不必另开接口。"""
    ep = _ep({"lines": [{"speaker": "A", "text": "x"}]})
    ep.id = 1
    store_end_state(ep, {"location": "剑炉"})
    payload = episode_dict(ep)
    assert payload["script"]["_end_state"]["state"]["location"] == "剑炉"


# ==================== 编排:门禁 → 契约 → 快照(反向验证) ====================

class _FakeAdapter:
    """按序返回预设输出;记录每次调用拿到的 prompt。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("适配器被调用的次数超出预期")
        return self.replies.pop(0)


def _script_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    project = Project(title="漫剧质量测试", target_chapters=3)
    db.add(project)
    db.flush()
    db.add(Chapter(project_id=project.id, chapter_number=1, status="approved",
                   final_content="林砚走进剑炉。" * 30, draft_content=""))
    ep = DramaEpisode(project_id=project.id, ep_index=1, title="第一集",
                      source_chapter=1, source_chapters=[1],
                      mode="dialogue", duration_target_s=60, script={})
    db.add(ep)
    db.commit()
    return db, project, ep


def _run_write(db, project, ep, adapter):
    orig = drama_script.get_adapter_for
    drama_script.get_adapter_for = lambda *a, **k: adapter
    try:
        return asyncio.run(drama_script.write_episode_script(db, project, ep))
    finally:
        drama_script.get_adapter_for = orig


def test_write_retries_once_then_succeeds():
    """第一次输出被截断(只 2 条),第二次完整 —— 应重试并最终成功。"""
    db, project, ep = _script_db()
    truncated = '{"synopsis": "半截", "lines": [{"speaker":"A","text":"1"},{"speaker":"A","text":"2"}]}'
    good = ("{\"synopsis\": \"完整\", \"lines\": ["
            + ",".join('{"speaker":"林砚","text":"台词%d","action":"动词%d"}' % (i, i) for i in range(8))
            + "]}")
    adapter = _FakeAdapter([truncated, good, '{"location": "剑炉", "open_threads": ["剑柄"]}'])
    payload = _run_write(db, project, ep, adapter)

    assert payload["status"] == "scripted"
    # 清洗把 action 前缀换成「画面」语义,顺便验证 lines 落在响应里
    assert len(payload["script"]["lines"]) == 8
    assert payload["script"]["lines"][0]["action"] == "动词0"
    # 三次调用:门禁失败的第一次 + 成功的第二次 + 集末契约
    assert len(adapter.prompts) == 3
    assert end_state_of(ep)["location"] == "剑炉"
    db.close()


def test_write_fails_after_all_attempts_and_keeps_db_untouched():
    """两次都崩 → 报错,且不落库(状态/剧本原样)。"""
    from app.engines.drama.script import DramaScriptError

    db, project, ep = _script_db()
    ep.script = {"lines": [{"speaker": "旧", "text": "旧剧本"}]}
    db.commit()
    adapter = _FakeAdapter(["{}", "不是 JSON"])
    with pytest.raises(DramaScriptError):
        _run_write(db, project, ep, adapter)
    db.refresh(ep)
    assert ep.status == "planned"
    assert ep.script["lines"][0]["text"] == "旧剧本"
    assert version_list(ep) == []  # 没成功就不该有快照
    db.close()


def test_write_snapshots_previous_script_before_overwrite():
    """重写:旧剧本先存一版,新剧本才覆盖 —— 顺序反了快照就存成新版内容。"""
    db, project, ep = _script_db()
    ep.script = {"mode": "dialogue", "lines": [{"speaker": "旧", "text": "上一版台词一"},
                                               {"speaker": "旧", "text": "上一版台词二"}]}
    db.commit()
    good = ("{\"synopsis\": \"新版\", \"lines\": ["
            + ",".join('{"speaker":"林砚","text":"新%d"}' % i for i in range(8))
            + "]}")
    adapter = _FakeAdapter([good, '{"location":"剑炉"}'])
    _run_write(db, project, ep, adapter)

    rows = version_list(ep)
    assert len(rows) == 1
    assert rows[0]["source"] == "generated"
    assert rows[0]["lines"][0]["text"] == "上一版台词一"  # 存的是旧版,不是新版
    assert ep.script["lines"][0]["text"] == "新0"
    db.close()


def test_write_degrades_when_end_state_extraction_fails():
    """契约提取失败只降级:剧本照常入库,契约标 failed。"""
    db, project, ep = _script_db()
    good = ("{\"synopsis\": \"ok\", \"lines\": ["
            + ",".join('{"speaker":"林砚","text":"新%d"}' % i for i in range(8))
            + "]}")
    # 第 2 次(契约)返回不可解析内容;ask_llm_json 会尝试续写,故再给一次垃圾
    adapter = _FakeAdapter([good, "垃圾", "还是垃圾"])
    payload = _run_write(db, project, ep, adapter)
    assert payload["status"] == "scripted"
    assert len(payload["script"]["lines"]) == 8
    assert ep.script["_end_state"]["status"] == "failed"
    db.close()


def test_prev_state_block_injected_into_prompt():
    """第 2 集写剧本时,第 1 集的集末契约要进 prompt(反向验证:清掉就没有)。"""
    db, project, ep = _script_db()
    prev = DramaEpisode(project_id=project.id, ep_index=0, title="第零集",
                        source_chapter=1, source_chapters=[1], mode="dialogue",
                        duration_target_s=60, script={"lines": [{"speaker": "A", "text": "x"}]})
    db.add(prev)
    db.flush()
    store_end_state(prev, {"location": "剑炉废墟", "open_threads": ["断裂的剑柄"]})
    db.add(Chapter(project_id=project.id, chapter_number=2, status="approved",
                   final_content="林砚醒来。" * 30, draft_content=""))
    ep.ep_index = 1
    db.commit()

    good = ("{\"synopsis\": \"ok\", \"lines\": ["
            + ",".join('{"speaker":"林砚","text":"新%d"}' % i for i in range(8))
            + "]}")
    adapter = _FakeAdapter([good, ""])
    _run_write(db, project, ep, adapter)
    main_prompt = adapter.prompts[0]
    assert "剑炉废墟" in main_prompt
    assert "断裂的剑柄" in main_prompt
    db.close()
