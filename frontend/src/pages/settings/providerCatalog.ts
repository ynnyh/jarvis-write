// 模型接入(cc-switch 风格)的协议目录与归一助手。拆自 SettingsPage.tsx。
// 后端有三类 wire 协议——openai-compatible / anthropic / gemini,存库的 interface_format
// 用这三个大类之一;deepseek/openai 是 openai-compatible 的历史别名,只在回显存量配置时并入通用卡。

// 协议大类:新建配置只在这三类里选。desc/baseUrl/model 与后端 settings.py 的 _PRESETS 对齐。
export interface ProtocolCategory {
  key: string; label: string; desc: string; baseUrl: string; model: string;
}
export const PROTOCOL_CATEGORIES: ProtocolCategory[] = [
  {
    key: "openai-compatible", label: "OpenAI 兼容",
    desc: "最通用的一张卡:OpenAI 官方、各类中转站(token 站 / API 超市)、本地 Ollama,以及卖 DeepSeek / Kimi / 通义 / GLM 等模型的服务——只要是 OpenAI /chat/completions 协议都选它,填对 Base URL 和模型名即可。",
    baseUrl: "https://api.openai.com/v1", model: "gpt-4o",
  },
  {
    key: "anthropic", label: "Anthropic (Claude)",
    desc: "Claude 原生 Messages API。用官方或支持 Anthropic 协议的渠道选它;卖 Claude 的 OpenAI 兼容中转站请改用「OpenAI 兼容」卡。",
    baseUrl: "https://api.anthropic.com", model: "claude-sonnet-4-20250514",
  },
  {
    key: "gemini", label: "Gemini",
    desc: "仅 Google 官方原生 API。卖 Gemini 模型的中转站请走「OpenAI 兼容」卡。",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta", model: "gemini-2.0-flash",
  },
];
export const CATEGORY_BY_KEY: Record<string, ProtocolCategory> = Object.fromEntries(
  PROTOCOL_CATEGORIES.map((c) => [c.key, c]));

// 快捷预设:点一下把「大类 + Base URL + 模型名」一并填好(纯前端便利,存库仍是大类 key)。
// 用户通常只需再填 API Key;想接别的厂商,选对应大类手填地址即可。
export interface QuickPreset { label: string; category: string; baseUrl: string; model: string; }
export const QUICK_PRESETS: QuickPreset[] = [
  { label: "DeepSeek", category: "openai-compatible", baseUrl: "https://api.deepseek.com", model: "deepseek-chat" },
  { label: "OpenAI", category: "openai-compatible", baseUrl: "https://api.openai.com/v1", model: "gpt-4o" },
  { label: "Kimi", category: "openai-compatible", baseUrl: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  { label: "通义千问", category: "openai-compatible", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  { label: "智谱 GLM", category: "openai-compatible", baseUrl: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-plus" },
  { label: "Claude", category: "anthropic", baseUrl: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" },
  { label: "Gemini", category: "gemini", baseUrl: "https://generativelanguage.googleapis.com/v1beta", model: "gemini-2.0-flash" },
];

// 徽标文案:覆盖所有可能存库的 interface_format(含历史别名),让存量配置也显示合理标签。
export const FORMAT_LABEL: Record<string, string> = {
  "openai-compatible": "OpenAI 兼容",
  anthropic: "Anthropic (Claude)",
  gemini: "Gemini",
  deepseek: "OpenAI 兼容",
  openai: "OpenAI 兼容",
};

// 历史别名归一到大类 key:存量 deepseek/openai 配置在表单里并入「OpenAI 兼容」大类。
export function normalizeCategory(fmt: string): string {
  return fmt === "deepseek" || fmt === "openai" ? "openai-compatible" : fmt;
}
