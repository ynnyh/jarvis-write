// 峰时计费提示逻辑:时段判断(北京时区)、官方渠道识别、会话内只弹一次。
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, ProviderConfigOut } from "../api";
import { confirmDialog } from "../ui/ConfirmDialog";
import {
  ackPeakPricing, confirmPeakPricing, isPeakNowCn, peakPricingNotice,
  resetPeakPricingCache,
} from "../peakPricing";

vi.mock("../api", () => ({ api: { listProviders: vi.fn() } }));
vi.mock("../ui/ConfirmDialog", () => ({ confirmDialog: vi.fn() }));

const mockedApi = vi.mocked(api);
const mockedConfirm = vi.mocked(confirmDialog);

function provider(over: Partial<ProviderConfigOut> = {}): ProviderConfigOut {
  return {
    id: 1, name: "官方ds", interface_format: "deepseek",
    api_key_masked: "sk-***", has_key: true,
    base_url: "https://api.deepseek.com", model: "deepseek-v4-flash",
    timeout: 0, max_tokens: 0, max_concurrency: 0, rpm: 0,
    thinking_mode: "", is_default: true, is_default_fast: false,
    is_default_review: false, default_base_url: "",
    ...over,
  } as ProviderConfigOut;
}

// 周二 2026-09-08;北京时间 = UTC+8
const peak = new Date("2026-09-08T10:00:00+08:00");
const off = new Date("2026-09-08T13:00:00+08:00");

describe("isPeakNowCn", () => {
  it("峰时窗口边界(北京时间 9-12、14-18,含头不含尾)", () => {
    expect(isPeakNowCn(new Date("2026-09-08T09:00:00+08:00"))).toBe(true);
    expect(isPeakNowCn(new Date("2026-09-08T11:59:00+08:00"))).toBe(true);
    expect(isPeakNowCn(new Date("2026-09-08T12:00:00+08:00"))).toBe(false);
    expect(isPeakNowCn(new Date("2026-09-08T14:00:00+08:00"))).toBe(true);
    expect(isPeakNowCn(new Date("2026-09-08T18:00:00+08:00"))).toBe(false);
  });
});

describe("peakPricingNotice", () => {
  beforeEach(() => {
    resetPeakPricingCache();
    mockedApi.listProviders.mockClear().mockResolvedValue([provider()]);
    mockedConfirm.mockReset();
  });

  it("低峰不提示", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(off);
    expect(await peakPricingNotice(1)).toBeNull();
    vi.useRealTimers();
  });

  it("峰时 + 官方渠道:提示含倍率与预估金额,章数线性", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(peak);
    const one = await peakPricingNotice(1);
    expect(one).toContain("×2");
    expect(one).toContain("¥0.16");
    const many = await peakPricingNotice(10);
    expect(many).toContain("¥1.60");
    vi.useRealTimers();
  });

  it("只有中转站(非官方 base_url)不提示——不按官方峰时计费", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(peak);
    mockedApi.listProviders.mockResolvedValue([
      provider({ base_url: "https://relay.example.com/v1" }),
    ]);
    expect(await peakPricingNotice(1)).toBeNull();
    vi.useRealTimers();
  });

  it("确认过一次后本会话不再提示", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(peak);
    ackPeakPricing();
    expect(await peakPricingNotice(1)).toBeNull();
    vi.useRealTimers();
  });
});

describe("confirmPeakPricing", () => {
  beforeEach(() => {
    resetPeakPricingCache();
    mockedApi.listProviders.mockClear().mockResolvedValue([provider()]);
    mockedConfirm.mockReset();
  });

  it("低峰直通,不弹窗不请求配置", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(off);
    expect(await confirmPeakPricing(1)).toBe(true);
    expect(mockedConfirm).not.toHaveBeenCalled();
    expect(mockedApi.listProviders).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("峰时弹窗:确认后放行并记住;取消后不放行", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(peak);
    mockedConfirm.mockResolvedValueOnce(true);
    expect(await confirmPeakPricing(1)).toBe(true);
    expect(mockedConfirm).toHaveBeenCalledOnce();
    // 第二次不再弹
    expect(await confirmPeakPricing(1)).toBe(true);
    expect(mockedConfirm).toHaveBeenCalledOnce();

    // 新会话(清 ack) + 取消
    sessionStorage.clear();
    mockedConfirm.mockResolvedValueOnce(false);
    expect(await confirmPeakPricing(1)).toBe(false);
    vi.useRealTimers();
  });
});
