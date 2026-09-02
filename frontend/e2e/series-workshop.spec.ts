// e2e 冒烟:不依赖 LLM key 的主路径——登录、系列工坊建主角/建集/删集、
// 档案编辑,以及 390px 窄屏同款流程(引擎卡/概念页两次翻车都是窄屏暴露的)。
// 生成提示词链路要真 LLM,暂不在冒烟内(后续可加 mock provider)。
import { expect, test } from "@playwright/test";

test.describe("系列短片工坊·主路径", () => {
  // 应用是 HashRouter:导航一律用 /#/xxx 直接落页,桌面/移动两套壳通用
  test("建主角 → 建一集 → 编辑档案 → 删一集", async ({ page }) => {
    await page.goto("/app/#/series");

    await expect(page.getByRole("heading", { name: "系列短片" })).toBeVisible();

    // 新建主角:手写定妆(冒烟不起 LLM,不走「AI 代写」)
    await page.getByLabel("主角名字").fill("冒烟浣熊");
    await page.getByLabel("一句话概念").fill("一只戴红围巾、爱囤零食的小浣熊");
    await page.getByLabel("定妆描述").fill("一只成年小浣熊,体态圆润敦实,戴一条洗旧的红围巾,眼圈和耳尖颜色偏深,e2e 冒烟专用形象。");
    await page.getByRole("button", { name: "建主角" }).click();

    // 建完跳工作台,主角名出现在页头
    await expect(page.getByRole("heading", { name: /系列短片 · 冒烟浣熊/ })).toBeVisible({
      timeout: 15_000,
    });

    // 写一集剧情 → 建集 → 卡片出现(状态:待生成)
    await page.getByLabel("剧情").fill("冒烟浣熊盯上了货架最上层的蜂蜜罐,踮脚、晃罐,最后一屁股坐在地上稳稳接住。");
    await page.getByRole("button", { name: "建一集" }).click();
    const epCard = page.locator("section.card").filter({ hasText: "第 1 集" });
    await expect(epCard).toBeVisible();
    await expect(epCard.getByText("待生成")).toBeVisible();

    // 编辑档案:改动定妆并保存(影响之后每一集的核心资产)
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    const lookBox = page.getByLabel("定妆描述");
    await expect(lookBox).toBeVisible();
    await lookBox.fill("一只成年小浣熊,体态圆润敦实,戴一条崭新的红围巾,e2e 冒烟改档后的形象。");
    await page.getByRole("button", { name: "保存档案" }).click();
    await expect(page.getByText("档案已保存", { exact: true })).toBeVisible();
    await expect(page.getByText("e2e 冒烟改档后的形象")).toBeVisible();

    // 删这一集(弹确认框)→ 回到空态
    await epCard.getByRole("button", { name: "删除", exact: true }).click();
    await page.getByRole("button", { name: "确认删除" }).click();
    await expect(page.getByText("还没有剧集")).toBeVisible();
  });
});

test.describe("系列短片工坊·窄屏 390px", () => {
  test("建主角全流程在窄屏不破版", async ({ page }) => {
    await page.goto("/app/#/series");

    await expect(page.getByRole("heading", { name: "系列短片" })).toBeVisible();
    // 窄屏新建表单可见可用(引擎卡教训:全局按钮 nowrap/固定高在窄屏画出版)
    await page.getByLabel("主角名字").fill("窄屏虎子");
    await page.getByLabel("定妆描述").fill("一只短毛小虎崽,圆脸,左耳有一块月牙白斑,e2e 窄屏冒烟专用。");
    await page.getByRole("button", { name: "建主角" }).click();

    await expect(page.getByRole("heading", { name: /系列短片 · 窄屏虎子/ })).toBeVisible({
      timeout: 15_000,
    });
    // 横向不得溢出:文档宽度不能超过视口(破版的最快判据)
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
