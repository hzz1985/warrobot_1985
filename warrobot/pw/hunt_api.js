const { chromium } = require("playwright");
const path = require("path");
(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(__dirname,"..","state","pw-profile"), { channel:"chrome", headless:true });
  const page = ctx.pages()[0] || await ctx.newPage();
  await page.goto("https://online.meibours.ir/Home/Default2", { waitUntil:"domcontentloaded", timeout:60000 });
  await page.waitForTimeout(4000);
  const srcs = await page.evaluate(() => Array.from(document.querySelectorAll("script[src]")).map(s=>s.src));
  const hits = new Set();
  for (const u of srcs) {
    try {
      const t = await page.evaluate(async (url) => (await fetch(url)).text(), u);
      for (const m of t.matchAll(/["'`](\/Web\/V1\/[A-Za-z0-9_\/]+)["'`]/g)) hits.add(m[1]);
    } catch (e) {}
  }
  console.log(JSON.stringify(Array.from(hits).slice(0,40), null, 0));
  await ctx.close();
})();
