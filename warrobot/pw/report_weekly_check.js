const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1300 } });
  const r = await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  for (const t of await p.locator(".tab").all()) {
    if ((await t.innerText()).indexOf("گزارش") >= 0) { await t.click(); break; }
  }
  await p.waitForTimeout(800);
  const info = await p.evaluate(() => {
    const s = document.querySelector("#report");
    return { http: 0, heads: Array.from(s.querySelectorAll("h2")).map((h) => h.innerText.trim()).slice(0,8),
             weekly: (Array.from(s.querySelectorAll("p.note")).find((n) => n.innerText.includes("گزارش هفته")) || {}).innerText };
  });
  console.log(JSON.stringify({ status: r.status(), ...info }, null, 1).slice(0, 900));
  await p.screenshot({ path: "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_weekly.png" });
  await b.close();
})();
