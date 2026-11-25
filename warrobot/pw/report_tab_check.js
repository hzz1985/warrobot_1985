const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1300 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  for (const t of await p.locator(".tab").all()) {
    if ((await t.innerText()).indexOf("گزارش") >= 0) { await t.click(); break; }
  }
  await p.waitForTimeout(1200);
  const info = await p.evaluate(() => {
    const sec = document.querySelector("#report");
    const notes = Array.from(sec.querySelectorAll("p.note")).map((n) => n.innerText.replace(/\s+/g, " ").trim());
    return { active: sec.classList.contains("active"), heads: Array.from(sec.querySelectorAll("h2")).map((h) => h.innerText.trim()), notes: notes.slice(0, 8), empties: Array.from(sec.querySelectorAll(".empty")).map((e) => e.innerText.trim()) };
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 1600));
  await p.screenshot({ path: "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_report.png" });
  await b.close();
})();
