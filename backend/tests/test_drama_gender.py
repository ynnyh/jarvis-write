# tests/test_drama_gender.py
# -*- coding: utf-8 -*-
"""漫剧角色性别:判定 → 硬约束下发 → 落库 → 单卡重出 → 事后校验。

为什么专门一组测试:「女角色的提示词变成男的了」是用户实际撞到的 bug,
根因有三层,每层都得钉住:
- 模型看到的档案里常常没有性别(digest 只有 300 字,「她」正好被截掉),
  而提示词又零性别约束、示例还全是男性(剑眉入鬓/玄色劲装/青年男声)→ 一猜就猜错;
- 卡上没有性别这一栏,错了只能改自由文本,英文轨还改不到;
- 没有「只重出这一张卡」的入口,想修一个角色得整批重跑。

验证点:
- infer_gender 只吃档案里真有的线索(代词/称谓),分不清就判「未定」不硬猜
- 批量生成:性别以「档案证据/卡上已定」为准,模型说反了也按档案落库
- 定妆照:素材块带性别,引擎兜底拼的提示词中英文都带性别词
- 单卡重出:把卡上拍板的性别当硬约束下发,锁定的卡也照重(显式重出 = 覆盖)
- 序列化:描述与标定性别打架时给出人话提示(女扮男装这类由用户自行判断)
- PATCH:只收 female/male/other/空,别的 400
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.engines.drama.gender import (
    conflicting_words,
    gender_conflict_note,
    infer_gender,
    normalize_gender,
)
from app.main import app

INVITE = "test-invite"


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


def _wait_job(client: TestClient, headers: dict, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] != "running":
            return job
        assert time.monotonic() < deadline, f"job {job_id} 超时: {job}"
        time.sleep(0.02)


class _JsonAdapter:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload, ensure_ascii=False)
        self.prompts: list[str] = []

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
        return self._raw


def _seed_female_entity(pid: int) -> int:
    """种一个「档案里没写性别、但事实里全是『她』」的角色——最容易被写成男的那种。"""
    from app.db.models import Entity, Fact
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        ent = Entity(
            project_id=pid,
            entity_type="character",
            name="林晚",
            aliases=["晚娘"],
            base_profile={},
        )
        s.add(ent)
        s.flush()
        for i, content in enumerate(
            (
                "她替兄长顶下了这趟镖,谁也不知道",
                "她一手绣活养活了半条巷子的人",
                "柳夫人赐她一枚旧玉佩当念想",
            ),
            start=1,
        ):
            s.add(
                Fact(
                    project_id=pid,
                    entity_id=ent.id,
                    fact_type="state",
                    content=content,
                    valid_from=i,
                    importance="high",
                    source_chapter=i,
                )
            )
        s.commit()
        return ent.id


def _card_of(pid: int, name: str):
    from app.db.models import DramaCharacterCard
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        return (
            s.query(DramaCharacterCard)
            .filter(DramaCharacterCard.project_id == pid, DramaCharacterCard.name == name)
            .one()
        )


# =============== 判定 ===============

def test_infer_gender_only_trusts_real_clues():
    """代词/称谓算证据;线索打平或没有 → 判「未定」交用户拍板,绝不硬猜。"""
    assert infer_gender("她替兄长顶下这趟镖")[0] == "female"
    assert infer_gender("他是这条道上最稳的镖头")[0] == "male"
    # 「夫人」不许被拆成男性的「夫」(长词优先匹配)
    assert infer_gender("柳夫人赐她一枚玉佩")[0] == "female"
    # 女扮男装:一比一抵消,宁可不判
    assert infer_gender("女扮男装混进军营") == ("", "")
    assert infer_gender("这里没有任何性别线索") == ("", "")
    assert infer_gender("") == ("", "")
    # 判定要带上依据,好让用户知道凭什么这么定
    gender, why = infer_gender("她笑了", "她转身走了")
    assert gender == "female" and "她" in why


def test_normalize_gender_accepts_human_writing():
    assert normalize_gender("女") == "female"
    assert normalize_gender("FEMALE") == "female"
    assert normalize_gender("男性") == "male"
    assert normalize_gender("非二元") == "other"
    # 不认识的写法一律当「未定」,不瞎猜
    assert normalize_gender("未知") == ""
    assert normalize_gender(None) == ""


def test_conflict_words_skip_disguise_and_english_substrings():
    """挑反向性别词:「男装」不算写错(女扮男装),woman 里的 man 也不算。"""
    assert conflicting_words("female", "二十岁少年,剑眉入鬓") == ["少年"]
    assert conflicting_words("female", "一身男装,眉眼英气") == []
    assert conflicting_words("male", "young woman, silk robe") == ["woman"]
    assert conflicting_words("", "随便什么") == []


# =============== 批量生成:性别以档案为准 ===============

_CHARS_REPLY_WRONG_GENDER = {
    "cards": [
        {
            "name": "林晚",
            # 模型说反了:档案里明明全是「她」
            "gender": "male",
            "appearance_cn": "二十出头的青年男性,剑眉入鬓,玄色劲装",
            "appearance_en": "young man, black outfit",
            "outfit_cn": "玄色劲装",
            "voice_desc": "青年男声,低沉克制",
        }
    ]
}


def test_generate_sends_gender_constraint_and_archive_wins(client):
    """档案里有性别线索 → 提示词里当硬约束下发;模型说反了也按档案落库。"""
    headers = _auth(client, "gender_gen")
    pid = _project(client, headers, "性别漫剧书")
    _seed_female_entity(pid)

    adapter = _JsonAdapter(_CHARS_REPLY_WRONG_GENDER)
    with patch("app.engines.drama.characters.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/characters/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job

    prompt = adapter.prompts[0]
    assert "性别:女【硬约束" in prompt, prompt[:600]
    assert "「她」" in prompt  # 依据也写进去,模型才知道不是随口指定
    card = _card_of(pid, "林晚")
    assert card.gender == "female", "档案证据必须压过模型自己的判断"
    # 描述跟性别打架 → 列表里带上人话提示,用户一眼看见
    got = client.get(f"/api/projects/{pid}/drama/characters", headers=headers).json()
    one = next(c for c in got["cards"] if c["name"] == "林晚")
    assert one["gender"] == "female"
    assert "少年" in one["gender_conflict"] or "男性" in one["gender_conflict"]


def test_generate_without_clue_takes_model_judgement(client):
    """档案一点线索都没有 → 下发「未判明」,由模型判断并回填 gender。"""
    from app.db.models import Entity
    from app.db.session import SessionLocal

    headers = _auth(client, "gender_unknown")
    pid = _project(client, headers, "无线索漫剧书")
    with SessionLocal() as s:
        s.add(Entity(project_id=pid, entity_type="character", name="无名客",
                     aliases=[], base_profile={}))
        s.commit()

    adapter = _JsonAdapter({
        "cards": [{"name": "无名客", "gender": "female",
                   "appearance_cn": "二十出头的青年女性,杏眼圆脸,月白襦裙",
                   "appearance_en": "young woman, pale robe",
                   "outfit_cn": "月白襦裙", "voice_desc": "青年女声,清亮微冷"}]
    })
    with patch("app.engines.drama.characters.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/characters/generate", headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert "性别:未判明" in adapter.prompts[0]
    assert _card_of(pid, "无名客").gender == "female"
    # 这张卡描述与性别一致 → 不该报冲突
    assert gender_conflict_note(_card_of(pid, "无名客")) == ""


# =============== 用户拍板 + 单卡重出 ===============

_REGEN_REPLY = {
    "cards": [
        {
            "name": "林晚",
            "gender": "female",
            "appearance_cn": "二十出头的青年女性,杏眼圆脸,乌发松挽,月白襦裙银线绣缠枝",
            "appearance_en": "young woman, oval face, pale blue robe",
            "outfit_cn": "月白襦裙",
            "voice_desc": "青年女声,清亮微冷,语速偏快",
        }
    ]
}


def test_patch_gender_rejects_garbage(client):
    headers = _auth(client, "gender_patch")
    pid = _project(client, headers, "改性别漫剧书")
    _seed_female_entity(pid)
    adapter = _JsonAdapter(_CHARS_REPLY_WRONG_GENDER)
    with patch("app.engines.drama.characters.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/characters/generate", headers=headers)
        _wait_job(client, headers, r.json()["job_id"])
    cid = _card_of(pid, "林晚").id

    bad = client.patch(f"/api/projects/{pid}/drama/characters/{cid}",
                       headers=headers, json={"gender": "女神"})
    assert bad.status_code == 400, bad.text
    ok = client.patch(f"/api/projects/{pid}/drama/characters/{cid}",
                      headers=headers, json={"gender": "male"})
    assert ok.status_code == 200 and ok.json()["card"]["gender"] == "male"
    # 也能改回「未定」
    back = client.patch(f"/api/projects/{pid}/drama/characters/{cid}",
                        headers=headers, json={"gender": ""})
    assert back.json()["card"]["gender"] == ""


def test_regenerate_one_card_honors_pinned_gender(client):
    """用户把性别改成「女」→ 单卡重出:硬约束下发,连锁定的卡也照重。"""
    headers = _auth(client, "gender_regen")
    pid = _project(client, headers, "单卡重出漫剧书")
    _seed_female_entity(pid)
    with patch("app.engines.drama.characters.get_adapter_for",
               return_value=_JsonAdapter(_CHARS_REPLY_WRONG_GENDER)):
        r = client.post(f"/api/projects/{pid}/drama/characters/generate", headers=headers)
        _wait_job(client, headers, r.json()["job_id"])
    cid = _card_of(pid, "林晚").id
    # 用户拍板 + 锁定(显式重出应当覆盖锁定,这才符合「我就要改这一张」)
    client.patch(f"/api/projects/{pid}/drama/characters/{cid}",
                 headers=headers, json={"gender": "female", "locked": True})

    adapter = _JsonAdapter(_REGEN_REPLY)
    with patch("app.engines.drama.characters.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/characters/{cid}/regenerate",
                        headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert len(adapter.prompts) == 1, "单卡重出只该问一次"
    assert "性别:女【硬约束" in adapter.prompts[0]
    assert "卡上已定" in adapter.prompts[0]
    assert "林晚" in adapter.prompts[0] and "晚娘" in adapter.prompts[0]

    card = job["result"]["card"]
    assert card["gender"] == "female"
    assert "青年女性" in card["appearance_cn"]
    assert "young woman" in card["appearance_en"]
    assert card["voice_desc"].startswith("青年女声")
    assert card["gender_conflict"] == "", "重写完不该再有性别冲突"
    assert _card_of(pid, "林晚").locked is True, "重出不改锁定状态"


def test_regenerate_reports_truncated_output(client):
    """模型这次啥也没给(截断/空转)→ 报错说清是怎么坏的,不写坏原有的卡。"""
    headers = _auth(client, "gender_regen_fail")
    pid = _project(client, headers, "重出失败漫剧书")
    _seed_female_entity(pid)
    with patch("app.engines.drama.characters.get_adapter_for",
               return_value=_JsonAdapter(_CHARS_REPLY_WRONG_GENDER)):
        r = client.post(f"/api/projects/{pid}/drama/characters/generate", headers=headers)
        _wait_job(client, headers, r.json()["job_id"])
    cid = _card_of(pid, "林晚").id
    before = _card_of(pid, "林晚").appearance_cn

    class _Truncated:
        async def ask(self, prompt, system=None):
            return '{"cards": [{"name": "林晚", "appearance_cn": "二十出头的青'

    with patch("app.engines.drama.characters.get_adapter_for", return_value=_Truncated()):
        r = client.post(f"/api/projects/{pid}/drama/characters/{cid}/regenerate",
                        headers=headers)
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error", job
    assert "截断" in job["error"]
    assert _card_of(pid, "林晚").appearance_cn == before, "失败不该动原有描述"


def test_other_users_card_regenerate_404(client):
    headers_a = _auth(client, "gender_owner")
    headers_b = _auth(client, "gender_stranger")
    pid = _project(client, headers_a, "归属隔离漫剧书")
    _seed_female_entity(pid)
    with patch("app.engines.drama.characters.get_adapter_for",
               return_value=_JsonAdapter(_CHARS_REPLY_WRONG_GENDER)):
        r = client.post(f"/api/projects/{pid}/drama/characters/generate", headers=headers_a)
        _wait_job(client, headers_a, r.json()["job_id"])
    cid = _card_of(pid, "林晚").id
    r = client.post(f"/api/projects/{pid}/drama/characters/{cid}/regenerate",
                    headers=headers_b)
    assert r.status_code == 404, r.text


# =============== 定妆照 / 分镜:性别一路带下去 ===============

def test_ref_sheet_block_and_fallback_carry_gender():
    """定妆照:素材块显式给性别,引擎兜底拼的提示词中英文都带性别词。"""
    from app.db.models import DramaCharacterCard, DramaStyleCard
    from app.engines.drama.characters import _assemble_ref_prompt, _cards_block

    card = DramaCharacterCard(
        project_id=1, name="林晚", gender="female",
        appearance_cn="二十出头的青年女性,杏眼圆脸,月白襦裙",
        appearance_en="young woman, pale robe",
        outfit_cn="月白襦裙",
    )
    assert "性别:女" in _cards_block([card])

    style = DramaStyleCard(project_id=1, style_cn="国风厚涂,黛青主色",
                           style_en="ink-wash, dark teal")
    cn, en = _assemble_ref_prompt(card, style)
    assert "女性角色" in cn
    assert "female character" in en


def test_shot_anchor_fallback_carries_gender():
    """分镜兜底注入的角色锚带上「(女性)」——那是生图站唯一能看到的性别线索。"""
    from app.db.models import DramaCharacterCard, DramaShot
    from app.engines.drama.prompt_render import _ensure_character_anchors

    card = DramaCharacterCard(
        project_id=1, name="林晚", gender="female",
        appearance_cn="二十出头的青年女性,杏眼圆脸,月白襦裙",
    )
    shot = DramaShot(episode_id=1, seq=1, characters=["林晚"], action_desc="推门而入")
    out = _ensure_character_anchors(shot, "竖版9:16,雪夜山道,国风厚涂", {"林晚": card}, {})
    assert "林晚(女性)" in out
