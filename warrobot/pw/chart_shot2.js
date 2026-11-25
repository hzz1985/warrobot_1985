const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1300 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  for (const t of await p.locator(".tab").all()) {
    if ((await t.innerText()).indexOf("فروش") >= 0) { await t.click(); break; }
  }
  await p.waitForTimeout(2000);
  const info = await p.evaluate(() => {
    const lbls = Array.from(document.querySelectorAll("svg .point-label")).map(e => ({t: e.textContent, o: getComputedStyle(e).opacity, f: getComputedStyle(e).fill}));
    return { labels: lbls.slice(0, 6), count: lbls.length };
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 800));
  await p.screenshot({ path: "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_chart2.png" });
  await b.close();
})();
