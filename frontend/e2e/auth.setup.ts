// 登录一次拿 storageState,后续 desktop/mobile 项目直接复用登录态。
// 走真实登录页(用户名+密码+提交),顺带把登录主路径本身纳入冒烟。
import { expect, test as setup } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

const STATE_FILE = "e2e/.auth/state.json";
const USERNAME = "e2e_writer";
const PASSWORD = "e2e-passw0rd";

setup("登录并保存会话", async ({ page }) => {
  mkdirSync(dirname(STATE_FILE), { recursive: true });
  await page.goto("/app/");
  // 未登录会被 App 壳路由到登录页(带品牌字样兜底断言,防白屏假绿)
  await expect(page.getByText("jarvis")).toBeVisible();
  await page.getByPlaceholder("2-50 个字符").fill(USERNAME);
  await page.locator('input[type="password"]').fill(PASSWORD);
  // 「登录」tab 和提交按钮同名,scope 到表单里那颗
  await page.locator("form").getByRole("button", { name: "登录", exact: true }).click();
  // 登录成功的标志:App 壳渲染,侧栏一级入口出现
  await expect(page.getByRole("link", { name: /系列短片/ })).toBeVisible({
    timeout: 15_000,
  });
  await page.context().storageState({ path: STATE_FILE });
});
