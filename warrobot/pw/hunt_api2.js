const { chromium } = require("playwright");
const path = require("path");
(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(__dirname,"..","state","pw-profile"), { channel:"chrome", headless:true });
  const page = ctx.pages()[0] || await ctx.newPage();
  const urls = new Set();
  page.on("request", r => urls.add(r.url().split("?")[0]));
  await page.goto("https://online.meibours.ir/", { waitUntil:"domcontentloaded", timeout:60000 });
  await page.waitForTimeout(3000);
  async function loginFormVisible() {
    return await page.evaluate(() => {
      if (!/login/i.test(location.href)) return false;
      const u = document.querySelector("#username");
      if (!u) return false;
      const r = u.getBoundingClientRect();
      const st = window.getComputedStyle(u);
      return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
    });
  }
  if (await loginFormVisible()) { console.log("login_required"); await ctx.close(); return; }
  await page.goto("https://online.meibours.ir/Home/Default2", { waitUntil:"domcontentloaded", timeout:60000 });
  await page.waitForTimeout(5000);
  const api = Array.from(urls).filter(u => u.includes("api.meibours"));
  console.log(JSON.stringify(api.slice(0,25)));
  // also search all script texts for 'Order' endpoints
  const srcs = await page.evaluate(() => Array.from(document.querySelectorAll("script[src]")).map(s=>s.src));
  const found = new Set();
  for (const u of srcs) {
    try {
      const t = await page.evaluate(async (url) => (await fetch(url)).text(), u);
      for (const m of t.matchAll(/(\/Web\/V1\/[A-Za-z0-9_\/]*Order[A-Za-z0-9_\/]*)/g)) found.add(m[1]);
    } catch (e) {}
  }
  console.log("order-endpoints:", JSON.stringify(Array.from(found).slice(0,25)));
  await ctx.close();
})();
