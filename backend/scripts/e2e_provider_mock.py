# -*- coding: utf-8 -*-
"""给临时验证库插一行指向 mock LLM 的 provider 配置(配合 scripts/mock_llm.py)。

为什么插库:设置页填 127.0.0.1 会被 SSRF 防线(assert_public_base_url)拒绝,
本地全流程走查只能直接写 provider_configs——api_key 用 app.crypto.encrypt
加密落库,quality/fast/review 三档默认全指向它。

用法(DATABASE_URL 与后端 uvicorn 同源):
    DATABASE_URL="sqlite:///./verify_jw.db" python scripts/e2e_provider_mock.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.crypto import encrypt  # noqa: E402
from app.db.models import ProviderConfig, User  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

BASE_URL = os.environ.get("MOCK_LLM_BASE_URL", "http://127.0.0.1:8766/v1")
MODEL = "mock-1"


def main() -> None:
    with SessionLocal() as db:
        users = db.query(User).all()
        if not users:
            raise SystemExit("先跑 scripts/e2e_seed.py 建测试用户")
        # 本地/桌面模式默认落 admin,e2e 走 e2e_writer——干脆每个用户都配一份,
        # 走查时不用关心当前登录的是谁
        for user in users:
            row = db.query(ProviderConfig).filter_by(user_id=user.id, name="mock").first()
            if row is None:
                row = ProviderConfig(user_id=user.id, name="mock")
                db.add(row)
            row.interface_format = "openai"
            row.api_key = encrypt("sk-mock-verify")
            row.base_url = BASE_URL
            row.model = MODEL
            row.is_default = True
            row.is_default_fast = True
            row.is_default_review = True
        db.commit()
        print(f"[e2e_provider_mock] ready: {BASE_URL} model={MODEL} users={[u.username for u in users]}")


if __name__ == "__main__":
    main()
