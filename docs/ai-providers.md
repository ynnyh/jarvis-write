# AI 供应商兼容性指南

jarvis-write 不内置任何模型——生成文字全部走你自己的 key,本项目做包在外面的控制层。本文回答三个问题:**我手头的 key 能不能用、怎么填、有什么注意事项**。

设置入口:登录后进 **设置 → 模型设置**,可建多套命名配置一键切换;每套可指定用途档位(写手/审校),key 按账号隔离、加密存储。

## 支持的协议卡(三选一 + 存量别名)

| 协议卡 | 适用场景 | base_url 预填 | 模型名示例 |
|---|---|---|---|
| **openai-compatible**(主力) | OpenAI / DeepSeek / Kimi / 通义 / 各类中转站 / 本地 Ollama 等一切 OpenAI Chat Completions 兼容服务 | `https://api.openai.com/v1` | `gpt-4o` / `deepseek-chat` / 中转站给的模型名 |
| **anthropic** | Anthropic Claude 原生 Messages 协议 | `https://api.anthropic.com` | `claude-sonnet-4-20250514` |
| **gemini** | Google Gemini 原生协议 | `https://generativelanguage.googleapis.com/v1beta` | `gemini-2.0-flash` |
| deepseek / openai | 上面两张卡的**存量别名**(行为完全等同),保留给历史配置,新配置建议直接用 openai-compatible | — | — |

**一句话结论:除非你明确要用 Claude / Gemini 原生协议,选 openai-compatible 就对了。**

## 已验证清单

- **DeepSeek 官方**(`https://api.deepseek.com` + `deepseek-chat`):默认档,全文生成/抽取/润色全链路日常验证
- **OpenAI 兼容中转站**:本站线上环境即跑在中转上,长流式、多档模型切换均为日常路径;Cloudflare 托管的中转会自动识别并做适配
- **Anthropic / Gemini 原生协议**:适配器内建,协议级支持;不同代理商的具体差异欢迎在 [GitHub Issues](https://github.com/ynnyh/jarvis-write/issues) 反馈,持续更新本页

## 内建的行为(不用你配)

- **重试与退避**:408/409/425/429/5xx/网络超时自动指数退避重试,并把后续尝试切到流式——中转站偶发抽风时大多数生成任务能自愈
- **主动限速(可选)**:每套配置可设「并发上限」和「RPM(次/分钟)」,0 = 不限;按**渠道 + 模型**维度全站共享计数——同一个中转站同一个模型的配额是共享的,多人多任务同时跑时设一个上限,满了自动排队,防 429/防封号也防一人打爆共享配额
- **超时/输出上限**:全局默认单次请求 600 秒、max_tokens 8192;每套配置可单独覆盖(0 = 跟随全局)
- **三档分模型**:写手(quality)/快活(fast:草稿、摘要)/审校(review:主审评分、一致性门禁)可分别指定不同模型——**写手与审校分模型,治「同模型自审自写」的评分死锁**
- **思考模式**:默认关闭(推理系模型思考默认开且常吃光 max_tokens,实测空正文+翻倍重试);需要推理的配置可单独强制 low/high/max

## 安全

- key 落库前经 **Fernet(AES-128-CBC + HMAC)加密**,按账号隔离,导出/备份文件不包含任何 key
- 服务器部署时,base_url 指向**内网/本机地址会被拒绝**——防止把 key 发去错误的地方;本机 Ollama 场景请用桌面版
- 任何导出功能(JSON 项目导出 / txt / epub)都不会带出 key

## 常见问题

**提示「未配置 XX 的 API key」**:该账号还没配模型,进「模型设置」新建一套配置即可。公开体验站(官网可进)的演示账号就是刻意不配 key 的,只供阅读。

**base_url 要不要带 `/v1`**:openai-compatible 卡按 OpenAI 惯例拼路径,预填的 `https://api.openai.com/v1` 带 `/v1`;中转站按它家文档给的基础地址填,通常也以 `/v1` 结尾。DeepSeek 官方卡填 `https://api.deepseek.com`(不带 /v1)。

**模型名填什么**:服务商文档里的**准确模型 ID**(如 `deepseek-chat`),不是商品名;中转站用它们列表里给的名字。

**生成一直超时**:先在模型设置里把该配置的「连通性测试」跑通;长文生成整体超时上限 600 秒,中转站如果对单请求限时应换支持长流式的线路。
