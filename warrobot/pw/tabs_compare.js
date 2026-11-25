const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1500, height: 1400 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(800);
  const grab = async (tabText, sel) => {
    for (const t of await p.locator(".tab").all()) {
      if ((await t.innerText()).includes(tabText)) { await t.click(); break; }
    }
    await p.waitForTimeout(600);
    return await p.evaluate((s) => {
      const sec = document.querySelector(s);
      if (!sec) return [];
      return Array.from(sec.querySelectorAll("table tr")).map((tr) =>
        Array.from(tr.querySelectorAll("th,td")).map((c) => c.innerText.replace(/\s+/g, " ").trim()));
    }, sel);
  };
  const buy = await grab("خرید", "#buy");
  const sell = await grab("فروش", "#sell");
  const agents = await grab("ایجنت", "#agents");
  const pick = (rows) => rows.filter((r) => r.some((c) => /آباد|افران/.test(c))).slice(0, 6);
  console.log(JSON.stringify({ buy: pick(buy), sell: pick(sell), agents: pick(agents) }, null, 1).slice(0, 3000));
  await p.screenshot({ path: "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\tabs_compare.png", fullPage: true });
  await b.close();
})().catch((e) => { console.log("ERR", String(e).slice(0, 200)); process.exit(1); });
