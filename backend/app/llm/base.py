"""LLM 适配器抽象基类。

所有厂商适配器实现同一套接口:
- `complete()`      一次性返回完整回复
- `stream()`        异步逐块产出(供 SSE 流式生成用)

上层引擎只依赖本抽象,不关心底层是 DeepSeek 还是 OpenAI。
"""
from __future__ import annotations

import abc
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

import httpx

from app import live
from app.llm import throttle

logger = logging.getLogger("jarvis-write.llm")

# 瞬时错误状态码:限流/服务端抖动/网关与 CDN 异常,值得退避重试。
# 520-529 是 Cloudflare 系错误(524=CDN 等源站超时掐断,中转站高发)。
RETRYABLE_STATUSES = frozenset(
    {408, 409, 425, 429, 500, 502, 503, 504, 529} | set(range(520, 530))
)

# 网络层瞬时异常:超时/连接失败,同样值得重试
TRANSIENT_NET_ERRORS = (httpx.TimeoutException, httpx.ConnectError)

# 输出被 max_tokens 截断的收尾原因(各家叫法不同,归一到一处判定):
# OpenAI=length / Anthropic=max_tokens / Gemini=MAX_TOKENS
FINISH_TRUNCATED = frozenset(
    {"length", "max_tokens", "model_length", "max_output_tokens"}
)
# 被安全策略拦下:同一段提示词重试多少次都是空,不该浪费重试
FINISH_FILTERED = frozenset(
    {
        "content_filter",
        "safety",
        "recitation",
        "blocklist",
        "prohibited_content",
        "image_safety",
        "spii",
    }
)

_THINK_BLOCK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.I)


def strip_think(text: str) -> str:
    """剥掉正文里的 `<think>…</think>` 思考块。

    一部分推理模型/中转站不走 reasoning_content 字段,而是把思考直接混在
    content 里。闭合的思考块整块删;删完还残留未闭合的 `<think`(思考被
    max_tokens 截断在半途)就从该处截断——半截思考不是正文,留着只会污染
    下游的 JSON 解析与正文入库。
    """
    if not text or "<think" not in text.lower():
        return text
    cleaned = _THINK_BLOCK.sub("", text)
    unclosed = cleaned.lower().rfind("<think")
    if unclosed != -1:
        cleaned = cleaned[:unclosed]
    return cleaned.strip()


