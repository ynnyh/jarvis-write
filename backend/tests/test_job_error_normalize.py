# 后台任务错误归一化:常见 LLM/网络异常上屏前转中文(原始英文进日志)
import httpx

from app.jobs import normalize_job_error


def test_http_status_errors_normalize_to_chinese():
    assert "API Key" in normalize_job_error(RuntimeError("上游返回 HTTP 401: invalid_api_key"))
    assert "模型不存在" in normalize_job_error(RuntimeError("上游返回 HTTP 404: model_not_found"))
    assert "限流" in normalize_job_error(RuntimeError("上游返回 HTTP 429: rate limit exceeded"))
    out = normalize_job_error(RuntimeError("上游返回 HTTP 402: insufficient quota"))
    assert "欠费" in out  # 会提到欠费,但不再咬定
    assert "insufficient quota" in out  # 上游原文保留(证据不能被话术盖掉)
    assert "令牌" in out  # 指引覆盖令牌额度这个假欠费口
    assert "模型账户欠费" not in out  # 旧话术:把 402 一口咬定成账户欠费


def test_402_relay_token_quota_not_labeled_arrears():
    """中转站令牌额度用尽也回 402:账户有钱,提示却让去充值——误导用户的根源话术。"""
    out = normalize_job_error(
        RuntimeError("上游返回 HTTP 402(余额/额度类拒绝): 令牌额度已用尽")
    )
    assert "令牌额度已用尽" in out  # 服务商原文照实上屏
    assert "不一定是账户欠费" in out
    assert "充值" in out  # 真欠费的用户仍有行动指引


def test_httpx_network_errors_normalize():
    assert "无法连接" in normalize_job_error(httpx.ConnectError("connection refused"))
    assert "超时" in normalize_job_error(httpx.ReadTimeout("timed out"))


def test_unknown_error_passthrough():
    # 已是中文的业务错误/未知异常:原样返回,不瞎猜
    assert normalize_job_error(ValueError("第 3 章没有大纲")) == "第 3 章没有大纲"
    assert normalize_job_error(RuntimeError("some weird error")) == "some weird error"


def test_render_error_not_hijacked():
    """出片链路的错误不许被翻译成『模型 API Key』——文案里同样含 HTTP 401/402,
    但说的是出片平台的令牌与余额,劫持后用户会去改错地方的 key。"""
    from app.engines.render.client import RenderError

    msg = "出片平台拒绝了查询请求(HTTP 402):令牌无效或余额不足,请到「设置 → 出片引擎」检查令牌与账户余额。"
    assert normalize_job_error(RenderError(msg)) == msg
    msg2 = "提交出片任务失败(HTTP 401):token invalid"
    assert normalize_job_error(RenderError(msg2)) == msg2
