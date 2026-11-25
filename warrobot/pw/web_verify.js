const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1000 } });
  const errs = [];
  p.on("pageerror", (e) => errs.push(String(e).slice(0, 120)));
  const r = await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(1200);
  const info = await p.evaluate(() => {
    const kpis = Array.from(document.querySelectorAll(".kpi")).map((k) => k.innerText.replace(/\s+/g, " ").trim());
    const heads = Array.from(document.querySelectorAll(".panel.active table tr.head th")).map((t) => t.innerText.trim());
    const firstRow = Array.from(document.querySelectorAll(".panel.active table tr")).slice(1, 2).map((r) => r.innerText.replace(/\s+/g, " ").trim());
    return { kpis, heads, firstRow };
  });
  console.log(JSON.stringify({ status: r.status(), errs, ...info }, null, 1).slice(0, 1400));
  await p.screenshot({ path: "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_balance.png" });
  await b.close();
})();
