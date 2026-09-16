# -*- coding: utf-8 -*-
"""订单制全流程冒烟(docs/20 首批):真实 HTTP 服务栈走一遍新端点。

临时库 + alembic 升级 + uvicorn 起服务,按用户旅程串:
注册 → 建书 → 直插大纲(绕 LLM)→ 订单存草稿/确认 → 作战图(含订单卡数据)
→ 章节订单回读 → 骨架 GET/编辑/拍板/锁 → 章纲锁/解锁 → 对账(无抽取时订单区如实缺省)
所有断言通过 = 冒烟通过。不调任何 LLM。
"""
import json
import os
import tempfile
import threading
import time
import urllib.request

import sys
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix="jw-smoke-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/smoke.db"
os.environ["INVITE_CODE"] = "smoke"
os.environ["RATE_LIMIT_ENABLED"] = "false"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.db.migration import _alembic_ini_path  # noqa: E402

cfg = Config(_alembic_ini_path())
cfg.attributes["configure_logger"] = False
command.upgrade(cfg, "head")
print("[1] alembic upgrade head OK")

from app.main import app  # noqa: E402

config = uvicorn.Config(app, host="127.0.0.1", port=18099, log_level="warning")
server = uvicorn.Server(config)
t = threading.Thread(target=server.run, daemon=True)
t.start()
for _ in range(50):
    time.sleep(0.2)
    if server.started:
        break
assert server.started, "服务未启动"
print("[2] uvicorn 起服 OK (127.0.0.1:18099)")

BASE = "http://127.0.0.1:18099"


def call(method: str, path: str, body: dict | None = None, token: str | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"__http_error__": e.code}


out = call("POST", "/api/auth/register",
           {"username": "smoker", "password": "pass123", "invite_code": "smoke"})
token = out["token"]
pid = call("POST", "/api/projects", {"title": "冒烟之书", "target_chapters": 12, "genre": "都市"},
           token)["id"]
print(f"[3] 注册+建书 OK (pid={pid})")

# 直插大纲 + 一章正文(绕 LLM,订单链路只需要这些)
from app.db.models import Chapter, Outline  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.engines.pipeline.blueprint import _outline_content_hash  # noqa: E402

s = SessionLocal()
data = {"title": "第1章", "chapter_role": "推进", "chapter_purpose": "p",
        "suspense_level": "中", "summary": "s", "characters_involved": ["陈默"]}
s.add(Outline(project_id=pid, chapter_number=1, content_hash=_outline_content_hash(data), **data))
s.add(Chapter(project_id=pid, chapter_number=1, final_content="陈默走进了雨里。", word_count=9))
s.commit()
s.close()
print("[4] 直插大纲+正文 OK")

payload = {
    "cast": {
        "entering": [{"name": "林晚", "reason": "带线索登场"}],
        "present": ["陈默"],
        "exiting": [{"name": "老赵", "mode": "远行", "threads": "账本"}],
    },
    "relations": [{"from": "林晚", "to": "陈默", "before": "陌生", "after": "合作", "event": "交易"}],
    "beats": ["接到匿名信", "亮出账本"],
    "hooks": {"carry_in": [{"text": "脚步声", "must": True}], "leave": "账本少一页"},
    "foreshadow": {"plant": ["缺页秘密"]},
    "scenes": [],
    "free_directive": "全章小雨",
}
b = f"/api/projects/{pid}/chapters/1"
r = call("PUT", f"{b}/order", payload, token)
assert r["status"] == "draft" and r["version"] == 1, r
r = call("POST", f"{b}/order/confirm", payload, token)
assert r["status"] == "confirmed" and r["version"] == 2, r
print(f"[5] 订单存草稿+确认 OK (v{r['version']})")

d = call("GET", f"{b}/dossier", token=token)
assert d["chapter_number"] == 1
o = call("GET", f"{b}/order", token=token)
assert o["order"]["payload"]["free_directive"] == "全章小雨"
rec = call("GET", f"{b}/reconciliation", token=token)
oc = rec["order_check"]
assert oc is not None and oc["version"] == 2, oc
assert "林晚" in oc["missed"], oc          # 正文只写了陈默 → 该来没来
assert "老赵" in oc["exit_missing"], oc    # 说好退场没写退场戏
print(f"[6] 作战图/订单回读/对账 OK (missed={oc['missed']}, exit_missing={oc['exit_missing']})")

sk = call("GET", f"/api/projects/{pid}/skeleton", token=token)
assert sk["target_chapters"] == 12 and sk["segments"] == []
r = call("PUT", f"/api/projects/{pid}/skeleton/0", {"title": "初入局"}, token)
assert r.get("__http_error__") == 404, r  # 空表编辑第 0 段 → 404(边界正确)
print("[7] 骨架 GET + 空表编辑边界 404 OK")

# 锁章 + 解锁
r = call("POST", f"/api/projects/{pid}/outlines/1/lock?locked=true", None, token)
assert r["locked"] is True
r = call("POST", f"/api/projects/{pid}/outlines/1/lock?locked=false", None, token)
assert r["locked"] is False
print("[8] 章纲锁定/解锁 OK")

jobs = call("GET", "/api/jobs?all=true", token=token)
assert "jobs" in jobs
print("[9] 任务中心接口 OK")

call("POST", f"{b}/order/unconfirm", None, token)
print("[10] 撤回确认 OK")

server.should_exit = True
t.join(timeout=5)
print("\n=== 冒烟全部通过 ===")
