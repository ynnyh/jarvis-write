# tests/test_drama_ref_sheet.py
# -*- coding: utf-8 -*-
"""漫剧定妆照(角色参考图)测试:提示词生成 + 图片上传/外链/删除/读取 + 粘贴版联动。

为什么要有定妆照:文字锚(锁定外貌段)只能把跨镜头漂移压小,压不到零——
同一段描述交给生图站,两次出图仍是两张脸。真正锁脸要靠「参考图 + 每格提示词」。

验证点:
- 出定妆照提示词:没风格卡 → 报错要先定画风;只补缺不覆盖;names 显式给了才强制重出
- 上传:按文件头判定(改扩展名无效)、张数上限、服务端生成文件名、读取走鉴权
- 外链:只收 http(s)
- 删除:连文件一起删,索引越界 404
- 粘贴版联动:有了定妆照,该角色出场的分镜粘贴版才出现「参考图」指令
- 归属隔离:别人的角色卡 → 404
"""
from __future__ import annotations

import json
import struct
import time
import zlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

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


class _RawAdapter:
    """按调用顺序吐预置原文的桩(用尽后重复最后一条),用来模拟截断/异形输出。"""

    def __init__(self, *raws: str):
        assert raws
        self._raws = list(raws)
        self.prompts: list[str] = []

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
        return self._raws.pop(0) if len(self._raws) > 1 else self._raws[0]


