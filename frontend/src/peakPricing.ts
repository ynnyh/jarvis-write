// 峰时计费提示:仅官方 DeepSeek 有峰谷定价(9:00-12:00、14:00-18:00 计费 ×2),
// 中转站/其他渠道不按官方时段计费,不提示(弹了反而误导)。
// 防弹窗疲劳:确认一次后本会话(sessionStorage)不再重复弹,关页重置。
import { api, ProviderConfigOut } from "./api";
import { confirmDialog } from "./ui/ConfirmDialog";

const PEAK_WINDOWS: Array<[number, number]> = [[9, 12], [14, 18]];
const ACK_KEY = "peak-pricing-ack";
// 单章参考价(2026-09 官方 v4-flash 实测:约 6.7 万 token/章,峰时全项 ×2)
const PER_CHAPTER_PEAK = 0.16;

/** 当前(北京时间)是否官方峰时定价窗口 */
export function isPeakNowCn(now = new Date()): boolean {
  const h = Number(
    new Intl.DateTimeFormat("en-CN", {
      hour: "2-digit", hour12: false, timeZone: "Asia/Shanghai",
    }).format(now),
  );
  return PEAK_WINDOWS.some(([lo, hi]) => h >= lo && h < hi);
}

export function peakEstimatedCost(chapters: number): number {
  return Math.round(PER_CHAPTER_PEAK * chapters * 100) / 100;
}

let providerCache: { at: number; official: boolean } | null = null;

function isOfficialDeepseek(p: ProviderConfigOut): boolean {
  return p.interface_format === "deepseek" && /api\.deepseek\.com/i.test(p.base_url || "");
}

async function hasOfficialDeepseek(): Promise<boolean> {
  if (providerCache && Date.now() - providerCache.at < 5 * 60_000) return providerCache.official;
  try {
    const list = await api.listProviders();
    const official = list.some(isOfficialDeepseek);
    providerCache = { at: Date.now(), official };
    return official;
  } catch { return false; } // 配置拉不到就不打扰,不阻塞生成
}

/** 峰时提示文案;null = 无需提示(非峰时 / 本会话已确认 / 非官方渠道) */
export async function peakPricingNotice(chapters: number): Promise<string | null> {
  if (!isPeakNowCn()) return null;
  if (sessionStorage.getItem(ACK_KEY)) return null;
  if (!(await hasOfficialDeepseek())) return null;
  const cost = peakEstimatedCost(chapters).toFixed(2);
  const off = (peakEstimatedCost(chapters) / 2).toFixed(2);
  return (
    `当前处于 DeepSeek 官方峰时定价(9:00-12:00、14:00-18:00),计费 ×2。` +
    `本次预计约 ¥${cost}(低峰约 ¥${off})。`
  );
}

/**
 * 生成前确认:峰时 + 官方渠道 + 本会话未确认过时弹一次。
 * 返回 false = 用户选择取消(去低峰再跑);确认后本会话不再弹。
 */
export async function confirmPeakPricing(chapters: number): Promise<boolean> {
  const notice = await peakPricingNotice(chapters);
  if (!notice) return true;
  const ok = await confirmDialog({
    title: "峰时计费提示",
    body: `${notice}\n\n现在继续,还是等低峰(12:00-14:00 / 18:00 后)再跑?选择继续后本次会话不再重复提示。`,
    confirmText: "继续生成",
  });
  if (ok) ackPeakPricing();
  return ok;
}

/** 重置渠道缓存与确认记录(测试用;生产代码不调) */
export function resetPeakPricingCache(): void {
  providerCache = null;
  sessionStorage.removeItem(ACK_KEY);
}

/** 用户已在别的弹窗里看到峰时成本信息并确认(如连写确认框合并了峰时文案) */
export function ackPeakPricing(): void {
  sessionStorage.setItem(ACK_KEY, String(Date.now()));
}
