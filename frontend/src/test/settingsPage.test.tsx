// 设置页单测:它本身只是容器装配(七张卡片 + 页脚文档链接),
// 各卡片自己的逻辑由 card 级测试负责(如 render.test 覆盖 RenderCard)。
// 这里钉两件事:七张卡片都在;桌面/网页两态下页脚文档链接的打开方式不同。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SettingsPage from "../pages/SettingsPage";
import { api } from "../api";
import { isDesktop } from "../desktop";

// openLink 调用处会接 .catch(),mock 必须返回 Promise
vi.mock("../api", () => ({ api: { openLink: vi.fn().mockResolvedValue({ ok: true }) } }));
vi.mock("../desktop", () => ({ isDesktop: vi.fn(() => false) }));
vi.mock("../pages/settings/AboutUpdateCard", () => ({ AboutUpdateCard: () => <div>ABOUT</div> }));
vi.mock("../pages/settings/AccountCard", () => ({ AccountCard: () => <div>ACCOUNT</div> }));
vi.mock("../pages/settings/AppLockCard", () => ({ AppLockCard: () => <div>APPLOCK</div> }));
vi.mock("../pages/settings/ProvidersCard", () => ({ ProvidersCard: () => <div>PROVIDERS</div> }));
vi.mock("../pages/settings/UsageCard", () => ({ UsageCard: () => <div>USAGE</div> }));
vi.mock("../pages/settings/RenderCard", () => ({ RenderCard: () => <div>RENDER</div> }));
vi.mock("../pages/settings/PreferencesCard", () => ({ PreferencesCard: () => <div>PREFS</div> }));

function renderPage() {
  render(<MemoryRouter><SettingsPage /></MemoryRouter>);
}

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(isDesktop).mockReturnValue(false);
  });
  afterEach(() => cleanup());

  it("装配七张设置卡片与返回工作台入口", () => {
    renderPage();
    for (const marker of ["ABOUT", "ACCOUNT", "APPLOCK", "PROVIDERS", "USAGE", "RENDER", "PREFS"]) {
      expect(screen.getByText(marker)).toBeTruthy();
    }
    expect(screen.getByRole("link", { name: /返回工作台/ })).toBeTruthy();
  });

  it("网页态:文档链接新窗口打开(target=_blank),不拦点击", () => {
    renderPage();
    const docs = screen.getByRole("link", { name: /API 文档/ });
    expect(docs.getAttribute("target")).toBe("_blank");

    const ev = new MouseEvent("click", { bubbles: true, cancelable: true });
    docs.dispatchEvent(ev);
    expect(api.openLink).not.toHaveBeenCalled();
    expect(ev.defaultPrevented).toBe(false);
  });

  it("桌面态:拦下默认跳转,交后端用系统浏览器打开", () => {
    vi.mocked(isDesktop).mockReturnValue(true);
    renderPage();
    const docs = screen.getByRole("link", { name: /API 文档/ });
    expect(docs.getAttribute("target")).toBeNull();

    fireEvent.click(docs);
    expect(api.openLink).toHaveBeenCalledWith(expect.stringContaining("/docs"));
  });
});
