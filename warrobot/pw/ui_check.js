const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1200 } });
  const r = await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(600);
  const buy = await p.evaluate(() => Array.from(document.querySelectorAll(".kpi")).map(k => k.innerText.replace(/\s+/g," ").trim()).slice(0,4));
  for (const t of await p.locator(".tab").all()) {
    if ((await t.innerText()).indexOf("فروش") >= 0) { await t.click(); break; }
  }
  await p.waitForTimeout(700);
  const sell = await p.evaluate(() => ({
    notes: Array.from(document.querySelectorAll("#sell p.note")).map(n => n.innerText.replace(/\s+/g," ").trim()).filter(x => x.includes("بروزرسانی")),
    kpi: Array.from(document.querySelectorAll("#sell .kpi")).map(k => k.innerText.replace(/\s+/g," ").trim()),
    rows: Array.from(document.querySelectorAll("#sell table tr")).slice(0,3).map(t => t.innerText.replace(/\s+/g," ").trim())
  }));
  console.log(JSON.stringify({ status: r.status(), buy, sell }, null, 1).slice(0, 1400));
  await p.screenshot({ path: "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_final_ui.png" });
  await b.close();
})();