def as_text(value) -> str:
    """把渠道五花八门的 content/reasoning 形态归一成字符串。

    见过的形态:纯字符串、content blocks 数组(`[{"type":"text","text":…}]`)、
    以及 `{"content": …}` 包一层。取不出文本就返回空串,绝不抛异常。
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return as_text(value.get("text") or value.get("content") or "")
    if isinstance(value, list):
        return "".join(as_text(v) for v in value)
    return ""


class UpstreamError(RuntimeError):
    """上游调用失败(继承 RuntimeError,老代码的 except 不受影响)。

    附带 status / retryable,供重试层判断要不要再来一次;
    retryable=False 的错(401/403/404/参数错误)重试无意义,直接抛给用户。
    """

    def __init__(
        self, message: str, *, status: int | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class EmptyContentError(UpstreamError):
    """上游 HTTP 200 但正文为空(推理模型思考吃满预算/被安全过滤/渠道空转)。

    retryable=False 是刻意的:同参数再打一次只会再等一次几分钟的长生成,
    毫无意义,所以不进 `with_retries` 的重试圈;由 `ask()` 决定是放大
    max_tokens 重来(budget_bound=True)还是直接把原因抛给用户。
    """

    def __init__(
        self,
        message: str,
        *,
        budget_bound: bool = False,
        status: int | None = None,
        diagnosis: str = "",
    ) -> None:
        super().__init__(message, status=status, retryable=False)
        # True = 放大输出预算有希望救回来(思考截断/渠道空转)
        self.budget_bound = budget_bound
        # 机器可读的现场:finish_reason / 思考字数 / token 数,汇进最终报错
        self.diagnosis = diagnosis


def check_upstream(resp: httpx.Response, *, hint: str = "") -> dict:
    """校验上游响应并返回 JSON;异常时抛出用户可读的错误。

    - HTTP 错误(>=400):带上状态码和上游错误消息(若有);
    - 52x(Cloudflare 网关/CDN 错误):单独说明"生成耗时过长被掐断/渠道拥挤",
      不误导用户去查 Base URL;
    - HTTP 200 但 body 夹 error 字段(中转站软错误,如"无可用渠道"):也抛出,
      不再被当成正常响应放行、退化成下游"空回复";
    - 非 JSON(Base URL 填错/协议不匹配,上游回了 HTML 错误页):说明原因并附 hint。
    """
    if resp.status_code >= 400:
        msg = f"上游返回 HTTP {resp.status_code}"
        try:
            err = resp.json()
            detail = (err.get("error") or {}).get("message") or err.get("message")
            if detail:
                msg += f": {detail}"
        except Exception:  # noqa: BLE001
            snippet = resp.text[:80].strip()
            if snippet:
                msg += f"(响应非 JSON,开头: {snippet})"
        if 520 <= resp.status_code <= 529:
            msg += (
                "。这是中转站网关/CDN 错误,通常是生成耗时过长被 CDN 掐断"
                "或渠道拥挤,稍后重试一般可恢复(系统会自动重试并改走流式)"
            )
        elif hint:
            msg += f"。{hint}"
        raise UpstreamError(
            msg,
            status=resp.status_code,
            retryable=resp.status_code in RETRYABLE_STATUSES,
        )
    try:
        data = resp.json()
    except json.JSONDecodeError:
        snippet = resp.text[:80].strip()
        msg = (
            f"上游返回了非 JSON 内容(HTTP {resp.status_code}),通常是 Base URL 填错"
            f"或该渠道不支持此协议(响应开头: {snippet})"
        )
        if hint:
            msg += f"。{hint}"
        raise UpstreamError(msg, status=resp.status_code) from None
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        detail = err.get("message") if isinstance(err, dict) else str(err)
        raise UpstreamError(
            f"上游返回错误(HTTP {resp.status_code}): {detail or err}",
            status=resp.status_code,
        )
    return data


def _describe_exc(exc: Exception) -> str:
    """给重试耗尽后的"最后错误"一个可读描述。

    httpx 网络异常在 Windows/anyio 下常是空消息(DNS 失败/连接被重置实测 str 为 ''),
    直接拼进"最后错误: "会一片空白,用户无从判断;按异常类型翻译成中文。
    """
    msg = str(exc).strip()
    if msg:
        return msg
    if isinstance(exc, httpx.ConnectTimeout):
        return "网络连接超时"
    if isinstance(exc, httpx.ConnectError):
        return (
            "网络连接失败/被重置(本机断网、DNS 故障,或该渠道套 CDN 在当前网络下"
            "间歇性不通——若反复出现请更换渠道)"
        )
    if isinstance(exc, httpx.TimeoutException):
        return "网络超时"
    return type(exc).__name__


async def with_retries(call, *, attempts: int = 3, base_delay: float = 2.0, on_retry=None):
    """瞬时错误退避重试:retryable 的 UpstreamError / 网络超时连接错误。

    - call(attempt): 第几次尝试(0 起),返回 awaitable;
    - on_retry(exc): 每次重试前回调,调用方可借此调整下一次尝试的方式
      (如被 CDN 掐断后改走流式聚合);
    - 非 retryable 的错误(鉴权/参数/Base URL 错)立即抛出,不浪费重试。
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await call(attempt)
        except UpstreamError as exc:
            if not exc.retryable:
                raise
            last = exc
        except TRANSIENT_NET_ERRORS as exc:
            last = exc
        if attempt < attempts - 1:
            if on_retry is not None:
                on_retry(last)
            await asyncio.sleep(base_delay * (2**attempt))
    raise UpstreamError(
        f"上游连续 {attempts} 次调用失败,最后错误: {_describe_exc(last)}",
        retryable=True,
    ) from last
