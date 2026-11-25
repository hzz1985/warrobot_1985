const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1000 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(1000);
  const row = p.locator("table tr").nth(1);
  await row.hover();
  await p.waitForTimeout(300);
  const after = await p.evaluate(() => {
    const tr = document.querySelectorAll("table tr")[1];
    const td = tr.querySelector("td");
    const cs = getComputedStyle(td);
    const kpi = document.querySelector(".kpi");
    return { tdBg: cs.backgroundColor, tdColor: cs.color,
             kpiVal: kpi ? getComputedStyle(kpi.querySelector(".val")).color : null };
  });
  // hover a tab and a kpi too
  await p.locator(".tab").first().hover();
  await p.waitForTimeout(200);
  const tab = await p.evaluate(() => {
    const t = document.querySelector(".tab");
    const cs = getComputedStyle(t);
    return { tabBg: cs.backgroundColor, tabColor: cs.color };
  });
  console.log(JSON.stringify({ ...after, ...tab }, null, 1));
  await b.close();
})();
