# 后台任务错误归一化:常见 LLM/网络异常上屏前转中文(原始英文进日志)
import httpx

from app.jobs import normalize_job_error


def test_http_status_errors_normalize_to_chinese():
    assert "API Key" in normalize_job_error(RuntimeError("上游返回 HTTP 401: invalid_api_key"))
    assert "模型不存在" in normalize_job_error(RuntimeError("上游返回 HTTP 404: model_not_found"))
    assert "限流" in normalize_job_error(RuntimeError("上游返回 HTTP 429: rate limit exceeded"))
    assert "欠费" in normalize_job_error(RuntimeError("上游返回 HTTP 402: insufficient quota"))


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
