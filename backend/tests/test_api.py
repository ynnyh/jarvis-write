# tests/test_api.py
# -*- coding: utf-8 -*-
"""接口级测试(TestClient + 临时库):注册边界与任务归属隔离。"""
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    # 进入上下文才会跑 lifespan(建表 + 幂等迁移),全程在临时库上
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str, password: str = "pass123") -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_register_rejects_overlong_password(client):
    """bcrypt 只取前 72 字节,超长密码应返回 400 而非 500。"""
    r = client.post(
        "/api/auth/register",
        json={"username": "long_pw_user", "password": "a" * 73, "invite_code": INVITE},
    )
    assert r.status_code == 400
    assert "密码过长" in r.json()["detail"]


def test_register_wrong_invite_code(client):
    r = client.post(
        "/api/auth/register",
        json={"username": "bad_invite", "password": "pass123", "invite_code": "nope"},
    )
    assert r.status_code == 403


def test_job_ownership_isolation(client):
    """别人的 job 按"不存在"处理(404),不泄露存在性;本人可查。"""
    from app.auth import current_user_id
    from app.jobs import create_job

    a = _register(client, "job_owner_a")
    b = _register(client, "job_owner_b")
    me_a = client.get("/api/auth/me", headers=_auth(a["token"])).json()

    # 以 A 的身份建任务(create_job 同步读取 contextvar)
    token_ctx = current_user_id.set(me_a["id"])
    try:
        job_id = create_job("test-kind")
    finally:
        current_user_id.reset(token_ctx)

    r = client.get(f"/api/jobs/{job_id}", headers=_auth(b["token"]))
    assert r.status_code == 404

    r = client.get(f"/api/jobs/{job_id}", headers=_auth(a["token"]))
    assert r.status_code == 200
    assert r.json()["kind"] == "test-kind"


def test_unauthenticated_401(client):
    assert client.get("/api/projects").status_code == 401


# ---------- 项目重命名 / 删除 ----------