# 渠道明确拒收思考控制参数的现场记录:(base_url, model) → 不再下发该参数。
# 适配器是按次创建的,不落模块级记不住,同一渠道每次调用都要白挨一个 400。
_THINKING_REJECTED: set[tuple[str, str]] = set()

# 400 响应里出现这些字样、且我们发了思考参数 → 判为参数不被渠道接受
# (OpenAI 官方对未知参数回 "Unknown parameter",各家网关措辞不一,取宽交集)
_PARAM_REJECT_MARKERS = (
    "thinking", "reasoning", "unknown", "unexpected", "unsupported",
    "not support", "parameter", "extra", "多余", "不支持", "无效参数",
)


def thinking_param_rejected(base_url: str, model: str) -> bool:
    """该 (渠道, 模型) 是否已被判定拒收思考参数(payload 据此跳过注入)。"""
    return (base_url or "", model) in _THINKING_REJECTED


def remember_thinking_rejected(base_url: str, model: str) -> None:
    _THINKING_REJECTED.add((base_url or "", model))


def _is_param_rejection(exc: "UpstreamError", sent_thinking: bool) -> bool:
    if not sent_thinking or exc.status != 400:
        return False
    text = str(exc).lower()
    return any(m in text for m in _PARAM_REJECT_MARKERS)


Role = Literal["system", "user", "assistant"]


@dataclass
class LLMMessage:
    """一条对话消息。"""

    role: Role
    content: str


