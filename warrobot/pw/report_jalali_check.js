const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1200 } });
  const r = await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  for (const t of await p.locator(".tab").all()) {
    if ((await t.innerText()).indexOf("گزارش") >= 0) { await t.click(); break; }
  }
  await p.waitForTimeout(700);
  const info = await p.evaluate(() => ({
    notes: Array.from(document.querySelectorAll("#report p.note")).map(n => n.innerText.replace(/\s+/g," ").trim()).slice(0,4)
  }));
  console.log(JSON.stringify({ status: r.status(), ...info }, null, 1).slice(0, 1200));
  await b.close();
})();
