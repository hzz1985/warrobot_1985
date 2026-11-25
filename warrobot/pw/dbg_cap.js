require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
﻿const { chromium } = require("playwright");
const path = require("path");
(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(__dirname,"..","state","pw-profile"), { channel:"chrome", headless:false });
  const page = ctx.pages()[0] || await ctx.newPage();
  await page.goto("https://online.meibours.ir/", { waitUntil:"domcontentloaded", timeout:60000 });
  await page.waitForTimeout(4000);
  console.log("url:", page.url());
  const info = await page.evaluate(() => {
    const e = document.querySelector("#captcha-img");
    return e ? {src:(e.src||"").slice(0,40), len:(e.src||"").length, w:e.naturalWidth, h:e.naturalHeight, complete:e.complete} : null;
  });
  console.log("img:", JSON.stringify(info));
  await ctx.close();
})();
