const { chromium } = require("playwright");
(async () => {
  try {
    const ctx = await chromium.launchPersistentContext("C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\pw-profile", {
      channel: "chrome",
      headless: false,
      args: ["--no-first-run", "--no-default-browser-check"],
    });
    const page = ctx.pages()[0] || await ctx.newPage();
    await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(4000);
    console.log("URL:", page.url());
    console.log("has panel:", await page.locator("#send_order_txtCount").count());
    await ctx.close();
    console.log("OK");
  } catch (e) {
    console.log("ERR:", String(e).slice(0, 300));
  }
})();
