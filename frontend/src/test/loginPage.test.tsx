// 登录/注册页单测:登录成功写 token 并回调 onAuthed(me);失败就地提示且不回调;
// 邀请码只在注册模式出现;提交走 trim 后的用户名;busy 时提交按钮禁用防重复提交。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import LoginPage from "../pages/LoginPage";
import { api, token } from "../api";

vi.mock("../api", () => ({
  api: { login: vi.fn(), register: vi.fn(), me: vi.fn() },
  token: { set: vi.fn(), get: vi.fn(() => ""), clear: vi.fn() },
}));

// 登录模式下「登录」既是切换 tab 又是提交按钮文案,用 id 精确定位提交键
function renderPage() {
  const onAuthed = vi.fn();
  render(
    <MemoryRouter>
      <LoginPage onAuthed={onAuthed} />
    </MemoryRouter>,
  );
  return onAuthed;
}

function submitBtn(): HTMLElement {
  const form = document.querySelector("form") as HTMLFormElement;
  return within(form).getByRole("button");
}

function fill(u: string, p: string) {
  fireEvent.change(screen.getByPlaceholderText("2-50 个字符"), { target: { value: u } });
  fireEvent.change(screen.getByPlaceholderText("至少 6 位"), { target: { value: p } });
}

describe("LoginPage", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("默认登录模式:不显示邀请码;切到注册后出现邀请码输入", () => {
    renderPage();
    expect(screen.queryByPlaceholderText("进 QQ 群免费领取")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    expect(screen.getByPlaceholderText("进 QQ 群免费领取")).toBeTruthy();
  });

  it("登录成功:用户名按 trim 提交,写 token 并回调 onAuthed(me)", async () => {
    vi.mocked(api.login).mockResolvedValue({ token: "tk-1", username: "alice", is_admin: false });
    vi.mocked(api.me).mockResolvedValue({ id: 1, username: "alice", is_admin: false });
    const onAuthed = renderPage();

    fill("  alice  ", "secret1");
    fireEvent.click(submitBtn());

    await waitFor(() => expect(onAuthed).toHaveBeenCalledTimes(1));
    expect(api.login).toHaveBeenCalledWith("alice", "secret1");
    expect(api.register).not.toHaveBeenCalled();
    expect(token.set).toHaveBeenCalledWith("tk-1");
    expect(onAuthed).toHaveBeenCalledWith({ id: 1, username: "alice", is_admin: false });
  });

  it("登录失败:显示后端错误,不写 token 也不回调", async () => {
    vi.mocked(api.login).mockRejectedValue(new Error("用户名或密码错误"));
    const onAuthed = renderPage();

    fill("alice", "wrongpass");
    fireEvent.click(submitBtn());

    expect(await screen.findByText("用户名或密码错误")).toBeTruthy();
    expect(onAuthed).not.toHaveBeenCalled();
    expect(token.set).not.toHaveBeenCalled();
  });

  it("注册模式:带邀请码走 register(二者都 trim)", async () => {
    vi.mocked(api.register).mockResolvedValue({ token: "tk-2", username: "bob", is_admin: false });
    vi.mocked(api.me).mockResolvedValue({ id: 2, username: "bob", is_admin: false });
    const onAuthed = renderPage();

    fireEvent.click(screen.getByRole("button", { name: "注册" }));
    fill("bob", "secret2");
    fireEvent.change(screen.getByPlaceholderText("进 QQ 群免费领取"), { target: { value: "  INV-9  " } });
    fireEvent.click(submitBtn());

    await waitFor(() => expect(onAuthed).toHaveBeenCalledTimes(1));
    expect(api.register).toHaveBeenCalledWith("bob", "secret2", "INV-9");
    expect(api.login).not.toHaveBeenCalled();
    expect(token.set).toHaveBeenCalledWith("tk-2");
  });

  it("提交中按钮禁用,防重复提交", async () => {
    // 挂起不 resolve,停在 busy 态
    vi.mocked(api.login).mockReturnValue(new Promise(() => {}));
    renderPage();

    fill("alice", "secret1");
    fireEvent.click(submitBtn());

    await waitFor(() => expect(submitBtn()).toBeDisabled());
    expect(api.login).toHaveBeenCalledTimes(1);
  });
});
