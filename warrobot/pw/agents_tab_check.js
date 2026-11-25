const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1200 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  for (const t of await p.locator(".tab").all()) {
    if ((await t.innerText()).indexOf("ایجنت") >= 0) { await t.click(); break; }
  }
  await p.waitForTimeout(1000);
  const info = await p.evaluate(() => {
    const s = document.querySelector("#agents");
    return { active: s.classList.contains("active"),
             heads: Array.from(s.querySelectorAll("h2")).map((h) => h.innerText.trim()),
             kpi: Array.from(s.querySelectorAll(".kpi")).map((k) => k.innerText.replace(/\s+/g," ").trim()),
             rows: s.querySelectorAll("table tr").length };
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 1200));
  await p.screenshot({ path: "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_agents.png" });
  await b.close();
})();