@dataclass
class LLMResponse:
    """一次完整调用的返回。"""

    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # 收尾原因:stop/length/content_filter…(Anthropic 的 stop_reason、
    # Gemini 的 finishReason 都归一到这里)。空正文归因全靠它。
    finish_reason: str = ""
    # 推理模型的思考内容(reasoning_content / thinking block),只用于诊断
    # 与兜底,正常路径绝不当正文
    reasoning: str = ""
    # 原始返回,调试用;不参与业务逻辑
    raw: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMAdapter(abc.ABC):
    """厂商适配器抽象基类。

    子类通过构造函数拿到 api_key / base_url / model_name 等,
    实现 `_complete_once`(非流式)与 `_iter_stream`(流式)即可接入;
    `complete()` 的流式优先/重试/空正文归因在本类统一实现。
    """

    interface_format: str = "base"

    # 长生成默认走流式(与 cc-switch / Claude Code 的行为一致)。
    # 非流式的长生成可能几分钟不吐一个字节,套 Cloudflare 的中转站会在
    # ~100 秒处掐断(HTTP 524),表现就是"测试连通没问题、正式生成老失败"。
    prefer_stream: bool = True

    def __init__(
        self,
        api_key: str,
        model_name: str,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: int = 600,
        thinking_mode: str = "",
        thinking_forced: bool = False,
        max_concurrency: int = 0,
        rpm: int = 0,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        # 思考模式控制:""(跟随默认,即 config.default_thinking_mode)/disabled/low/high/max。
        # 只对识别得出的推理系模型下发参数(见 openai_compatible._looks_reasoning),
        # Anthropic/Gemini 原生协议适配器暂不消费该字段。
        # thinking_forced:用户在配置里显式指定(非跟随默认)——此时不受模型名启发式限制。
        self.thinking_mode = thinking_mode
        self.thinking_forced = thinking_forced
        # 主动限速(0 = 不限):按「渠道 + 模型」维度限并发/速率,见 llm/throttle.py。
        # 同一中转站同一模型的上游配额是共享的,按渠道限才能真防 429/防封号。
        self.max_concurrency = max_concurrency
        self.rpm = rpm
        # 瞬时错误重试:次数与退避基数(秒)。置 1 即关闭重试。
        self.retry_attempts = 3
        self.retry_base_delay = 2.0

    def throttle_key(self) -> str:
        """限速闸门的维度键:渠道 + 模型(上游配额真正共享的单位)。"""
        return f"{(self.base_url or 'default').strip()}::{self.model_name}"

    # ---- 便捷构造:把一个纯文本 prompt 包成 messages ----
    @staticmethod
    def to_messages(
        prompt: str, system: str | None = None
    ) -> list[LLMMessage]:
        msgs: list[LLMMessage] = []
        if system:
            msgs.append(LLMMessage(role="system", content=system))
        msgs.append(LLMMessage(role="user", content=prompt))
        return msgs

    # ---- 统一的 complete:流式优先 + 退避重试 + 空正文归因 ----
    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        """一次性拿完整回复。

        为什么默认走流式:流式一出响应头就持续吐 chunk,CDN 不会掐,慢渠道也
        不会整段闷住;这正是 cc-switch/Claude Code 稳而我们这边容易失败的差别。
        - 渠道明确拒绝 SSE(非瞬时错,如 400/404 参数不支持)→ 自动回落非流式;
        - 429/5xx/网络超时 → 指数退避重试,并把后续尝试切到流式;
        - 空正文(EmptyContentError)不在这层重试,交给 `ask()` 放大预算。
        """
        use_stream = self.prefer_stream
        stream_rejected = False

        async def call(_attempt: int) -> LLMResponse:
            nonlocal use_stream, stream_rejected
            if not use_stream:
                return await self._once_live(messages)
            try:
                return await self._complete_via_stream(messages)
            except EmptyContentError:
                raise
            except UpstreamError as exc:
                # 渠道不认思考控制参数(400 参数错):撤掉参数记下渠道,重发一次。
                # 必须排在"回落非流式"之前——参数问题换流式/非流式都会一样 400。
                if _is_param_rejection(exc, sent_thinking=bool(self.thinking_mode)):
                    logger.info(
                        "渠道拒收思考参数(%s),撤掉参数重发: %s",
                        self.model_name, exc,
                    )
                    remember_thinking_rejected(self.base_url or "", self.model_name)
                    self.thinking_mode = ""
                    exc.retryable = True
                    raise
                # 瞬时错/鉴权错换成非流式也一样,交给外层重试或直接抛
                if exc.retryable or exc.status in (401, 403):
                    raise
                logger.warning(
                    "该渠道疑似不支持流式(HTTP %s),回落非流式: %s", exc.status, exc
                )
                stream_rejected = True
                use_stream = False
                return await self._once_live(messages)

        def on_retry(_exc: Exception) -> None:
            nonlocal use_stream
            use_stream = not stream_rejected

        return await with_retries(
            call,
            attempts=self.retry_attempts,
            base_delay=self.retry_base_delay,
            on_retry=on_retry,
        )

    @abc.abstractmethod
    async def _complete_once(self, messages: list[LLMMessage]) -> LLMResponse:
        """非流式调用一次:POST 后等完整响应体。"""
        raise NotImplementedError

    @abc.abstractmethod
    def _iter_stream(
        self, messages: list[LLMMessage], sink: dict
    ) -> AsyncIterator[str]:
        """流式产出文本增量,并把诊断信息塞进 sink。

        sink 约定(都可缺省):finish_reason / reasoning / prompt_tokens /
        completion_tokens。实现应为 async generator。
        """
        raise NotImplementedError

    def stream(self, messages: list[LLMMessage]) -> AsyncIterator[str]:
        """异步逐块产出文本增量(用于 SSE 打字机效果),丢弃诊断信息。"""
        return self._iter_live(messages, {})

    # ---- 实时正文:全站唯一的直播出水口 ----
    def _iter_live(
        self, messages: list[LLMMessage], sink: dict
    ) -> AsyncIterator[str]:
        """在流式增量上装一个分流器:一边照常产出给调用方,一边推给实时正文总线。

        全站 60+ 处 LLM 调用最终都汇到这两条路(`ask()/complete()` 走
        `_complete_via_stream`,少数自己接 SSE 的走 `stream()`),所以"让前端看见
        模型正在写什么"只需这一个钩子,不必逐接口改、也不会漏。
        无 job 上下文时(前台请求/脚本/测试)`live.publish` 自己丢弃。

        注:这里推的是原始增量。少数把思考混在正文里的模型会带 `<think>` 标签,
        直播照原样显示(正式落库的正文由 strip_think 清洗,两者互不影响)。

        begin_call/end_call 圈出"这一次调用正在吐字":有些环节会在同一次流式调用
        进行中反复更新进度文案(蓝图边写边报「已生成 N/M 章」),总线据此只换标签、
        不清屏,免得用户眼前的字每隔几百字消失一次。
        """
        async def gen() -> AsyncIterator[str]:
            # 全站唯一直播出水口,也是全站唯一的上游入水口:并发/速率闸门挂在这里,
            # complete()(含流式聚合)与直连 stream() 两条路都逃不过,一处接线全覆盖
            async with throttle.slot(self.throttle_key(), self.max_concurrency, self.rpm):
                live.begin_call()
                try:
                    async for delta in self._iter_stream(messages, sink):
                        if delta:
                            live.publish(delta)
                        yield delta
                finally:
                    live.end_call()

        return gen()

    async def _once_live(self, messages: list[LLMMessage]) -> LLMResponse:
        """非流式兜底路径:整段回来后补播一次(总比一个字都看不到强)。"""
        async with throttle.slot(self.throttle_key(), self.max_concurrency, self.rpm):
            resp = await self._complete_once(messages)
        if resp.content:
            live.publish(resp.content)
        return resp

    async def _complete_via_stream(self, messages: list[LLMMessage]) -> LLMResponse:
        """流式聚合成一次完整回复:边收边拼,并做空正文归因。"""
        sink: dict = {}
        chunks: list[str] = []
        async for delta in self._iter_live(messages, sink):
            chunks.append(delta)
        text = strip_think("".join(chunks))
        resp = LLMResponse(
            content=text,
            model=self.model_name,
            prompt_tokens=sink.get("prompt_tokens", 0),
            completion_tokens=sink.get("completion_tokens", 0),
            finish_reason=sink.get("finish_reason", "") or "",
            reasoning=sink.get("reasoning", "") or "",
        )
        if text.strip():
            return resp
        salvaged = self._salvage_reasoning(resp)
        if salvaged:
            resp.content = salvaged
            return resp
        note = "" if (chunks or resp.reasoning) else "流式连上了但一个字节都没吐"
        raise self._empty_content_error(resp, note=note)

    # ---- 空正文:归因与兜底 ----
    def _salvage_reasoning(self, resp: LLMResponse) -> str:
        """正文为空但思考完整时,拿思考内容兜底。

        少数中转渠道会把答案整段塞进 reasoning_content / thinking,content 留空。
        只在"正常收尾"(finish_reason 非截断非过滤)时兜底——思考被 max_tokens
        截断时那就是半截思考,绝不能当正文写进书里。
        """
        fr = (resp.finish_reason or "").lower()
        if fr in FINISH_TRUNCATED or fr in FINISH_FILTERED:
            return ""
        text = strip_think(resp.reasoning or "").strip()
        if not text:
            return ""
        logger.warning(
            "上游把正文放进了思考字段(model=%s, finish_reason=%s),取思考内容兜底 %d 字",
            self.model_name,
            resp.finish_reason or "无",
            len(text),
        )
        return text

    def _empty_content_error(
        self, resp: LLMResponse, *, status: int | None = None, note: str = ""
    ) -> EmptyContentError:
        """把"200 但正文为空"翻译成有原因、可行动的错误。"""
        fr_raw = resp.finish_reason or ""
        fr = fr_raw.lower()
        diag = (
            f"finish_reason={fr_raw or '未知'}, 思考 {len(resp.reasoning or '')} 字, "
            f"输出 {resp.completion_tokens} tokens, max_tokens={self.max_tokens}"
        )
        prefix = f"{note}。" if note else ""
        if fr in FINISH_FILTERED:
            return EmptyContentError(
                f"{prefix}上游拒绝输出:内容被安全策略拦下({diag})。"
                "改写敏感段落或到「模型设置」换渠道再试",
                budget_bound=False,
                status=status,
                diagnosis=diag,
            )
        if fr in FINISH_TRUNCATED or (resp.reasoning or "").strip():
            return EmptyContentError(
                f"{prefix}模型把输出预算全花在思考上了,正文为空({diag})。"
                "推理模型的思考也占 max_tokens,系统会放大预算重试",
                budget_bound=True,
                status=status,
                diagnosis=diag,
            )
        return EmptyContentError(
            f"{prefix}上游返回 200 但正文为空({diag})。"
            "多为中转渠道空转/额度耗尽/被静默过滤",
            budget_bound=True,
            status=status,
            diagnosis=diag,
        )

    @staticmethod
    def _record_usage(resp: "LLMResponse") -> None:
        """用量记账(静默失败,绝不影响生成)。"""
        try:
            from app.auth import current_user_id
            from app.db.models import LlmUsage
            from app.db.session import session_scope

            with session_scope() as db:
                db.add(
                    LlmUsage(
                        user_id=current_user_id.get(),
                        model=resp.model,
                        prompt_tokens=resp.prompt_tokens,
                        completion_tokens=resp.completion_tokens,
                    )
                )
        except Exception:  # noqa: BLE001
            pass

    # ---- 便捷入口:直接传字符串 ----
    async def ask(self, prompt: str, system: str | None = None) -> str:
        """带重试的问答:空正文自动放大 max_tokens 重试,并给出原因。

        推理类模型(DeepSeek-R 系/中转站转的思考模型)思考内容会吃掉 token
        上限,导致正文为空——空正文绝不能当结果返回污染下游,这里兜底。
        """
        return await self.ask_messages(self.to_messages(prompt, system))

    async def ask_messages(self, messages: list[LLMMessage]) -> str:
        """多轮对话版的 ask(见模块级 `complete_text_with_budget`)。"""
        return await complete_text_with_budget(self, messages)


def _looks_degenerate(text: str) -> bool:
    """复读退化检测:长正文里同一批句子反复出现(模型陷入生成循环)。

    按中英文句读+换行切句,句长 >=6 字才计入,句子总数 >=24 才判(短文天然
    重复率高,不冤枉);唯一句占比不足一半即判退化。实测退化形态(同一句连抄
    几十遍)比率趋近 1,正常章节在 0.1 以下,阈值 0.5 两边都有足够余量。
    """
    sents = [s.strip() for s in re.split(r"[。!?!.!?\n]", text) if len(s.strip()) >= 6]
    if len(sents) < 24:
        return False
    return len(set(sents)) / len(sents) < 0.5


async def complete_text_with_budget(adapter, messages: list[LLMMessage]) -> str:
    """调一次模型拿纯文本:空正文/被截断都放大预算重试(最多 3 轮)+ 用量记账。

    自己拼 messages 的入口(改稿讨论、架构讨论、润色讨论、灵感对话)都必须走这里,
    不要裸调 `complete()`:complete() 遇空正文是**抛** EmptyContentError,裸调用会
    在第一次空正文就把错误摔给用户,拿不到放大预算的第二、三次机会。
    安全过滤类的空正文(budget_bound=False)放大预算也没用,立即抛出。

    **截断也要重试**:推理模型思考吃掉大半预算时,正文往往不是空的而是**吐了个开头
    就被 max_tokens 砍断**(实测:定妆照 JSON 停在 `char 73`,报「Unterminated string」)。
    只治空正文治不到这种,半截内容会一路流到下游解析失败,还错怪成别的原因。
    三轮都截断就把**最长的那次**返回(总比无内容好),由调用方决定能不能用。

    写成模块级函数而不是只挂在 LLMAdapter 上:调用方大量使用鸭子类型的假适配器
    (测试里只实现 complete/ask),这里只依赖 `complete()` 与 `max_tokens`。
    """
    original_max = adapter.max_tokens
    model = getattr(adapter, "model_name", "?")
    diag = ""
    longest = ""  # 被截断但非空的最好一次(兜底返回,别让一次调用彻底白跑)
    try:
        for attempt in range(3):
            try:
                resp = await adapter.complete(messages)
            except EmptyContentError as exc:
                diag = exc.diagnosis or str(exc)
                logger.warning(
                    "空正文(model=%s, 第 %d/3 次, max_tokens=%d): %s",
                    model, attempt + 1, adapter.max_tokens, exc,
                )
                if not exc.budget_bound:
                    raise
                adapter.max_tokens = min(adapter.max_tokens * 2, 32768)
                continue
            LLMAdapter._record_usage(resp)
            content = (resp.content or "").strip()
            # getattr:鸭子类型的假适配器(测试/自定义)可能没有这两个字段,
            # 而本函数的契约是只依赖 complete() 与 max_tokens,不许因此炸掉
            finish = str(getattr(resp, "finish_reason", "") or "")
            if content:
                if finish.lower() not in FINISH_TRUNCATED:
                    return content
                if len(content) > len(longest):
                    longest = content
                diag = (
                    f"finish_reason={finish}, 正文 {len(content)} 字, "
                    f"思考 {len(getattr(resp, 'reasoning', '') or '')} 字, "
                    f"max_tokens={adapter.max_tokens}"
                )
                logger.warning(
                    "正文被截断(model=%s, 第 %d/3 次): %s", model, attempt + 1, diag
                )
                if _looks_degenerate(content):
                    # 复读退化:同一批句子反复填充直到烧光输出预算。这不是预算不足,
                    # 翻倍重试只会再陪跑几分钟、烧更多 token、产出更多复读——快败,
                    # 把「直接重试还是换模型」的决定交回用户。
                    raise UpstreamError(
                        f"模型输出陷入复读退化(正文 {len(content)} 字几乎全是重复句子,"
                        f"已耗尽输出预算),已终止本次调用不再重试,以免继续烧 token。"
                        "请直接重试;反复出现请到「设置」更换模型",
                        retryable=False,
                    )
                if adapter.max_tokens >= 32768:
                    break  # 预算已到顶,再翻倍也没有意义
                adapter.max_tokens = min(adapter.max_tokens * 2, 32768)
                continue
            # 适配器没归因出来的空正文(自定义/鸭子类型适配器可能走到)
            diag = (
                f"finish_reason={finish or '未知'}, "
                f"输出 {getattr(resp, 'completion_tokens', 0)} tokens"
            )
            logger.warning("空正文(model=%s, 第 %d/3 次): %s", model, attempt + 1, diag)
            adapter.max_tokens = min(adapter.max_tokens * 2, 32768)
        if longest:
            logger.warning(
                "三轮均被截断,返回最长的一次(model=%s, %d 字): %s",
                model, len(longest), diag,
            )
            return longest
        raise UpstreamError(
            f"模型连续 3 次返回空正文(model={model},输出预算已放大到 "
            f"{adapter.max_tokens})。诊断: {diag or '无'}。"
            "最常见原因是推理模型的思考吃满了输出预算:换一个非推理模型/"
            "思考更省的模型,或到「模型设置」把该配置的 max_tokens 调大",
            retryable=False,
        )
    finally:
        adapter.max_tokens = original_max