def _create_project(client: TestClient, headers: dict, title: str = "测试书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def test_rename_project_ok(client):
    headers = _auth(_register(client, "rename_owner")["token"])
    p = _create_project(client, headers, "旧书名")

    r = client.patch(f"/api/projects/{p['id']}", headers=headers, json={"title": "新书名"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "新书名"

    r = client.get(f"/api/projects/{p['id']}", headers=headers)
    assert r.json()["title"] == "新书名"


def test_rename_project_rejects_bad_title(client):
    headers = _auth(_register(client, "rename_bad")["token"])
    p = _create_project(client, headers)

    assert client.patch(
        f"/api/projects/{p['id']}", headers=headers, json={"title": "   "}
    ).status_code == 400
    assert client.patch(
        f"/api/projects/{p['id']}", headers=headers, json={"title": "长" * 101}
    ).status_code == 400


def test_rename_project_not_owner_404(client):
    """非 owner 改他人项目标题 → 404(不泄露存在性)。"""
    a = _auth(_register(client, "rename_a")["token"])
    b = _auth(_register(client, "rename_b")["token"])
    p = _create_project(client, a)

    r = client.patch(f"/api/projects/{p['id']}", headers=b, json={"title": "抢书名"})
    assert r.status_code == 404


def test_delete_project_cascades(client):
    """删除后项目、大纲、章节、摘要、事实库全部查不到。"""
    from app.db.models import Chapter, ChapterSummary, Entity, Outline, Project
    from app.db.session import SessionLocal

    headers = _auth(_register(client, "del_owner")["token"])
    p = _create_project(client, headers, "要删的书")

    # 直接落库一些关联数据(走 API 生成太慢且依赖 LLM)
    db = SessionLocal()
    try:
        outline = Outline(project_id=p["id"], chapter_number=1, title="第一章")
        db.add(outline)
        db.flush()
        db.add(Chapter(project_id=p["id"], outline_id=outline.id, chapter_number=1,
                       final_content="正文", status="approved"))
        db.add(ChapterSummary(project_id=p["id"], chapter_number=1, rolling_summary="摘要"))
        db.add(Entity(project_id=p["id"], entity_type="character", name="张三"))
        db.commit()
    finally:
        db.close()

    r = client.delete(f"/api/projects/{p['id']}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted_chapters"] == 1

    # 接口层:项目 404
    assert client.get(f"/api/projects/{p['id']}", headers=headers).status_code == 404
    # 数据库层:关联数据全部清空
    db = SessionLocal()
    try:
        assert db.get(Project, p["id"]) is None
        for model in (Chapter, ChapterSummary, Entity, Outline):
            assert db.query(model).filter_by(project_id=p["id"]).count() == 0
    finally:
        db.close()


def test_delete_project_not_owner_404(client):
    """非 owner 删他人项目 → 404,且数据不受影响。"""
    a = _auth(_register(client, "del_a")["token"])
    b = _auth(_register(client, "del_b")["token"])
    p = _create_project(client, a, "别删我")

    r = client.delete(f"/api/projects/{p['id']}", headers=b)
    assert r.status_code == 404

    assert client.get(f"/api/projects/{p['id']}", headers=a).status_code == 200


def _setup_user_with_key(client, username: str) -> dict:
    """注册并存一套 deepseek 假配置(走数据库,不碰 .env)。"""
    user = _register(client, username)
    headers = _auth(user["token"])
    r = client.post(
        "/api/settings/providers",
        headers=headers,
        json={
            "interface_format": "deepseek",
            "name": "DeepSeek",
            "api_key": "sk-test",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
        },
    )
    assert r.status_code == 200, r.text
    return headers


# ---------- AI 起名 ----------

class _TitleAdapter:
    """假适配器:返回带序号/书名号/空行的混乱输出,验证解析兜底。"""

    def __init__(self, text: str):
        self._text = text

    async def ask(self, prompt, system=None):
        return self._text


def test_title_suggestion_ok(client):
    """AI 起名:返回去序号/去书名号后的候选书名(最多 5 个)。"""
    from unittest.mock import patch

    headers = _setup_user_with_key(client, "title_user")
    with patch(
        "app.api.projects.create_llm_adapter",
        return_value=_TitleAdapter("1. 霓虹深渊\n2. 《芯片猎人》\n- 深渊之下\n\n"),
    ):
        r = client.post(
            "/api/projects/title-suggestion",
            headers=headers,
            json={"topic": "义体维修师捡到罪证芯片", "genre": "赛博朋克"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["titles"] == ["霓虹深渊", "芯片猎人", "深渊之下"]


def test_title_suggestion_requires_key(client):
    """未配置任何 key 时返回 400,并提示去设置页。"""
    headers = _auth(_register(client, "title_nokey")["token"])
    r = client.post(
        "/api/projects/title-suggestion",
        headers=headers,
        json={"topic": "随便", "genre": ""},
    )
    assert r.status_code == 400
    assert "尚未配置模型" in r.json()["detail"]


def test_title_suggestion_llm_failure(client):
    """LLM 调用抛错 → 502,带原因;返回空 → 502 提示重试。"""
    from unittest.mock import patch

    headers = _setup_user_with_key(client, "title_fail_user")

    class _BoomAdapter:
        async def ask(self, prompt, system=None):
            raise RuntimeError("connection refused")

    with patch("app.api.projects.create_llm_adapter", return_value=_BoomAdapter()):
        r = client.post(
            "/api/projects/title-suggestion",
            headers=headers,
            json={"topic": "t", "genre": ""},
        )
    assert r.status_code == 502
    assert "connection refused" in r.json()["detail"]

    with patch(
        "app.api.projects.create_llm_adapter", return_value=_TitleAdapter("\n  \n")
    ):
        r = client.post(
            "/api/projects/title-suggestion",
            headers=headers,
            json={"topic": "t", "genre": ""},
        )
    assert r.status_code == 502
    assert "没有返回可用书名" in r.json()["detail"]


# ---------- provider 配置状态(前端引导横幅用) ----------


def test_provider_status_endpoint(client):
    """未配置 key → configured=false;配置任一家(DB)后 → true。"""
    headers = _auth(_register(client, "status_user")["token"])
    r = client.get("/api/settings/providers/status", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is False

    headers2 = _setup_user_with_key(client, "status_user2")
    r = client.get("/api/settings/providers/status", headers=headers2)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True
    assert body["providers"]["deepseek"] is True

    # 未登录 → 401
    assert client.get("/api/settings/providers/status").status_code == 401


# ---------- 默认 provider 智能回落 / 空 key 清晰报错 ----------


def _with_uid(client: TestClient, headers: dict, fn):
    """在指定用户的 contextvar 下调用工厂函数(模拟请求上下文)。"""
    from app.auth import current_user_id

    me = client.get("/api/auth/me", headers=headers).json()
    tok = current_user_id.set(me["id"])
    try:
        return fn()
    finally:
        current_user_id.reset(tok)


def test_default_provider_falls_back_to_only_configured(client):
    """只配了 openai(非默认)→ 回落到 openai,而不是死用 .env 的 deepseek。"""
    from app.llm.factory import resolve_default_provider

    headers = _auth(_register(client, "fb_openai_only")["token"])
    r = client.post(
        "/api/settings/providers",
        headers=headers,
        json={"interface_format": "openai", "api_key": "sk-openai"},
    )
    assert r.status_code == 200, r.text

    assert _with_uid(client, headers, resolve_default_provider) == "openai"


def test_default_provider_prefers_db_default(client):
    """DB 标了 is_default 的优先于回落:openai 有 key,deepseek 标默认 → deepseek。"""
    from app.llm.factory import resolve_default_provider

    headers = _auth(_register(client, "fb_db_default")["token"])
    r = client.post(
        "/api/settings/providers",
        headers=headers,
        json={"interface_format": "openai", "api_key": "sk-openai"},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/settings/providers",
        headers=headers,
        json={
            "interface_format": "deepseek",
            "api_key": "sk-deep",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "is_default": True,
        },
    )
    assert r.status_code == 200, r.text

    assert _with_uid(client, headers, resolve_default_provider) == "deepseek"


def test_generate_without_any_key_returns_400(client):
    """完全没配 key 调生成类接口 → 400 清晰文案,不再是 500 LocalProtocolError。"""
    headers = _auth(_register(client, "nokey_gen")["token"])
    p = _create_project(client, headers)

    r = client.post(
        f"/api/projects/{p['id']}/architecture", headers=headers, json={"tendency": {}}
    )
    assert r.status_code == 400, r.text
    assert "API key" in r.json()["detail"]
    assert "模型设置" in r.json()["detail"]


def test_create_adapter_empty_key_raises_400(client):
    """工厂层兜底:空 key(含纯空白)直接抛 HTTPException(400)。"""
    from fastapi import HTTPException as FastAPIHTTPException

    from app.llm.factory import create_llm_adapter

    headers = _auth(_register(client, "nokey_factory")["token"])

    def _build():
        return create_llm_adapter("deepseek")

    try:
        _with_uid(client, headers, _build)
    except FastAPIHTTPException as exc:
        assert exc.status_code == 400
        assert "deepseek" in exc.detail
    else:
        raise AssertionError("空 key 应抛 HTTPException(400)")


# ---------- 模型接入大类扩展(cc-switch:三大类 + 两别名) ----------


def _add_provider(client: TestClient, headers: dict, **fields) -> dict:
    """走 API 存一套配置,返回响应体。默认给全必填字段,可用 kw 覆盖。"""
    body = {"api_key": "sk-test", "base_url": "", "model": "m", **fields}
    r = client.post("/api/settings/providers", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_provider_status_covers_all_protocols(client):
    """status.providers 覆盖三大类 + 两别名共 5 键(前端据此渲染协议清单)。"""
    headers = _auth(_register(client, "proto_all")["token"])
    r = client.get("/api/settings/providers/status", headers=headers)
    assert r.status_code == 200, r.text
    assert set(r.json()["providers"]) == {
        "openai-compatible", "anthropic", "gemini", "deepseek", "openai",
    }


def test_anthropic_native_config_resolves_and_builds(client):
    """anthropic 原生协议:能存、被选默认、造出 AnthropicAdapter。"""
    from app.llm.anthropic import AnthropicAdapter
    from app.llm.factory import create_llm_adapter, resolve_default_provider

    headers = _auth(_register(client, "anthropic_native")["token"])
    _add_provider(
        client, headers,
        interface_format="anthropic", name="Claude",
        api_key="sk-ant", base_url="https://api.anthropic.com",
        model="claude-sonnet-4-20250514",
    )
    assert client.get(
        "/api/settings/providers/status", headers=headers
    ).json()["providers"]["anthropic"] is True

    assert _with_uid(client, headers, resolve_default_provider) == "anthropic"
    adapter = _with_uid(client, headers, lambda: create_llm_adapter("anthropic"))
    assert isinstance(adapter, AnthropicAdapter)
    assert adapter.interface_format == "anthropic"
    assert adapter.api_key == "sk-ant"


def test_openai_compatible_config_resolves_and_builds(client):
    """通用卡 openai-compatible:能存、被选默认、造出 OpenAICompatibleAdapter 本尊
    (而非 deepseek/openai 子类)。"""
    from app.llm.factory import create_llm_adapter, resolve_default_provider
    from app.llm.openai_compatible import OpenAICompatibleAdapter

    headers = _auth(_register(client, "oai_compat")["token"])
    _add_provider(
        client, headers,
        interface_format="openai-compatible", name="中转站A",
        api_key="sk-relay", base_url="https://relay.example.com/v1",
        model="gpt-4o-mini",
    )
    assert client.get(
        "/api/settings/providers/status", headers=headers
    ).json()["providers"]["openai-compatible"] is True

    assert _with_uid(
        client, headers, resolve_default_provider
    ) == "openai-compatible"
    adapter = _with_uid(
        client, headers, lambda: create_llm_adapter("openai-compatible")
    )
    assert type(adapter) is OpenAICompatibleAdapter
    assert adapter.base_url == "https://relay.example.com/v1"


def test_reject_unknown_interface_format(client):
    """未知协议名保存被拒(设置页白名单校验)→ 400 未知协议。"""
    headers = _auth(_register(client, "bad_fmt")["token"])
    r = client.post(
        "/api/settings/providers",
        headers=headers,
        json={"interface_format": "grok-native", "api_key": "sk-x"},
    )
    assert r.status_code == 400
    assert "未知协议" in r.json()["detail"]


# ---------- 后台管理(阶段 9) ----------


def _admin_auth(client: TestClient) -> dict:
    """初始管理员由启动迁移创建(用户名/密码取配置默认值)。"""
    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin12345"}
    )
    assert r.status_code == 200, r.text
    return _auth(r.json()["token"])


def test_admin_users_requires_admin(client):
    """普通用户/未登录访问后台 → 403/401。"""
    headers = _auth(_register(client, "not_admin")["token"])
    assert client.get("/api/admin/users", headers=headers).status_code == 403
    assert client.get("/api/admin/users").status_code == 401


def test_admin_lists_users_with_stats(client):
    """用户列表含项目数与用量汇总;admin 本身在列。"""
    headers = _auth(_register(client, "stats_user")["token"])
    _create_project(client, headers, "统计用书")

    r = client.get("/api/admin/users", headers=_admin_auth(client))
    assert r.status_code == 200, r.text
    users = {u["username"]: u for u in r.json()}
    assert "admin" in users
    row = users["stats_user"]
    assert row["project_count"] == 1
    assert row["is_active"] is True
    assert row["is_admin"] is False
    assert row["total_calls"] == 0
    assert row["created_at"]


def test_disabled_user_login_and_token_blocked(client):
    """禁用后:登录 403 带提示,旧 token 立即失效;启用后恢复。"""
    user = _register(client, "disable_me")
    headers = _auth(user["token"])
    admin = _admin_auth(client)
    uid = _me_id(client, headers)

    r = client.patch(
        f"/api/admin/users/{uid}", headers=admin, json={"is_active": False}
    )
    assert r.status_code == 200, r.text

    # 旧 token 失效
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    # 登录被拒
    r = client.post(
        "/api/auth/login",
        json={"username": "disable_me", "password": "pass123"},
    )
    assert r.status_code == 403
    assert "已被禁用" in r.json()["detail"]

    # 启用后恢复登录
    assert client.patch(
        f"/api/admin/users/{uid}", headers=admin, json={"is_active": True}
    ).status_code == 200
    r = client.post(
        "/api/auth/login",
        json={"username": "disable_me", "password": "pass123"},
    )
    assert r.status_code == 200, r.text


def _me_id(client: TestClient, headers: dict) -> int:
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _me_id_via_admin(client: TestClient, admin_headers: dict, username: str) -> int:
    r = client.get("/api/admin/users", headers=admin_headers)
    assert r.status_code == 200, r.text
    return next(u["id"] for u in r.json() if u["username"] == username)


def test_admin_reset_password(client):
    """重置后旧密码登录失败,新密码可登录。"""
    user = _register(client, "reset_me")
    admin = _admin_auth(client)
    uid = _me_id_via_admin(client, admin, "reset_me")

    r = client.post(
        f"/api/admin/users/{uid}/reset-password",
        headers=admin,
        json={"password": "newpass456"},
    )
    assert r.status_code == 200, r.text

    assert client.post(
        "/api/auth/login", json={"username": "reset_me", "password": "pass123"}
    ).status_code == 401
    r = client.post(
        "/api/auth/login", json={"username": "reset_me", "password": "newpass456"}
    )
    assert r.status_code == 200, r.text
    assert user["username"] == r.json()["username"]


def test_admin_delete_user_cascades(client):
    """删用户后:其项目及关联数据清空,账号本身消失。"""
    from app.db.models import Outline, Project, User
    from app.db.session import SessionLocal

    headers = _auth(_register(client, "delete_me")["token"])
    p = _create_project(client, headers, "随主而逝")
    db = SessionLocal()
    try:
        db.add(Outline(project_id=p["id"], chapter_number=1, title="第一章"))
        db.commit()
    finally:
        db.close()

    admin = _admin_auth(client)
    uid = _me_id_via_admin(client, admin, "delete_me")
    r = client.delete(f"/api/admin/users/{uid}", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["deleted_projects"] == 1

    db = SessionLocal()
    try:
        assert db.get(User, uid) is None
        assert db.get(Project, p["id"]) is None
        assert db.query(Outline).filter_by(project_id=p["id"]).count() == 0
    finally:
        db.close()


def test_admin_cannot_disable_or_delete_self(client):
    admin = _admin_auth(client)
    uid = _me_id_via_admin(client, admin, "admin")

    r = client.patch(
        f"/api/admin/users/{uid}", headers=admin, json={"is_active": False}
    )
    assert r.status_code == 400
    r = client.delete(f"/api/admin/users/{uid}", headers=admin)
    assert r.status_code == 400


# ---------- 多邀请码体系 ----------

def _clear_invite_codes() -> None:
    """清空 invite_codes 表,回到"表为空 → 回落旧单码"的状态。"""
    from app.db.models import InviteCode
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.query(InviteCode).delete()
        db.commit()
    finally:
        db.close()


def _register_with(client: TestClient, username: str, code: str):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": code},
    )


def test_invite_fallback_when_table_empty(client):
    """invite_codes 表为空时,回落旧单码逻辑(app_settings 优先于 .env)。"""
    from app.db.models import AppSetting
    from app.db.session import SessionLocal

    _clear_invite_codes()
    admin = _admin_auth(client)

    # 列表为空,legacy_fallback 给出当前生效的旧单码(来自 .env)
    r = client.get("/api/admin/invite-codes", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["legacy_fallback"] == {"code": INVITE, "source": "env"}

    # app_settings 设置后,DB 值生效,env 值不再可用
    db = SessionLocal()
    try:
        db.add(AppSetting(key="invite_code", value="db-code"))
        db.commit()
    finally:
        db.close()
    assert _register_with(client, "fb_old", INVITE).status_code == 403
    r = _register_with(client, "fb_new", "db-code")
    assert r.status_code == 200, r.text

    # 置空 = 关闭注册
    db = SessionLocal()
    try:
        row = db.get(AppSetting, "invite_code")
        row.value = ""
        db.commit()
    finally:
        db.close()
    r = _register_with(client, "fb_closed", "db-code")
    assert r.status_code == 403
    assert "未开放注册" in r.json()["detail"]

    # 删掉 DB 记录,恢复 env 生效,避免影响后续用例
    db = SessionLocal()
    try:
        db.delete(db.get(AppSetting, "invite_code"))
        db.commit()
    finally:
        db.close()
    assert _register_with(client, "fb_env", INVITE).status_code == 200


def test_invite_codes_multi_flow(client):
    """多邀请码:CRUD、限次、停用、删除、used_count 递增;结束后清表。"""
    admin = _admin_auth(client)
    _clear_invite_codes()

    # 新建:长期码 + 限 2 次码
    r = client.post(
        "/api/admin/invite-codes",
        headers=admin,
        json={"code": "LONG-2026", "note": "长期合作方"},
    )
    assert r.status_code == 200, r.text
    long_id = r.json()["id"]
    assert r.json()["max_uses"] is None
    r = client.post(
        "/api/admin/invite-codes",
        headers=admin,
        json={"code": "TWO-ONLY", "max_uses": 2},
    )
    assert r.status_code == 200, r.text
    two_id = r.json()["id"]

    # 重复 code → 400;格式非法 → 422;max_uses < 1 → 422
    assert client.post(
        "/api/admin/invite-codes", headers=admin, json={"code": "LONG-2026"}
    ).status_code == 400
    assert client.post(
        "/api/admin/invite-codes", headers=admin, json={"code": "bad code!"}
    ).status_code == 422
    assert client.post(
        "/api/admin/invite-codes",
        headers=admin,
        json={"code": "ZERO-USE", "max_uses": 0},
    ).status_code == 422

    # 表有记录后:legacy_fallback 为 null,旧单码(env)不再生效
    body = client.get("/api/admin/invite-codes", headers=admin).json()
    assert len(body["items"]) == 2
    assert body["legacy_fallback"] is None
    assert _register_with(client, "mc_env", INVITE).status_code == 403

    # 长期码可多次注册,used_count 递增
    assert _register_with(client, "mc_a", "LONG-2026").status_code == 200
    assert _register_with(client, "mc_b", "LONG-2026").status_code == 200
    items = client.get("/api/admin/invite-codes", headers=admin).json()["items"]
    long_item = next(i for i in items if i["id"] == long_id)
    assert long_item["used_count"] == 2
    assert long_item["note"] == "长期合作方"

    # 限次码:用完即失效,第 3 次注册被拒
    assert _register_with(client, "mc_c", "TWO-ONLY").status_code == 200
    assert _register_with(client, "mc_d", "TWO-ONLY").status_code == 200
    r = _register_with(client, "mc_e", "TWO-ONLY")
    assert r.status_code == 403
    assert "无效或已失效" in r.json()["detail"]

    # 停用 → 403;启用后恢复
    assert client.patch(
        f"/api/admin/invite-codes/{long_id}",
        headers=admin,
        json={"is_active": False},
    ).status_code == 200
    assert _register_with(client, "mc_f", "LONG-2026").status_code == 403
    assert client.patch(
        f"/api/admin/invite-codes/{long_id}",
        headers=admin,
        json={"is_active": True},
    ).status_code == 200
    assert _register_with(client, "mc_g", "LONG-2026").status_code == 200

    # 删除 → 403(表里还有别的码,不会回落);重复删除 → 404
    assert client.delete(
        f"/api/admin/invite-codes/{two_id}", headers=admin
    ).status_code == 200
    assert _register_with(client, "mc_h", "TWO-ONLY").status_code == 403
    assert client.delete(
        f"/api/admin/invite-codes/{two_id}", headers=admin
    ).status_code == 404

    # 收尾:清空表,恢复"回落旧单码"状态,不影响其他用例
    assert client.delete(
        f"/api/admin/invite-codes/{long_id}", headers=admin
    ).status_code == 200


def test_invite_codes_require_admin(client):
    """邀请码管理接口仅管理员可用。"""
    user = _auth(_register(client, "not_admin_ic")["token"])
    assert client.get("/api/admin/invite-codes", headers=user).status_code == 403
    assert client.post(
        "/api/admin/invite-codes", headers=user, json={"code": "HACK-123"}
    ).status_code == 403
    assert client.get("/api/admin/invite-codes").status_code == 401


# ---------- 阅读中片段润色(polish-fragment) ----------


def _create_chapter_with_content(client: TestClient, headers: dict, title: str = "润色书") -> dict:
    """建项目 + 直接落库一章带定稿正文(走 API 生成依赖 LLM,太慢)。"""
    from app.db.models import Chapter, Outline
    from app.db.session import SessionLocal

    p = _create_project(client, headers, title)
    db = SessionLocal()
    try:
        outline = Outline(project_id=p["id"], chapter_number=1, title="第一章", summary="主角进城")
        db.add(outline)
        db.flush()
        db.add(Chapter(project_id=p["id"], outline_id=outline.id, chapter_number=1,
                       final_content="他走进了城门。", status="approved"))
        db.commit()
    finally:
        db.close()
    return p


class _FragmentAdapter:
    """假适配器:直接返回"润色后"文本。"""

    async def ask(self, prompt, system=None):
        return "他迈步走进了高大的城门。"


def test_polish_fragment_ok(client):
    """mock LLM:片段润色返回 polished;prompt 注入蓝图摘要与润色方向。"""
    from unittest.mock import patch

    headers = _auth(_register(client, "frag_user")["token"])
    p = _create_chapter_with_content(client, headers)

    captured: dict = {}

    class _CaptureAdapter:
        async def ask(self, prompt, system=None):
            captured["prompt"] = prompt
            return "他迈步走进了高大的城门。"

    with patch("app.engines.polish.polisher.get_adapter_for", return_value=_CaptureAdapter()):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/polish-fragment",
            headers=headers,
            json={"fragment": "他走进了城门。", "direction": "更紧张一些"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["polished"] == "他迈步走进了高大的城门。"
    # 上下文与方向确实注入 prompt(防跑题约束)
    assert "主角进城" in captured["prompt"]
    assert "更紧张一些" in captured["prompt"]
    assert "不得改变" in captured["prompt"]


def test_polish_fragment_empty_400(client):
    """空片段(含纯空白)→ 400;章节不存在 → 404。"""
    headers = _auth(_register(client, "frag_empty")["token"])
    p = _create_chapter_with_content(client, headers, "空片段书")

    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/polish-fragment",
        headers=headers,
        json={"fragment": "   ", "direction": ""},
    )
    assert r.status_code == 400

    r = client.post(
        f"/api/projects/{p['id']}/chapters/99/polish-fragment",
        headers=headers,
        json={"fragment": "一段正文", "direction": ""},
    )
    assert r.status_code == 404


def test_polish_chapter_threads_directive_into_prompt(client):
    """③档整章优化:请求带 directive → 蒸馏出的修改意见透传进润色 prompt。

    回归 docs/10 ③档「意见清单不落地」缺陷:AiDock 承诺「③④ 都会高优先级遵循」,
    但旧签名/端点丢 directive、只做标准去 AI 味。此测锁定 directive 经
    ChapterPolishRequest → polish_text → POLISH_PROMPT 的【用户修改要求】块全程不断线。
    """
    from unittest.mock import patch

    headers = _auth(_register(client, "polish_dir")["token"])
    p = _create_chapter_with_content(client, headers, "③档意见书")

    prompts: list[str] = []

    class _CaptureAdapter:
        # polish_text 三次调 LLM(抽事实/润色/校验),按 prompt 内容分发(仿 _LoopAdapter)
        async def ask(self, prompt, system=None):
            prompts.append(prompt)
            if "抽取" in prompt and "事实" in prompt:
                return '{"facts": ["他走进了城门"]}'
            if "逐条检查" in prompt:
                return '{"violations": []}'
            return "【润色稿】他迈步走进了高大的城门。"

    directive = "开头环境描写压缩一半,直接进冲突"
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=_CaptureAdapter()):
        r = client.post(
            f"/api/projects/{p['id']}/polish/chapter/1",
            headers=headers,
            json={"tendency": {}, "directive": directive},
        )
    assert r.status_code == 200, r.text
    # directive 落进润色 prompt(那条含"待润色文本"的),且带【用户修改要求】优先级标记
    polish_prompt = next(pr for pr in prompts if "待润色文本" in pr)
    assert "【用户修改要求" in polish_prompt
    assert directive in polish_prompt


def test_polish_fragment_not_owner_404(client):
    """非 owner 润色他人项目片段 → 404(不泄露存在性)。"""
    a = _auth(_register(client, "frag_a")["token"])
    b = _auth(_register(client, "frag_b")["token"])
    p = _create_chapter_with_content(client, a, "别人的书")

    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/polish-fragment",
        headers=b,
        json={"fragment": "他走进了城门。", "direction": "更生动"},
    )
    assert r.status_code == 404


# ---------- 阅读中段落对话(discuss) ----------


class _DiscussAdapter:
    """假适配器:按需返回固定文本;记录最后一次 complete 收到的 messages。

    discuss 走多轮 complete(不是 ask),需带 max_tokens / _record_usage /
    complete 三样,对齐 base.LLMAdapter 的真实调用面。
    """

    def __init__(self, reply: str):
        self._reply = reply
        self.max_tokens = 8192
        self.captured: list = []

    def _record_usage(self, resp):  # noqa: ANN001 — 记账在测试里是 no-op
        pass

    async def complete(self, messages):
        self.captured = messages
        return type("R", (), {
            "content": self._reply, "model": "fake",
            "prompt_tokens": 1, "completion_tokens": 1,
        })()


def test_discuss_explains_without_suggestion(client):
    """纯提问:AI 只解释,无【改写建议】→ suggestion 为 null;上下文注入 system。"""
    from unittest.mock import patch

    headers = _auth(_register(client, "disc_ask")["token"])
    p = _create_chapter_with_content(client, headers, "对话书")

    adapter = _DiscussAdapter("这段是说主角终于抵达了城门口,情绪是忐忑的。")
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/discuss",
            headers=headers,
            json={
                "messages": [{"role": "user", "content": "这段是什么意思?"}],
                "target": "他走进了城门。",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "主角" in body["reply"]
    assert body["suggestion"] is None
    # system 里注入了本章梗概与选中段落
    system = adapter.captured[0].content
    assert "主角进城" in system
    assert "他走进了城门。" in system


def test_discuss_returns_suggestion(client):
    """要求改写:回复带【改写建议】标记 → 拆出 suggestion,reply 留说明部分。"""
    from unittest.mock import patch

    headers = _auth(_register(client, "disc_fix")["token"])
    p = _create_chapter_with_content(client, headers, "改写书")

    reply = "我把它写得更紧张一些。\n【改写建议】\n他屏住呼吸,一步跨进了城门。"
    adapter = _DiscussAdapter(reply)
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/discuss",
            headers=headers,
            json={
                "messages": [{"role": "user", "content": "帮我改得紧张点"}],
                "target": "他走进了城门。",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggestion"] == "他屏住呼吸,一步跨进了城门。"
    assert "更紧张" in body["reply"]
    assert "【改写建议】" not in body["reply"]


def test_discuss_empty_messages_400(client):
    """空对话 → 400;章节不存在 → 404。"""
    headers = _auth(_register(client, "disc_empty")["token"])
    p = _create_chapter_with_content(client, headers, "空对话书")

    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/discuss",
        headers=headers,
        json={"messages": [], "target": "他走进了城门。"},
    )
    assert r.status_code == 400

    r = client.post(
        f"/api/projects/{p['id']}/chapters/99/discuss",
        headers=headers,
        json={"messages": [{"role": "user", "content": "在?"}], "target": "x"},
    )
    assert r.status_code == 404


def test_discuss_not_owner_404(client):
    """非 owner 就他人项目段落对话 → 404(不泄露存在性)。"""
    a = _auth(_register(client, "disc_a")["token"])
    b = _auth(_register(client, "disc_b")["token"])
    p = _create_chapter_with_content(client, a, "别人的对话书")

    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/discuss",
        headers=b,
        json={"messages": [{"role": "user", "content": "这啥意思"}], "target": "他走进了城门。"},
    )
    assert r.status_code == 404


def test_discuss_chapter_freeqa_without_target(client):
    """target 置空/缺省 = 无选段的整章自由问答(AI 窄栏「随便问」):
    注入整章正文与梗概,只答不改(不给 suggestion),prompt 不含选段锚点块。"""
    from unittest.mock import patch

    headers = _auth(_register(client, "disc_free")["token"])
    p = _create_chapter_with_content(client, headers, "自由问答书")

    adapter = _DiscussAdapter("这章主角的动机是进城寻亲,站得住。")
    with patch("app.engines.polish.polisher.get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/discuss",
            headers=headers,
            # target 整个缺省(前端无选段时不传)
            json={"messages": [{"role": "user", "content": "这章主角的动机站得住吗?"}]},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "寻亲" in body["reply"]
    assert body["suggestion"] is None
    # system 注入整章正文节选与梗概,且无「选中段落」锚点块
    system = adapter.captured[0].content
    assert "他走进了城门。" in system
    assert "主角进城" in system
    assert "作者选中、正在讨论的段落" not in system


# ---------- 书籍简介(synopsis) ----------


def _create_project_with_topic(client: TestClient, headers: dict) -> dict:
    p = _create_project(client, headers, "简介书")
    r = client.patch(
        f"/api/projects/{p['id']}",
        headers=headers,
        json={"topic": "落魄镖师发现镖箱里藏着个大活人", "genre": "武侠"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_synopsis_generate_ok(client):
    """mock LLM:生成接口返回简介;prompt 注入主题与类型。"""
    from unittest.mock import patch

    headers = _setup_user_with_key(client, "syn_user")
    p = _create_project_with_topic(client, headers)

    captured: dict = {}

    class _SynAdapter:
        async def ask(self, prompt, system=None):
            captured["prompt"] = prompt
            return "  一趟险镖,箱中藏着惊天秘密;落魄镖师从此卷入江湖漩涡。  "

    with patch("app.api.projects.create_llm_adapter", return_value=_SynAdapter()):
        r = client.post(f"/api/projects/{p['id']}/synopsis", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["synopsis"] == "一趟险镖,箱中藏着惊天秘密;落魄镖师从此卷入江湖漩涡。"
    assert "落魄镖师发现镖箱里藏着个大活人" in captured["prompt"]
    assert "武侠" in captured["prompt"]


def test_synopsis_generate_empty_topic_400(client):
    """主题为空 → 400,提示先去灵感区定主题。"""
    headers = _setup_user_with_key(client, "syn_empty")
    p = _create_project(client, headers, "没主题的书")

    r = client.post(f"/api/projects/{p['id']}/synopsis", headers=headers)
    assert r.status_code == 400
    assert "主题" in r.json()["detail"]


def test_synopsis_generate_requires_key(client):
    """未配置任何 key → 工厂层 400。"""
    headers = _auth(_register(client, "syn_nokey")["token"])
    p = _create_project_with_topic(client, headers)

    r = client.post(f"/api/projects/{p['id']}/synopsis", headers=headers)
    assert r.status_code == 400
    assert "API key" in r.json()["detail"]


def test_synopsis_generate_not_owner_404(client):
    """非 owner 生成他人项目简介 → 404(不泄露存在性)。"""
    a = _setup_user_with_key(client, "syn_a")
    b = _auth(_register(client, "syn_b")["token"])
    p = _create_project_with_topic(client, a)

    r = client.post(f"/api/projects/{p['id']}/synopsis", headers=b)
    assert r.status_code == 404


def test_synopsis_patch_roundtrip(client):
    """PATCH 保存简介后,get/list 都能读回;新建项目默认为 null。"""
    headers = _auth(_register(client, "syn_patch")["token"])
    p = _create_project(client, headers, "往返书")
    assert p["synopsis"] is None

    text = "一段手写的书籍简介。"
    r = client.patch(
        f"/api/projects/{p['id']}", headers=headers, json={"synopsis": text}
    )
    assert r.status_code == 200, r.text
    assert r.json()["synopsis"] == text

    assert client.get(
        f"/api/projects/{p['id']}", headers=headers
    ).json()["synopsis"] == text
    items = client.get("/api/projects", headers=headers).json()
    assert next(i for i in items if i["id"] == p["id"])["synopsis"] == text


# ---------- ②档多处批注改(revise-annotated-async) ----------


def _wait_job(client: TestClient, headers: dict, job_id: str, timeout: float = 10.0) -> dict:
    """轮询 job 至结束(非 running)。TestClient 同步驱动:每次 GET 推进事件循环,
    让后台 work 协程往前跑。mock 适配器下几十毫秒即完成。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} 超时未结束")


def test_revise_annotated_async_ok(client):
    """②档:逐条复用 polish_fragment,返回逐段 old/new pairs;
    每条批注的意见与蓝图摘要都注入各自 prompt。"""
    from unittest.mock import patch

    headers = _auth(_register(client, "anno_ok")["token"])
    p = _create_chapter_with_content(client, headers, "批注书")

    prompts: list[str] = []

    class _CaptureAdapter:
        async def ask(self, prompt, system=None):
            prompts.append(prompt)
            return "改好的段落。"

    with patch("app.engines.polish.polisher.get_adapter_for", return_value=_CaptureAdapter()):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/revise-annotated-async",
            headers=headers,
            json={"annotations": [
                {"para_idx": 0, "original": "他走进了城门。", "note": "更紧张一些"},
                {"para_idx": 2, "original": "夜色降临。", "note": "更抒情一些"},
            ]},
        )
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    pairs = job["result"]["pairs"]
    assert [x["para_idx"] for x in pairs] == [0, 2]
    assert all(x["ok"] for x in pairs)
    assert pairs[0]["old"] == "他走进了城门。" and pairs[0]["new"] == "改好的段落。"
    # 蓝图摘要注入每条 prompt;两条意见各注入各自 prompt(不串)
    assert all("主角进城" in pr for pr in prompts)
    assert any("更紧张一些" in pr for pr in prompts)
    assert any("更抒情一些" in pr for pr in prompts)


def test_revise_annotated_item_failure_marked(client):
    """单条空白批注(strip 后为空 → polish_fragment 抛错)只标 ok=False,
    不拖垮整个 job;其余条目照常返回。"""
    from unittest.mock import patch

    headers = _auth(_register(client, "anno_fail")["token"])
    p = _create_chapter_with_content(client, headers, "半失败书")

    class _Adapter:
        async def ask(self, prompt, system=None):
            return "改好的段落。"

    with patch("app.engines.polish.polisher.get_adapter_for", return_value=_Adapter()):
        r = client.post(
            f"/api/projects/{p['id']}/chapters/1/revise-annotated-async",
            headers=headers,
            json={"annotations": [
                {"para_idx": 0, "original": "他走进了城门。", "note": "更紧张"},
                {"para_idx": 1, "original": "   ", "note": "改这段"},
            ]},
        )
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    pairs = job["result"]["pairs"]
    assert pairs[0]["ok"] is True and pairs[0]["new"] == "改好的段落。"
    assert pairs[1]["ok"] is False and pairs[1]["new"] == ""


def test_revise_annotated_empty_422(client):
    """空批注列表 → 422(Pydantic min_length);章节不存在 → 404。"""
    headers = _auth(_register(client, "anno_empty")["token"])
    p = _create_chapter_with_content(client, headers, "空批注书")

    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/revise-annotated-async",
        headers=headers, json={"annotations": []},
    )
    assert r.status_code == 422

    r = client.post(
        f"/api/projects/{p['id']}/chapters/99/revise-annotated-async",
        headers=headers,
        json={"annotations": [{"para_idx": 0, "original": "一段正文", "note": ""}]},
    )
    assert r.status_code == 404


def test_revise_annotated_not_owner_404(client):
    """非 owner 对他人章节批注改 → 404(不泄露存在性)。"""
    a = _auth(_register(client, "anno_a")["token"])
    b = _auth(_register(client, "anno_b")["token"])
    p = _create_chapter_with_content(client, a, "别人的批注书")

    r = client.post(
        f"/api/projects/{p['id']}/chapters/1/revise-annotated-async",
        headers=b,
        json={"annotations": [{"para_idx": 0, "original": "他走进了城门。", "note": "改"}]},
    )
    assert r.status_code == 404