def _png() -> bytes:
    """真的 1x1 PNG(不是随手拼的字节):既过文件头校验,也确实是张图。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def _project(client: TestClient, headers: dict, title: str) -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_card(pid: int, with_style: bool = True) -> int:
    """种一张角色卡(可选带风格卡),返回卡 id。"""
    from app.db.models import DramaCharacterCard, DramaStyleCard
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        if with_style:
            s.add(
                DramaStyleCard(
                    project_id=pid,
                    style_name="水墨武侠",
                    style_cn="国风厚涂,黛青主色,侧逆光",
                    style_en="ink-wash wuxia, cinematic",
                    negative="文字,水印,五官错位",
                    ratio="9:16",
                )
            )
        card = DramaCharacterCard(
            project_id=pid,
            name="沈砚",
            appearance_cn="三十岁,剑眉薄唇,玄色劲装银线滚边",
            appearance_en="black outfit swordsman",
            outfit_cn="玄色劲装",
        )
        s.add(card)
        s.commit()
        return card.id


def _seed_extra(pid: int, name: str, appearance: str = "二十岁,杏眼圆脸,月白襦裙") -> int:
    """再种一张角色卡(appearance 传空串 = 没有锁定外貌段的卡)。"""
    from app.db.models import DramaCharacterCard
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        card = DramaCharacterCard(project_id=pid, name=name, appearance_cn=appearance)
        s.add(card)
        s.commit()
        return card.id


def _card_of(pid: int, name: str):
    from app.db.models import DramaCharacterCard
    from app.db.session import SessionLocal

    with SessionLocal() as s:
        return (
            s.query(DramaCharacterCard)
            .filter(DramaCharacterCard.project_id == pid, DramaCharacterCard.name == name)
            .one()
        )


_SHEET_REPLY = {
    "sheets": [
        {
            "name": "沈砚",
            "ref_prompt_cn": "单人正面半身居中,纯色浅灰背景,柔和均匀光;三十岁,"
                             "剑眉薄唇,玄色劲装银线滚边;国风厚涂,黛青主色,侧逆光",
            "ref_prompt_en": "character reference sheet, single person, front view, "
                             "upper body, plain background, black outfit swordsman, ink-wash wuxia",
        }
    ]
}


# =============== 定妆照提示词 ===============

def test_ref_prompts_need_style_card(client):
    """没定画风就出定妆照 → 报错点名去定画风(定妆照必须带画风锚才统一)。"""
    headers = _auth(client, "ref_nostyle")
    pid = _project(client, headers, "无画风漫剧书")
    _seed_card(pid, with_style=False)

    r = client.post(f"/api/projects/{pid}/drama/characters/ref-prompts",
                    headers=headers, json={})
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error", job
    assert "画风" in job["error"]


def test_ref_prompts_fill_missing_then_force(client):
    """只补缺:第二次点不再空跑 LLM(generated=0);names 显式给了才强制重出。"""
    headers = _auth(client, "ref_prompts")
    pid = _project(client, headers, "定妆照漫剧书")
    _seed_card(pid)

    adapter = _JsonAdapter(_SHEET_REPLY)
    with patch("app.engines.drama.characters.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/characters/ref-prompts",
                        headers=headers, json={})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["generated"] == 1
    card = job["result"]["cards"][0]
    assert "纯色浅灰背景" in card["ref_prompt_cn"]
    # 画风锚与外貌锚都进了提示词
    assert "国风厚涂" in adapter.prompts[0] and "玄色劲装" in adapter.prompts[0]
    # 序列化顺带给出按平台粘贴版(比例走定妆照专用 3:4,不是竖屏 9:16)
    assert card["ref_paste"]["oneframe"]["main"].startswith("单人正面半身")
    assert "3:4" in card["ref_paste"]["oneframe"]["main"]
    assert "五官错位" in card["ref_paste"]["oneframe"]["main"]  # 负面词并入正文
    assert card["ref_paste"]["oneframe"]["negative"] == ""

    # 已经有了 → 不再调 LLM(桩队列为空也不报错,说明真没调)
    r = client.post(f"/api/projects/{pid}/drama/characters/ref-prompts",
                    headers=headers, json={})
    job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["generated"] == 0

    # 点名重出 → 强制覆盖
    forced = _JsonAdapter({"sheets": [{"name": "沈砚", "ref_prompt_cn": "重出版本",
                                       "ref_prompt_en": "redone"}]})
    with patch("app.engines.drama.characters.get_adapter_for", return_value=forced):
        r = client.post(f"/api/projects/{pid}/drama/characters/ref-prompts",
                        headers=headers, json={"names": ["沈砚"]})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["generated"] == 1
    assert job["result"]["cards"][0]["ref_prompt_cn"] == "重出版本"


# ---- 可靠性:模型输出被截断 / 名字异形 / 干脆没给,都不许走进死胡同 ----

# 真实线上失败长这样:推理吃掉预算,JSON 停在半个字符串上(Unterminated string)
_TRUNCATED = (
    '{"sheets": [\n'
    '  {"name": "沈砚", "ref_prompt_cn": "单人正面半身居中,纯色浅灰背景,柔和均匀光;'
    '三十岁,剑眉薄唇,玄色劲装银线滚边;国风厚涂,黛青主色,侧逆光", '
    '"ref_prompt_en": "character reference sheet, single person, front view"},\n'
    '  {"name": "柳青", "ref_prompt_cn": "单人正面半身居中,纯色浅'
)


def test_ref_prompts_salvage_truncated_then_assemble(client):
    """输出被截断:写完的那条照样收下,没写完的由引擎按锚段拼一条,不整批白跑。"""
    headers = _auth(client, "ref_trunc")
    pid = _project(client, headers, "截断漫剧书")
    _seed_card(pid)
    _seed_extra(pid, "柳青")

    # 第二次调用(单条纠偏)也空手而归 → 逼出引擎兜底
    adapter = _RawAdapter(_TRUNCATED, "")
    with patch("app.engines.drama.characters.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/characters/ref-prompts",
                        headers=headers, json={})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"] == {**job["result"], "generated": 1, "assembled": 1}

    shen = _card_of(pid, "沈砚")
    assert "剑眉薄唇" in shen.ref_prompt_cn and shen.ref_prompt_en          # 模型那条
    liu = _card_of(pid, "柳青")
    assert "杏眼圆脸" in liu.ref_prompt_cn                                  # 外貌锚
    assert "国风厚涂" in liu.ref_prompt_cn                                  # 画风锚
    assert "单人正面半身" in liu.ref_prompt_cn                              # 构图规范
    assert liu.ref_prompt_en.startswith("character reference sheet")


def test_ref_prompts_tolerates_odd_names_and_keys(client):
    """名字带【】/(主角)、键名写成同义词、顶层是数组——一律照样落到卡上。"""
    headers = _auth(client, "ref_odd")
    pid = _project(client, headers, "异形漫剧书")
    _seed_card(pid)
    _seed_extra(pid, "柳青")

    raw = json.dumps(
        [
            {"角色": "【沈砚】", "prompt_cn": "单人正面半身,沈砚,国风厚涂"},
            {"name": "柳青(女主)", "中文": "单人正面半身,柳青,国风厚涂", "en": "liu qing ref"},
        ],
        ensure_ascii=False,
    )
    adapter = _RawAdapter(raw)
    with patch("app.engines.drama.characters.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/characters/ref-prompts",
                        headers=headers, json={})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["generated"] == 2 and job["result"]["assembled"] == 0
    assert len(adapter.prompts) == 1  # 两位一批,没触发纠偏重问
    assert "沈砚" in _card_of(pid, "沈砚").ref_prompt_cn
    assert _card_of(pid, "柳青").ref_prompt_en == "liu qing ref"


def test_ref_prompts_chunks_by_four(client):
    """超过 4 位分批调用:一次要 8 条正是当初被截断的原因,现在每批最多 4 条。"""
    headers = _auth(client, "ref_chunk")
    pid = _project(client, headers, "分批漫剧书")
    _seed_card(pid)
    for n in ("柳青", "陆行舟", "苏窈", "裴九"):
        _seed_extra(pid, n)

    reply = {
        "sheets": [
            {"name": n, "ref_prompt_cn": f"单人正面半身,{n},国风厚涂", "ref_prompt_en": f"{n} ref"}
            for n in ("沈砚", "柳青", "陆行舟", "苏窈", "裴九")
        ]
    }
    adapter = _RawAdapter(json.dumps(reply, ensure_ascii=False))
    with patch("app.engines.drama.characters.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/characters/ref-prompts",
                        headers=headers, json={})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["generated"] == 5 and job["result"]["assembled"] == 0
    assert len(adapter.prompts) == 2                      # 4 + 1
    assert "共 4 位" in adapter.prompts[0]
    assert "共 1 位" in adapter.prompts[1]
    # 每批只带本批的卡:第一批不许出现第五位的名字(串批 = 又变成一次 8 条)
    assert "裴九" not in adapter.prompts[0] and "裴九" in adapter.prompts[1]


def test_ref_prompts_error_points_at_real_cause(client):
    """一条也没成时报错要说清哪一环坏了,不再一律甩「角色名对不上」。"""
    headers = _auth(client, "ref_why")
    pid = _project(client, headers, "报错漫剧书")
    _seed_card(pid, with_style=True)
    # 没有锁定外貌段的卡:连兜底也拼不出来,报错要点名让用户先补外貌
    _seed_extra(pid, "无名氏", appearance="")

    adapter = _RawAdapter("")
    with patch("app.engines.drama.characters.get_adapter_for", return_value=adapter):
        r = client.post(f"/api/projects/{pid}/drama/characters/ref-prompts",
                        headers=headers, json={"names": ["无名氏"]})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "error", job
    assert "锁定外貌" in job["error"] and "无名氏" in job["error"]

    # 有外貌段但模型干脆没吐东西 → 引擎兜底顶上,不报错(按钮永不走进死胡同)
    silent = _RawAdapter("")
    with patch("app.engines.drama.characters.get_adapter_for", return_value=silent):
        r = client.post(f"/api/projects/{pid}/drama/characters/ref-prompts",
                        headers=headers, json={"names": ["沈砚"]})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["assembled"] == 1 and job["result"]["generated"] == 0
    assert len(silent.prompts) == 2  # 一批 + 一次单条纠偏重问,才认命兜底
    assert "剑眉薄唇" in _card_of(pid, "沈砚").ref_prompt_cn


def test_ref_prompts_single_card_takes_result_despite_wrong_name(client):
    """只要一张卡时,名字写跑了也照样收下——一条对一卡不可能配错人。

    (多条的情况刻意不按位置配:把甲的脸描述写到乙的卡上,用户看不出来,
    却会毁掉整片的人物一致性,那种错宁可让引擎兜底。)
    """
    headers = _auth(client, "ref_single")
    pid = _project(client, headers, "单卡漫剧书")
    _seed_card(pid)

    wrong = _RawAdapter(json.dumps(
        {"sheets": [{"name": "路人甲", "ref_prompt_cn": "单人正面半身,玄色劲装,国风厚涂"}]},
        ensure_ascii=False))
    with patch("app.engines.drama.characters.get_adapter_for", return_value=wrong):
        r = client.post(f"/api/projects/{pid}/drama/characters/ref-prompts",
                        headers=headers, json={"names": ["沈砚"]})
        job = _wait_job(client, headers, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["generated"] == 1 and job["result"]["assembled"] == 0
    assert _card_of(pid, "沈砚").ref_prompt_cn.endswith("国风厚涂")


# =============== 上传 / 外链 / 删除 / 读取 ===============

def test_reference_upload_read_delete(client):
    """上传→读取→删除全链路;文件名由服务端生成,读取走鉴权端点。"""
    headers = _auth(client, "ref_upload")
    pid = _project(client, headers, "上传漫剧书")
    cid = _seed_card(pid)
    base = f"/api/projects/{pid}/drama/characters/{cid}/reference"

    data = _png()
    r = client.post(base, headers=headers,
                    files={"file": ("../../evil.php", data, "image/png")},
                    data={"note": "正面"})
    assert r.status_code == 200, r.text
    imgs = r.json()["card"]["ref_images"]
    assert len(imgs) == 1 and imgs[0]["kind"] == "upload" and imgs[0]["note"] == "正面"
    # 用户给的文件名不参与路径:服务端按 项目/卡号-序号 生成
    assert imgs[0]["src"] == f"drama/{pid}/{cid}-1.png"

    r = client.get(f"{base}/0", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content == data

    # 没登录读不到(整个路由挂了鉴权依赖)
    assert client.get(f"{base}/0").status_code in (401, 403)

    r = client.delete(f"{base}/0", headers=headers)
    assert r.status_code == 200 and r.json()["card"]["ref_images"] == []
    # 文件真的删了
    from app import storage
    assert not (storage.upload_root() / f"drama/{pid}/{cid}-1.png").exists()
    assert client.get(f"{base}/0", headers=headers).status_code == 404
    assert client.delete(f"{base}/0", headers=headers).status_code == 404


def test_reference_rejects_non_image_and_over_limit(client):
    """按文件头判定(改扩展名无效);张数上限拦住第 4 张。"""
    headers = _auth(client, "ref_reject")
    pid = _project(client, headers, "拒绝漫剧书")
    cid = _seed_card(pid)
    base = f"/api/projects/{pid}/drama/characters/{cid}/reference"

    r = client.post(base, headers=headers,
                    files={"file": ("shell.png", b"<?php echo 1; ?>", "image/png")})
    assert r.status_code == 400 and "PNG" in r.json()["detail"]

    from app import storage
    for i in range(storage.MAX_REFS_PER_CARD):
        r = client.post(base, headers=headers, files={"file": (f"{i}.png", _png(), "image/png")})
        assert r.status_code == 200, r.text
    r = client.post(base, headers=headers, files={"file": ("x.png", _png(), "image/png")})
    assert r.status_code == 400 and "最多" in r.json()["detail"]


def test_reference_link_and_isolation(client):
    """外链只收 http(s);别人的角色卡一律 404(项目归属先校验)。"""
    headers = _auth(client, "ref_link")
    pid = _project(client, headers, "外链漫剧书")
    cid = _seed_card(pid)
    base = f"/api/projects/{pid}/drama/characters/{cid}/reference"

    r = client.post(f"{base}/link", headers=headers, json={"url": "javascript:alert(1)"})
    assert r.status_code == 400

    r = client.post(f"{base}/link", headers=headers,
                    json={"url": "https://img.example.com/a.png", "note": "站点原图"})
    assert r.status_code == 200
    imgs = r.json()["card"]["ref_images"]
    assert imgs == [{"kind": "url", "src": "https://img.example.com/a.png", "note": "站点原图"}]
    # 外链不是本地文件,读取端点不伺候
    assert client.get(f"{base}/0", headers=headers).status_code == 404

    other = _auth(client, "ref_stranger")
    assert client.get(f"{base}/0", headers=other).status_code == 404
    assert client.post(f"{base}/link", headers=other, json={"url": "https://x/y.png"}).status_code == 404


# =============== 与分镜粘贴版的联动 ===============

def test_shot_paste_mentions_reference_only_when_uploaded(client):
    """分镜粘贴版:该格角色**有定妆照**才写「参考图」指令,没有就不提。"""
    from app.db.models import DramaEpisode, DramaShot
    from app.db.session import SessionLocal

    headers = _auth(client, "ref_shot")
    pid = _project(client, headers, "联动漫剧书")
    cid = _seed_card(pid)
    with SessionLocal() as s:
        ep = DramaEpisode(project_id=pid, ep_index=1, title="雪夜", source_chapters=[1])
        s.add(ep)
        s.flush()
        s.add(
            DramaShot(
                episode_id=ep.id, seq=1, scene_name="荒山雪道", characters=["沈砚"],
                action_desc="拔刀", shot_type="近景", camera="推", duration_s=4,
                prompt_cn="沈砚拔刀,雪夜,国风厚涂", prompt_en="draw sword, snowy night",
                negative="文字,水印",
            )
        )
        s.commit()
        ep_id = ep.id

    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}", headers=headers)
    paste = r.json()["shots"][0]["paste"]
    assert "【参考图】" not in paste["oneframe"]["main"]
    assert "【不要出现】文字,水印" in paste["oneframe"]["main"]      # 负面并入正文
    assert paste["dualbox"]["negative"] == "文字,水印"              # 双框站分开粘
    assert paste["mj"]["main"].endswith("oversaturated")            # MJ 带 --no
    assert "--ar 9:16" in paste["mj"]["main"]

    client.post(f"/api/projects/{pid}/drama/characters/{cid}/reference", headers=headers,
                files={"file": ("a.png", _png(), "image/png")})
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}", headers=headers)
    main = r.json()["shots"][0]["paste"]["oneframe"]["main"]
    assert "【参考图】" in main and "沈砚" in main

    # 导出手册同一套规则:单框站整段版 + 定妆照小节
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}/export?format=md", headers=headers)
    md = r.text
    assert "① 单框站:整段粘这个" in md and "【参考图】" in md
    assert "定妆照" in md and "已有参考图 1 张" in md
    r = client.get(f"/api/projects/{pid}/drama/episodes/{ep_id}/export?format=csv", headers=headers)
    assert "paste_oneframe" in r.text.splitlines()[0]


# =============== 删项目要清干净 ===============

def test_delete_project_clears_drama_rows_and_files(client):
    """删项目:漫剧七张表 + 已上传的定妆照文件一起清掉,不留幽灵占配额。

    文件不清 = 每项目 80MB 配额被删掉的项目占着;库不清 = 表越滚越大,
    而且项目号复用时(理论上不会,但别赌)会串到别人的资产上。
    """
    from app import storage
    from app.db.models import (
        DramaCharacterCard, DramaEpisode, DramaProductionPack, DramaSceneCard,
        DramaShot, DramaStyleCard, DramaTrailer,
    )
    from app.db.session import SessionLocal

    headers = _auth(client, "ref_delete")
    pid = _project(client, headers, "待删漫剧书")
    cid = _seed_card(pid)
    with SessionLocal() as s:
        ep = DramaEpisode(project_id=pid, ep_index=1, title="雪夜", source_chapters=[1])
        s.add(ep)
        s.flush()
        s.add(DramaShot(episode_id=ep.id, seq=1, scene_name="雪道", characters=["沈砚"],
                        action_desc="拔刀", shot_type="近景", camera="推", duration_s=4))
        s.add(DramaProductionPack(episode_id=ep.id, pack={"mode": "dialogue"}))
        s.add(DramaSceneCard(project_id=pid, name="荒山雪道", appearance_cn="雪夜山道"))
        s.add(DramaTrailer(project_id=pid, target_s=45, title="雪夜", lines=[], shots=[]))
        s.commit()
        ep_id = ep.id

    r = client.post(f"/api/projects/{pid}/drama/characters/{cid}/reference", headers=headers,
                    files={"file": ("a.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    rel = r.json()["card"]["ref_images"][0]["src"]
    assert (storage.upload_root() / rel).exists()

    assert client.delete(f"/api/projects/{pid}", headers=headers).status_code == 200

    # 文件与目录都没了
    assert not (storage.upload_root() / rel).exists()
    assert not (storage.upload_root() / "drama" / str(pid)).exists()
    assert storage.project_usage_bytes(pid) == 0
    # 七张表一行不剩
    with SessionLocal() as s:
        for model in (DramaStyleCard, DramaCharacterCard, DramaSceneCard,
                      DramaEpisode, DramaTrailer):
            assert s.query(model).filter(model.project_id == pid).count() == 0, model
        for model in (DramaShot, DramaProductionPack):
            assert s.query(model).filter(model.episode_id == ep_id).count() == 0, model
