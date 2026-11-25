const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1200 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  for (const t of await p.locator(".tab").all()) {
    if ((await t.innerText()).indexOf("فروش") >= 0) { await t.click(); break; }
  }
  await p.waitForTimeout(700);
  const info = await p.evaluate(() => {
    const sec = document.querySelector("#sell");
    const tables = Array.from(sec.querySelectorAll("table")).map(tb =>
      Array.from(tb.querySelectorAll("tr")).map(r => r.innerText.replace(/\s+/g, " ").trim()));
    const svg = sec.querySelector("svg");
    const labels = svg ? Array.from(svg.querySelectorAll("text")).map(t => t.textContent.trim()).filter(Boolean).slice(0, 8) : [];
    return { tables, chartLabels: labels, hasSvg: !!svg };
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 1800));
  await p.screenshot({ path: "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_sell_fixed.png" });
  await b.close();
})();
